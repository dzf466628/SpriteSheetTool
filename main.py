"""SpriteSheetTool 启动入口：python main.py

单实例：整个系统只跑一个进程。第二次启动（包括双击 .sstool 文件关联打开）会把
"要打开的文件"转给已经在跑的实例，由它开一个新标签页，然后自己直接退出。
见 ``spritesheet/single_instance.py``。
"""
import os
import sys
import faulthandler
import traceback

from PyQt6.QtGui import QFont, QIcon, QImageReader
from PyQt6.QtWidgets import QApplication

from spritesheet.single_instance import SingleInstance
from spritesheet.ui.main_window import MainWindow
from spritesheet.ui.theme import APP_QSS, install_dark_titlebars


def _install_crash_logging() -> None:
    """把未捕获异常和原生致命错误（access violation 等）写入程序目录 crash.log。

    PyQt 事件回调里未捕获的 Python 异常、以及 Qt C++ 层 fatal 在 Windows 上常常
    直接表现为"右键/拖动一下程序就没了"且控制台没有任何信息。faulthandler 在
    Windows 注册向量化异常处理，能在崩溃瞬间 dump 出 Python 栈；sys.excepthook
    记录普通异常。下次闪退即可凭 crash.log 定位，而不是靠猜。
    """
    import datetime
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log")
    try:
        fh = open(log_path, "a", encoding="utf-8")
        fh.write("\n==== 会话开始 %s ====\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        fh.flush()
        faulthandler.enable(fh)
    except Exception:
        fh = None
    prev_hook = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        try:
            if fh is not None:
                fh.write("\n[未捕获异常 %s]\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
                fh.flush()
        except Exception:
            pass
        return prev_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def _project_arg(argv: list[str]) -> str | None:
    """从命令行里取要打开的 .sstool 路径（Windows 文件关联会把它作为 argv[1]）。"""
    for arg in argv[1:]:
        if arg.lower().endswith(".sstool"):
            return os.path.abspath(arg)
    return None


def main() -> int:
    _install_crash_logging()
    app = QApplication(sys.argv)
    # Qt 6 图片解码默认配额 256MB：超大序列帧工程（几百帧拼出的合成大图解压后
    # 轻松上 500MB）会被直接拒绝——用户看到的就是「工程缺少有效的原图数据」。
    # 程序唯一入口统一放宽到 2GB；打开工程时还有一次 4GB 的抢救重试（main_window）。
    QImageReader.setAllocationLimit(2048)
    app.setApplicationName("SpriteSheetTool")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    # 所有顶层窗口（含弹窗）显示时自动套用黑色标题栏。
    # 内部会保存过滤器引用，并在应用退出前自动卸载，避免退出析构期崩溃。
    install_dark_titlebars(app)

    project_path = _project_arg(sys.argv)

    # ---- 单实例守卫：已经有实例在跑就把请求交给它，本进程直接退出 ----
    guard = SingleInstance()
    if guard.notify_existing(project_path or ""):
        print("SpriteSheetTool 已在运行，已把请求转交给它（不重复启动）。")
        return 0
    guard.listen()
    app.aboutToQuit.connect(guard.close)

    # 使用统计（只报计数、已脱敏、无开关、全静默）。**必须放在单实例守卫之后**：
    # 第二次启动只是"把请求转交给已在跑的实例然后自己退出"，不该算一次启动。
    from spritesheet import telemetry
    telemetry.start()        # 发 start（软件名/版本/脱敏身份在 telemetry 里）
    telemetry.heartbeat()    # 起心跳线程：每 60 秒一包，退出时自动发 exit

    app_root = os.path.dirname(os.path.abspath(__file__))
    # The application icon is intentionally separate from the .sstool file icon.
    icon_path = os.path.join(app_root, "SpriteSheetTool.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(app_root, "SpriteSheetTool.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()

    def _on_external_open(path: str) -> None:
        """另一个实例转过来的请求：开一个新标签页，并把窗口提到前面。"""
        if path:
            window._open_project(path)
        window.showNormal()
        window.raise_()
        window.activateWindow()

    guard.opened.connect(_on_external_open)

    window.show()
    # 「本次更新」：更新完**第一次**打开时弹一次，点掉就记住版本号，之后不再打扰。
    # 用单次定时器推迟 800ms：先把主窗口画出来再弹，启动观感更顺；
    # 逻辑全在 whats_new 里（没看过才弹、异常静默、不碰用户设置）。
    from PyQt6.QtCore import QTimer as _QTimer
    def _show_whats_new() -> None:
        from spritesheet.ui.whats_new import maybe_show
        maybe_show(window)
    _QTimer.singleShot(800, _show_whats_new)
    # Windows file association passes the clicked document as argv[1].
    # Defer opening until the event loop is running so the main window is
    # fully initialized before project state is restored.
    if project_path:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: window._open_project(project_path))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
