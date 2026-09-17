"""单实例回归：第二个进程必须把文件转给已在跑的实例，自己不开窗口。

三层验证，从轻到重：
  1. 协议层：起一个主实例，用客户端 socket 发一个路径，断言信号收到同样的内容；
  2. 探测层：主实例在跑时 ``notify_existing`` 返回 True；关掉之后返回 False；
  3. 端到端：**真的再起一个 python 进程**跑 main.py 并带上一个 .sstool 路径，
     断言它很快退出、退出码 0，而且主实例收到了那个路径。
     （第 3 条才是"双击文件不再开第二个窗口"的真正证据。）

用法: .venv\\Scripts\\python.exe tools\\verify_single_instance.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def pump(app, ms: int = 250) -> None:
    """跑一会儿事件循环（QLocalSocket 是异步的）。"""
    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def pump_until(app, done, timeout_s: float = 30.0) -> None:
    """边跑事件循环边等 done() 为真。

    ⚠️ 这条很关键：主实例的 QLocalServer 只有在**事件循环转起来**时才会 accept。
    如果测试里用阻塞调用（subprocess.run / 同线程里 waitForReadyRead）等对方，
    主实例的循环就停了，连接一直躺在 backlog 里 —— 实测会得到"收到了空消息"
    这种假失败。真实场景里主实例的循环一直在跑，所以测试也必须保持它转。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline and not done():
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication(sys.argv)
    from spritesheet.single_instance import SingleInstance

    key = f"SpriteSheetTool.test.{os.getpid()}"
    received: list[str] = []

    guard = SingleInstance(key)
    guard.opened.connect(received.append)
    check("主实例能监听", guard.listen() is True)

    print()
    print("=== 1. 协议层：转一个文件路径（客户端放子线程，主线程保持事件循环）===")
    payload = str(ROOT / "样例.sstool")
    result: list[bool] = []

    def send() -> None:
        result.append(SingleInstance(key).notify_existing(payload))

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    pump_until(app, lambda: bool(received) and bool(result), timeout_s=10)
    thread.join(timeout=5)
    check("从实例报告已转交", result == [True], f"实际 {result}")
    check("主实例收到了同样的路径", received == [payload], f"实际 {received}")

    print()
    print("=== 2. 探测层：有没有实例在跑 ===")
    check("主实例在跑时探测返回 True", SingleInstance(key).notify_existing("") is True)
    guard.close()
    pump(app, 200)
    check("主实例退出后探测返回 False（并且能重新监听）",
          SingleInstance(key).notify_existing("") is False)

    # 收尾：把测试用的服务名清掉
    from PyQt6.QtNetwork import QLocalServer
    QLocalServer.removeServer(key)

    print()
    print("=== 3. 端到端：真起一个进程跑 main.py（Popen + 保持事件循环）===")
    main_guard = SingleInstance()          # 用正式的服务名
    main_received: list[str] = []
    main_guard.opened.connect(main_received.append)
    if not main_guard.listen():
        check("端到端用的主实例能监听", False)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="sstool_single_"))
        target = tmp / "被打开.sstool"
        # 随便写个文件即可 —— 第二实例只负责转发路径，不读内容
        target.write_bytes(b"x")

        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONIOENCODING"] = "utf-8"
        t0 = time.time()
        proc = subprocess.Popen([sys.executable, str(ROOT / "main.py"), str(target)],
                                cwd=str(ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pump_until(app, lambda: proc.poll() is not None or bool(main_received),
                   timeout_s=60)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        elapsed = time.time() - t0
        print(f"  第二个进程用时 {elapsed:.2f}s，退出码 {proc.returncode}")
        check("第二个进程正常退出（没有开新窗口一直跑）", proc.returncode == 0,
              f"退出码 {proc.returncode}，stderr={err.decode('utf-8', 'replace')[:300]}")
        check("第二个进程很快就退了（< 20s，说明没进事件循环）", elapsed < 20,
              f"实际 {elapsed:.2f}s")
        check("主实例收到了这个路径", main_received == [str(target)],
              f"实际 {main_received}"
              + ("" if main_received else
                 "  ← 子进程转发成功但本实例没收到：多半是**另有一个实例**"
                 "（比如先前测试启的打包版）占着同一个服务名，把请求接走了。"
                 "用 Get-Process 查一下有没有残留的 SpriteSheetTool 进程。"))
        stdout = out.decode("utf-8", "replace")
        check("第二个进程明确说明已转交", "转交" in stdout or "已在运行" in stdout,
              f"stdout={stdout[:200]!r}")
        main_guard.close()

    print()
    print(f"SINGLE INSTANCE FAILED {len(FAILURES)}" if FAILURES else "SINGLE INSTANCE OK")
    if FAILURES:
        print("失败项：", *FAILURES, sep="\n  - ")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
