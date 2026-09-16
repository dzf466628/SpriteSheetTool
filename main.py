"""SpriteSheetTool 启动入口：python main.py

单实例：整个系统只跑一个进程。第二次启动（包括双击 .sstool 文件关联打开）会把
"要打开的文件"转给已经在跑的实例，由它开一个新标签页，然后自己直接退出。
见 ``spritesheet/single_instance.py``。
"""
import os
import sys

from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import QApplication

from spritesheet.single_instance import SingleInstance
from spritesheet.ui.main_window import MainWindow
from spritesheet.ui.theme import APP_QSS, install_dark_titlebars


def _project_arg(argv: list[str]) -> str | None:
    """从命令行里取要打开的 .sstool 路径（Windows 文件关联会把它作为 argv[1]）。"""
    for arg in argv[1:]:
        if arg.lower().endswith(".sstool"):
            return os.path.abspath(arg)
    return None


def main() -> int:
    app = QApplication(sys.argv)
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
    # Windows file association passes the clicked document as argv[1].
    # Defer opening until the event loop is running so the main window is
    # fully initialized before project state is restored.
    if project_path:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: window._open_project(project_path))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
