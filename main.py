"""SpriteSheetTool 启动入口：python main.py"""
import os
import sys

from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import QApplication

from spritesheet.ui.main_window import MainWindow
from spritesheet.ui.theme import APP_QSS, install_dark_titlebars


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SpriteSheetTool")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    # 所有顶层窗口（含弹窗）显示时自动套用黑色标题栏。
    # 内部会保存过滤器引用，并在应用退出前自动卸载，避免退出析构期崩溃。
    install_dark_titlebars(app)

    app_root = os.path.dirname(os.path.abspath(__file__))
    # The application icon is intentionally separate from the .sstool file icon.
    icon_path = os.path.join(app_root, "SpriteSheetTool.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(app_root, "SpriteSheetTool.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()
    # Windows file association passes the clicked document as argv[1].
    # Defer opening until the event loop is running so the main window is
    # fully initialized before project state is restored.
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".sstool"):
        project_path = os.path.abspath(sys.argv[1])
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: window._open_project(project_path))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
