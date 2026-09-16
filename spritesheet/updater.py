"""启动时自动检查更新 + 自动下载更新。

服务器端零配置：把安装包（SpriteSheetTool_v.X.Y.Z.exe）直接上传到
http://duadu.cc/download/SpriteSheetTool/ 目录即可，Web 服务器会自动生成目录索引。
客户端请求该目录索引，从文件名中解析出版本号最高的安装包，
与本地版本比较，有新版本时通知 UI 引导下载并自动安装。
"""
from __future__ import annotations

import os
import re
import urllib.request
import urllib.error
import urllib.parse

from PyQt6.QtCore import QThread, pyqtSignal

# 更新检查目录（服务器自动列出安装包文件）
UPDATE_URL = "http://duadu.cc/download/SpriteSheetTool/.index.php"
CHECK_TIMEOUT = 8  # 秒；网络异常/超时都静默跳过，不打扰用户

# 安装包文件名模式：SpriteSheetTool_v.1.2.3.exe
_INSTALLER_RE = re.compile(r"SpriteSheetTool_v\.(\d+(?:\.\d+)*)\.exe", re.IGNORECASE)


def _parse_version(text: str) -> tuple[int, ...]:
    """把 '1.2.3' 解析成 (1,2,3) 用于比较。容忍非数字段。"""
    parts = []
    for seg in str(text).strip().split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _pick_latest(html: str, base_url: str) -> dict | None:
    """从目录索引 HTML 中找出版本号最高的安装包。

    返回 {version, url}；目录里没有安装包时返回 None。
    """
    best = None  # (version_tuple, version_str, url)
    for match in _INSTALLER_RE.finditer(html):
        version_str = match.group(1)
        ver = _parse_version(version_str)
        href = match.group(0)
        url = urllib.parse.urljoin(base_url, href)
        if best is None or ver > best[0]:
            best = (ver, version_str, url)
    if best is None:
        return None
    return {"version": best[1], "url": best[2]}


class UpdateCheckThread(QThread):
    """后台线程检查更新；不阻塞 UI。完成时发出 result_ready 信号。

    信号参数 dict，status 取值：
      - "update": 发现新版本（含 version/url）
      - "latest": 已是最新（含 version=服务器最高版本）
      - "no_installer": 目录里没有安装包（含 url）
      - "error": 网络/解析失败（含 reason）
    """

    result_ready = pyqtSignal(object)  # dict

    def __init__(self, current_version: str, url: str = UPDATE_URL, parent=None):
        super().__init__(parent)
        self._current = current_version
        self._url = url

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": "SpriteSheetTool-Updater/1.0", "Accept": "text/html"},
            )
            with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                final_url = resp.geturl()  # 跟随重定向后的真实地址（http->https 等）
            latest = _pick_latest(html, final_url)
            if latest is None:
                self.result_ready.emit({"status": "no_installer", "url": final_url})
                return
            if _parse_version(latest["version"]) > _parse_version(self._current):
                latest["status"] = "update"
                self.result_ready.emit(latest)
            else:
                latest["status"] = "latest"
                self.result_ready.emit(latest)
        except Exception as exc:  # noqa: BLE001 - 网络异常统一静默转 error 状态
            self.result_ready.emit({"status": "error", "reason": f"{type(exc).__name__}: {exc}"})


class UpdateDownloadThread(QThread):
    """后台下载安装包，带进度报告。"""

    # 字节数必须用 qint64：PyQt 的 int 是 C++ 32 位，安装包超过 2 GiB 时
    # （例如 3.42GB 的 Content-Length）会溢出成负数，接收端 `total > 0`
    # 判false，进度条就永远停在 0 不动（KeyFrame 项目踩过这个坑）。
    progress = pyqtSignal("qint64", "qint64")  # done_bytes, total_bytes
    download_finished = pyqtSignal(str)   # 本地文件路径
    download_failed = pyqtSignal(str)     # 错误信息

    def __init__(self, url: str, target_dir: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._target_dir = target_dir
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            os.makedirs(self._target_dir, exist_ok=True)
            filename = urllib.parse.unquote(self._url.rstrip("/").split("/")[-1]) or "update.exe"
            target = os.path.join(self._target_dir, filename)
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": "SpriteSheetTool-Updater/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(target, "wb") as fh:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        if self._cancel:
                            fh.close()
                            try:
                                os.remove(target)
                            except OSError:
                                pass
                            return
                        fh.write(chunk)
                        done += len(chunk)
                        self.progress.emit(done, total)
            if os.path.getsize(target) == 0:
                raise OSError("下载文件为空")
            self.download_finished.emit(target)
        except Exception as exc:  # noqa: BLE001
            self.download_failed.emit(f"{type(exc).__name__}: {exc}")
