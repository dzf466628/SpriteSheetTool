"""使用者统计上报（静默、绝不拖慢启动、绝不打扰用户）。

用户口径：「你不获取敏感信息，只是统计计数」「不做开关」「要脱敏」「有问题静默」。

设计要点（为什么这么写）
------------------------
* **零 Qt 依赖 + 守护线程**：上报走 `threading` + `urllib`，不挂 QTimer、不碰窗口 ——
  于是它不会拖慢启动（不阻塞事件循环）、也不会在关机/退出顺序里被拆掉（这是本工具
  踩过的"带定时器被回收"那一族坑）。线程是 daemon，进程退出时不拦路。
* **脱敏到"能统计、认不出人"**：
  - 计算机名 → 只发前 5 个字符 + `***`；
  - 另给一个**稳定哈希**做机器身份（多台机器能区分、能算独立用户数，但不可直接读出名字）；
  - IP → 只留网段（IPv4 前三段），客户端自报的和服务端侧记的都脱敏。
* **只报计数**：功能名 + 次数，没有文件名、没有路径、没有图片内容、没有工程内容。
* **一切异常吞掉**：网络不通、证书失败、DNS 挂掉、服务器 500 —— 全部静默，
  用户侧永远看不到任何提示，也不写日志刷屏。上报是"锦上添花"，绝不能影响正事。

用法
----
    from spritesheet import telemetry
    telemetry.install()                 # 入口处调一次：发 start + 起心跳（5 分钟）
    telemetry.note("decimate_dedupe")   # 功能点：纯计数，怎么调都不会出错
    telemetry.note("import_files", 3)
退出时自动发 exit（带本次运行的聚合计数）；被强杀时丢掉 exit，但心跳里已经带过计数了。
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import socket
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from spritesheet import __version__

# 正式地址：面板是按「站点目录」配反代的（/data/ss_stats/ → 127.0.0.1:3008），
# 所以上报也走同一条前缀（服务端对"带前缀/不带前缀"两种都认）。
# 用 **8443** 而不是裸域名：实测 https://dudua.synology.me/data/api/health 从外网超时
# （80/443 在外网可能被封），而 8443 这条通路稳定可用、证书对 dudua.synology.me 有效。
REPORT_URL = os.environ.get("SSTOOL_STATS_URL",
                            "https://dudua.synology.me:8443/data/ss_stats/api/report")
TIMEOUT = 4.0                     # 短超时：慢就等于没有，绝不拖住后台线程
HEARTBEAT_SEC = 300               # 5 分钟一次心跳；服务端 15 分钟内有心跳算在线
_SALT = "sstool-2024"             # 固定盐：让哈希稳定（跨重装/跨机器一致）又不直接暴露名字

_counts: dict[str, int] = {}
_ident: dict | None = None
_installed = False
_t0 = time.time()
_lock = threading.Lock()


def _ident_path() -> Path:
    """实例身份存哪：优先用系统给的应用数据目录，退化到用户目录。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "SpriteSheetTool" / "stats_id.json"


def _local_ip() -> str:
    """本机局域网 IP（不真发包，只是让系统挑一张网卡）；失败返回空串。"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:  # noqa: BLE001
            pass


def _mask_ip(ip: str) -> str:
    """脱敏：IPv4 只留前三段。"""
    try:
        parts = str(ip or "").split(".")
        return ".".join(parts[:3]) + ".*" if len(parts) == 4 else ""
    except Exception:  # noqa: BLE001
        return ""


def _mask_host(name: str) -> str:
    """脱敏：计算机名只留前 5 个字符 + ***（够你人工认一批，认不出具体是谁）。"""
    s = str(name or "").strip()
    if not s:
        return ""
    return (s[:5] if len(s) > 5 else s) + "***"


def _hash_host(name: str) -> str:
    """机器身份：稳定哈希（同机器恒定），用于算"多少台机器在跑"。"""
    try:
        return hashlib.sha256((_SALT + str(name)).encode("utf-8")).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return ""


def _load_ident() -> dict:
    """读/建实例身份。任何失败都退化成一个临时身份，绝不抛异常。"""
    global _ident
    if _ident is not None:
        return _ident
    host = ""
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001
        host = ""
    ident = {"id": "", "h": _hash_host(host), "hm": _mask_host(host),
             "os": "", "ip": _mask_ip(_local_ip())}
    try:
        ident["os"] = platform.platform()[:40]
    except Exception:  # noqa: BLE001
        ident["os"] = ""
    try:
        path = _ident_path()
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            ident["id"] = str(saved.get("id") or "")
        if not ident["id"]:
            ident["id"] = uuid.uuid4().hex
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"id": ident["id"]}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        if not ident["id"]:
            ident["id"] = uuid.uuid4().hex       # 存不下就用临时的
    _ident = ident
    return ident


def note(key: str, n: int = 1) -> None:
    """记一次功能使用。**永远不抛异常**（上报绝不能影响正事）。"""
    try:
        k = str(key or "")[:60]
        if not k:
            return
        with _lock:
            _counts[k] = _counts.get(k, 0) + max(1, int(n))
    except Exception:  # noqa: BLE001
        pass


def _payload(event: str) -> bytes:
    ident = _load_ident()
    with _lock:
        feats = dict(_counts)
    body = {"t": event, "v": __version__, "id": ident.get("id", ""),
            "h": ident.get("h", ""), "hm": ident.get("hm", ""),
            "os": ident.get("os", ""), "ip": ident.get("ip", ""),
            "up": int(time.time() - _t0), "feat": feats}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _send(event: str):
    """后台发一包，**全静默**：任何失败都当没发生。返回线程对象（退出时可等它一下）。"""
    def run():
        try:
            req = urllib.request.Request(
                REPORT_URL, data=_payload(event),
                headers={"Content-Type": "application/json; charset=utf-8"})
            urllib.request.urlopen(req, timeout=TIMEOUT).close()
        except Exception:  # noqa: BLE001
            pass
    try:
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th
    except Exception:  # noqa: BLE001
        return None


def _send_exit(wait: float = 1.5) -> None:
    """退出上报：**这一包必须等它发出去**，否则"本次运行的功能计数"就白统计了。

    实测抓到的坑：`exit` 也走守护线程时，进程一退线程就被掐掉 —— 服务端只收到
    `start`、功能计数永远是空的。所以这里等一小会儿（最多 `wait` 秒）；
    网络慢到超时就丢掉，绝不拖住关窗（"有问题静默"）。
    """
    th = _send("exit")
    try:
        if th is not None:
            th.join(wait)
    except Exception:  # noqa: BLE001
        pass


def _heartbeat_loop() -> None:
    while True:
        try:
            time.sleep(HEARTBEAT_SEC)
            _send("hb")
        except Exception:  # noqa: BLE001
            pass


def install() -> None:
    """入口处调一次：发 start、起心跳线程、注册退出上报。重复调用无副作用。"""
    global _installed
    try:
        if _installed:
            return
        _installed = True
        _load_ident()                       # 提前建身份（也顺手验一次，失败也不抛）
        _send("start")
        threading.Thread(target=_heartbeat_loop, daemon=True).start()
        atexit.register(_send_exit)
    except Exception:  # noqa: BLE001
        pass


def counts() -> dict:
    """给探针/调试看当前计数（正式流程用不上）。"""
    with _lock:
        return dict(_counts)