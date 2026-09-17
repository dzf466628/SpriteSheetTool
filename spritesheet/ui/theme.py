"""深色主题 + 单一玫红强调色（与 AnchorMotion 风格统一）。"""

import ctypes

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

ACCENT = "#E91E63"
ACCENT_HOVER = "#F06292"
BG_0 = "#1a1a1a"   # 最底层
BG_1 = "#232323"   # 窗口/工具栏
BG_2 = "#2b2b2b"   # 面板/输入
BG_3 = "#353535"   # hover
LINE = "#3d3d3d"
TEXT = "#e8e8e8"
TEXT_DIM = "#9a9a9a"

APP_QSS = f"""
* {{
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QMainWindow, QDialog, QWidget {{
    background-color: {BG_1};
}}
QToolBar {{
    background-color: {BG_1};
    border: none;
    border-bottom: 1px solid {LINE};
    spacing: 4px;
    padding: 4px 8px;
}}
QToolBar QToolButton {{
    background-color: transparent;
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 10px;
    min-height: 22px;
}}
QToolBar QToolButton:hover {{
    background-color: {BG_3};
    border: 1px solid {LINE};
}}
QToolBar QToolButton:checked {{
    background-color: rgba(233, 30, 99, 0.18);
    border: 1px solid {ACCENT};
    color: #ffffff;
}}
QPushButton {{
    background-color: {BG_2};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 5px 14px;
}}
QPushButton:hover {{
    background-color: {BG_3};
    border-color: {ACCENT_HOVER};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: #ffffff;
}}
QListWidget {{
    background-color: {BG_0};
    border: 1px solid {LINE};
    border-radius: 4px;
    outline: none;
}}
QListWidget::item {{
    border-bottom: 1px solid #262626;
    padding: 4px 6px;
}}
QListWidget::item:hover {{
    background-color: {BG_2};
}}
QListWidget::item:selected {{
    background-color: rgba(233, 30, 99, 0.25);
    color: #ffffff;
    border-left: 3px solid {ACCENT};
}}
QStatusBar {{
    background-color: {BG_1};
    border-top: 1px solid {LINE};
    color: {TEXT_DIM};
}}
QDockWidget {{
    titlebar-close-icon: none;
    color: {TEXT};
}}
QDockWidget::title {{
    background-color: {BG_1};
    border-bottom: 1px solid {LINE};
    padding: 6px 10px;
}}
QMenuBar {{
    background-color: {BG_1};
    border-bottom: 1px solid {LINE};
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 12px;
}}
QMenuBar::item:selected {{
    background-color: {BG_3};
}}
QMenu {{
    background-color: {BG_2};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px 6px 14px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {ACCENT};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {LINE};
    border-radius: 3px;
    background: {BG_2};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QScrollBar:vertical {{
    background: {BG_0};
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #4a4a4a;
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_HOVER};
}}
QScrollBar:horizontal {{
    background: {BG_0};
    height: 6px;
}}
QScrollBar::handle:horizontal {{
    background: #4a4a4a;
    border-radius: 3px;
    min-width: 20px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0;
}}
QToolTip {{
    background-color: {BG_2};
    color: {TEXT};
    border: 1px solid {ACCENT};
    padding: 3px 6px;
}}
QSpinBox, QDoubleSpinBox {{
    background-color: {BG_2};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 2px 4px;
    min-height: 18px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px;
    background-color: {BG_3};
    border: none;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {ACCENT};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {TEXT};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT};
}}
QComboBox {{
    background-color: {BG_2};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 3px 8px;
    min-height: 18px;
}}
QComboBox:hover {{
    border-color: {ACCENT_HOVER};
}}
QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    width: 22px;
    border: none;
    background-color: {BG_3};
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
}}
QComboBox::drop-down:hover {{
    background-color: {ACCENT};
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {TEXT};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_2};
    border: 1px solid {ACCENT};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    outline: none;
}}
QGroupBox {{
    background-color: {BG_1};
    border: 1px solid {LINE};
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 4px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 6px;
    padding: 0 4px;
    color: {ACCENT_HOVER};
    font-weight: bold;
}}
QLineEdit {{
    background-color: {BG_2};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 3px 6px;
    color: {TEXT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:read-only {{
    background-color: {BG_0};
    color: {TEXT_DIM};
}}
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: {BG_3};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT_HOVER};
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}
"""

# ---- Windows 暗色标题栏 ----
# 系统标题栏默认是浅色，与软件深色风格不一致。
# 通过 DWM API (DWMWA_USE_IMMERSIVE_DARK_MODE) 把每个顶层窗口的标题栏强制为黑色。
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_dwmapi = None


def _apply_dark_titlebar(hwnd: int) -> None:
    global _dwmapi
    try:
        if _dwmapi is None:
            _dwmapi = ctypes.windll.dwmapi
        value = ctypes.c_int(1)
        _dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            _DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        pass  # 非 Windows / 老系统：忽略，保持默认标题栏


def _sip_isdeleted(obj) -> bool:
    """判断 PyQt 包装对象对应的 C++ 对象是否已被销毁（防止悬空指针崩溃）。"""
    try:
        from PyQt6 import sip
        return bool(sip.isdeleted(obj))
    except Exception:
        return False


class DarkTitleBarFilter(QObject):
    """全局事件过滤器：任何顶层窗口显示时自动套用黑色标题栏。

    注意：installEventFilter 不持有 Python 引用，调用方必须保存本对象，
    否则会被 GC 回收导致过滤器静默失效。
    """

    @staticmethod
    def _safe_apply(widget) -> None:
        """给单个窗口套用暗色标题栏；任何异常都吞掉，绝不影响主流程。"""
        try:
            if widget is None or _sip_isdeleted(widget):
                return
            if not widget.isWindow():
                return
            hwnd = int(widget.winId())
            if hwnd:
                _apply_dark_titlebar(hwnd)
        except Exception:  # noqa: BLE001 - 窗口可能已被销毁
            pass

    def eventFilter(self, obj, event):
        etype = event.type()
        if isinstance(obj, QWidget) and etype in (
            QEvent.Type.Show, QEvent.Type.ShowToParent, QEvent.Type.WinIdChange,
        ):
            # 立即设置一次（此时对象必然有效）
            self._safe_apply(obj)
            # 事件循环下一轮再补一次（部分窗口首次设置会被系统覆盖）。
            # 关键：这里【不能】用 lambda 捕获 obj —— 窗口可能在回调执行前被销毁，
            # 访问已析构的 C++ 对象会触发 access violation（原生崩溃，Python 无法捕获）。
            # 改为无参回调，在回调内枚举当前仍存活的顶层窗口。
            QTimer.singleShot(0, self.apply_to_all_top_level)
        return super().eventFilter(obj, event)

    @staticmethod
    def apply_to_all_top_level() -> None:
        """对当前存活的所有顶层窗口套用暗色标题栏。

        只依赖 Qt 返回的存活窗口列表（不含已销毁对象），因此不会产生悬空引用。
        """
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            if _sip_isdeleted(widget):
                continue
            try:
                if widget.isWindow() and widget.isVisible():
                    _apply_dark_titlebar(int(widget.winId()))
            except Exception:  # noqa: BLE001
                pass


def install_dark_titlebars(app: QApplication) -> DarkTitleBarFilter:
    """安装全局黑色标题栏事件过滤器。

    必须在 app.aboutToQuit 时自动卸载：否则程序退出、窗口逐个析构时，
    app 级事件过滤器仍会被回调，访问正在销毁的窗口会造成 access violation 崩溃。
    """
    filter_obj = DarkTitleBarFilter(app)
    # 保存引用：installEventFilter 不持有 Python 引用，否则过滤器会被 GC 回收而失效
    app._dark_title_bar_filter = filter_obj
    app.installEventFilter(filter_obj)

    def _detach() -> None:
        try:
            app.removeEventFilter(filter_obj)
        except Exception:  # noqa: BLE001
            pass

    app.aboutToQuit.connect(_detach)
    return filter_obj
