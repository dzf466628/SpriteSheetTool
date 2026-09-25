"""主窗口：系统标题栏 + 菜单栏 + Logo 名称栏 + 左侧边栏 + 原图区(带标尺) + 右侧预览 + 状态栏。

本模块经 SPLIT_GUIDE 步骤 3-7 拆分后只保留：
- ``UpdateDownloadDialog``：自动更新下载弹窗（主窗口专用）；
- ``_TransformHistoryCommand``：帧变换撤销命令；
- ``MainWindow``：主窗口装配（菜单、侧边栏、原图区/预览区接线、工程读写、导入导出、自动更新）。

其余部件已迁出：
``widgets.py``（通用控件）/ ``image_viewer.py``（原图区）/ ``preview.py``（预览区）/
``export_dialog.py``（导出弹窗）；工程序列化辅助在 ``spritesheet/project_file.py``。
"""
from __future__ import annotations

import atexit
import os
import sys
import weakref
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction, QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QRegion,
    QUndoCommand, QUndoStack,
)
from PyQt6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
    QTreeWidget, QTreeWidgetItem,
)

from spritesheet import __app_name__, __version__, telemetry
from spritesheet.anim import (Animation, fps_and_overrides, is_animated_file,
                              read_animation, size_warning)
from spritesheet.project_file import (
    _pixmap_bytes, _pixmap_from_bytes, _rect_from_dict, read_project, write_project,
)
from spritesheet.settings import (
    last_directory, set_last_directory, show_project_thumbnail,
)
from spritesheet.updater import UPDATE_URL, UpdateCheckThread, UpdateDownloadThread

from .anim_doc import (AnimDoc, AnimDocBar, FramesSource, KeyOp, MODE_DOC, MODE_FRAMES,
                       StrokeOp, TransformOp, anim_doc_from_payload, anim_doc_payload,
                       ask_animation_mode, frames_source_from_payload,
                       frames_source_payload)
from .anim_settings import AnimSettingsBar
from .image_viewer import SUPPORTED_IMAGE_EXT, ImageViewer
from .key_panel import KeyPanel
from .models import Action, MACARON_COLORS
from .preview import ActionPreviewWidget
from .settings_dialog import SettingsDialog
from .transform import (action_frame_pixmap, action_frame_size, _alpha_bounds,
                        cell_rect, compute_align_offsets, compute_source_anchor_offsets,
                        detect_sheet_grid, grid_shape)
from .widgets import (
    ActionCardWidget, ActionItemDelegate, AppNameLabel, ProjectTabBar, _APP_ROOT,
    paint_checkerboard,
)

# 工程文件缩略图：**正方形** 256×256（资源管理器用方形槽位显示，按画面比例输出
# 会让宽条素材显示成一条）。
#   底色  = 灰色棋盘格（与软件预览区同一套配色），内容装不满时由它兜底
#   描边  = 黑 → 白 → 粉 三层（由外到内），把缩略图"罩"起来
#   角标  = 工程图标.png，右下角，占短边 _THUMB_BADGE_DIV 分之一
_THUMB_SIZE = 256
_THUMB_PAD = 6                      # 内容相对描边内缘再留的空白
_THUMB_CHECKER_CELL = 12
_THUMB_BADGE_DIV = 3                # 角标 = 短边/3（此前是 1/4，用户要求再大些）
# 描边三层（外→内）。红色用**角标图的品牌粉** 工程图标.png 的主色 #FB5582，
# 和右下角标同色；界面强调色是 theme.ACCENT #E91E63，要换成它就改这一处。
_THUMB_STROKE = (("#000000", 4), ("#FFFFFF", 4), ("#FB5582", 6))


# 主窗口
class UpdateDownloadDialog(QDialog):
    """自动更新下载对话框（深色风格 + 实时进度 + 取消）。"""

    download_ok = pyqtSignal(str)  # 安装包本地路径

    def __init__(self, version: str, url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在更新")
        self.setModal(True)
        self.setFixedWidth(420)
        self._url = url
        self._thread: UpdateDownloadThread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(f"正在下载 v{version} ...")
        title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        self.lbl_status = QLabel("连接服务器 ...")
        self.lbl_status.setStyleSheet("color: #9a9a9a; font-size: 12px;")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            "QProgressBar { background: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 4px;"
            " height: 14px; text-align: center; color: #ffffff; font-size: 10px; }"
            "QProgressBar::chunk { background: #E91E63; border-radius: 3px; }"
        )
        layout.addWidget(self.progress)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self.btn_cancel, 0, Qt.AlignmentFlag.AlignRight)

        self._start()

    def _start(self):
        import tempfile
        target_dir = os.path.join(tempfile.gettempdir(), "SpriteSheetToolUpdate")
        self._thread = UpdateDownloadThread(self._url, target_dir, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.download_finished.connect(self._on_finished)
        self._thread.download_failed.connect(self._on_failed)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_progress(self, done: int, total: int):
        if total > 0:
            percent = int(done * 100 / total)
            self.progress.setValue(percent)
            self.lbl_status.setText(
                f"已下载 {done / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB"
            )
        else:
            self.lbl_status.setText(f"已下载 {done / 1024 / 1024:.1f} MB")

    def _on_finished(self, path: str):
        self.lbl_status.setText("下载完成，即将安装 ...")
        self.progress.setValue(100)
        self.btn_cancel.setEnabled(False)
        QTimer.singleShot(300, lambda: (self.accept(), self.download_ok.emit(path)))

    def _on_failed(self, reason: str):
        QMessageBox.warning(self, "下载失败", f"下载更新失败：\n{reason}")
        self.reject()

    def _on_cancel(self):
        if self._thread is not None:
            self._thread.cancel()
        self.reject()

    def _on_thread_done(self):
        self._thread = None


class _TransformHistoryCommand(QUndoCommand):
    """Undo command for per-frame free-transform state."""

    def __init__(self, owner, before, after, text: str = "变换") -> None:
        super().__init__(text)
        self._owner = owner
        self.before = before
        self.after = after
        self._initial_redo = True

    def redo(self) -> None:
        if self._initial_redo:
            self._initial_redo = False
            return
        self._owner._restore_transform_state(self.after)

    def undo(self) -> None:
        self._owner._restore_transform_state(self.before)


class _SnapshotCommand(QUndoCommand):
    """全量快照式 undo：保存整个工程状态（含帧图），undo/redo 恢复。"""

    def __init__(self, owner, before: dict, after: dict, text: str) -> None:
        super().__init__(text)
        self._owner = owner
        self.before = before
        self.after = after
        self._initial_redo = True

    def redo(self) -> None:
        if self._initial_redo:
            self._initial_redo = False
            return
        self._owner._restore_snapshot(self.after)

    def undo(self) -> None:
        self._owner._restore_snapshot(self.before)


class AboutDialog(QDialog):
    """关于对话框：GPL v3 声明 + 第三方组件清单。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于 SpriteSheetTool")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 软件名前面加图标（用户要求）。用**应用自己的窗口图标**，不猜文件路径：
        # 打包/未打包、换机器都一致；拿不到图标就老老实实只显示名字，不留空白。
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        _app = QApplication.instance()
        _icon = _app.windowIcon() if _app is not None else None
        if _icon is not None and not _icon.isNull():
            icon_label = QLabel()
            icon_label.setFixedSize(28, 28)
            icon_label.setPixmap(_icon.pixmap(28, 28))
            title_row.addWidget(icon_label)
        title = QLabel(f"SpriteSheetTool  v{__version__}")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: bold;")
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        subtitle = QLabel("精灵图打包工具：自动检测元素、按动作编排动画、自由变换、一键导出图集/GIF/JSON")
        subtitle.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addSpacing(4)

        gpl_label = QLabel(
            "本软件以 <span style='color:#E91E63;'>GNU GPL v3</span> 开源，"
            "你可以自由使用、修改和再分发。"
            "程序按\"原样\"提供，不附带任何担保。"
        )
        gpl_label.setStyleSheet("color: #e8e8e8; font-size: 11px;")
        gpl_label.setWordWrap(True)
        layout.addWidget(gpl_label)

        layout.addSpacing(4)

        third_title = QLabel("使用的第三方开源组件：")
        third_title.setStyleSheet("color: #e8e8e8; font-size: 12px; font-weight: bold;")
        layout.addWidget(third_title)

        components = [
            ("PyQt6 6.11", "GPL v3 / Commercial — Riverbank Computing"),
            ("Pillow 12.3", "HPND License — Python Imaging Library"),
            ("PyInstaller 6.22", "GPL v2+ with Bootloader Exception"),
            ("Inno Setup 6", "Inno Setup License — Jordan Russell"),
            ("简体中文翻译", "Inno Setup 社区翻译 — kira-96"),
        ]
        for name, desc in components:
            item = QLabel(f"  •  <span style='color:#E91E63;'>{name}</span>  —  {desc}")
            item.setStyleSheet("color: #b0b0b0; font-size: 10px;")
            item.setWordWrap(True)
            layout.addWidget(item)

        layout.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # 最下面：官网 + 邮箱（用户要求）。做成**可点的链接**（点官网开浏览器、
        # 点邮箱直接起草邮件），比自己抄一遍地址强；文案走同一套灰，不抢眼。
        layout.addSpacing(10)
        footer = QLabel(
            "官网 <a href='https://duadu.cc' style='color:#E91E63;'>duadu.cc</a>"
            "　·　邮箱 <a href='mailto:3140992714@qq.com' style='color:#E91E63;'>"
            "3140992714@qq.com</a>"
        )
        footer.setOpenExternalLinks(True)
        footer.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        footer.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        layout.addWidget(footer)


class HistoryPanel(QWidget):
    """树状操作历史：全局历史 + 每个动作的历史，全部分组显示在同一棵树里。

    记录归属与撤销隔离：
    - 全局操作（添加/删除动作等）落在全局 QUndoStack；
    - 动作内操作（选择/变换/偏移/动画参数）落在该动作自己的 QUndoStack；
    - 点击哪条记录就只跳转那一条所在的栈，互不影响。
    """

    def __init__(self, main: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        # 用弱引用持有主窗口：面板是主窗口的子控件，生命周期由 Qt 父子树管理。
        # 若再强引用回主窗口就形成 Python 循环引用，解释器退出时 GC 顺序
        # 会让挂着的信号连接在 Qt 析构中崩溃（0xC0000409）。
        self._main_ref = weakref.ref(main)
        self._stacks: list[tuple[object, QUndoStack]] = []   # (key, stack)；key 为 Action 或 "global"
        self._conns: list = []                                # [(signal, connection)]
        self._synced_keys: list = []
        # 解释器退出兜底：offscreen 回归脚本不走 app.exec()/close()，
        # aboutToQuit/closeEvent 都不触发；用 atexit 保证 Python 收尾前先断开，
        # 否则挂着到 QUndoStack 的连接会在 Qt 析构时 fail-fast（0xC0000409）。
        atexit.register(self.disconnect_all)
        self.setObjectName("historyPanel")
        self.setStyleSheet(
            "#historyPanel { background: #1e1e1e; border: 1px solid #3d3d3d; border-radius: 6px; }"
        )
        self.setMinimumWidth(220)
        self.setMaximumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(
            "QTreeWidget { background: #1a1a1a; color: #e0e0e0; border: 1px solid #3d3d3d; border-radius: 4px; }"
            "QTreeWidget::item { padding: 3px 6px; font-size: 11px; }"
            "QTreeWidget::item:selected { background: rgba(233,30,99,0.3); }"
            "QTreeBranch:open, QTreeBranch:closed { color: #F06292; }"
        )
        self.tree.itemClicked.connect(self._jump_to)
        layout.addWidget(self.tree)

        self._refresh()

    # ------------------------------------------------------------- 栈管理
    def rebuild(self) -> None:
        """动作列表增删后调用：重连各栈的信号。"""
        self._sync_stacks()
        self._refresh()

    def disconnect_all(self) -> None:
        """关窗/退出前断开所有栈连接并清空引用。

        关键收尾：面板持有到各 QUndoStack 的活动信号连接与包装引用，
        若拖到解释器退出时再析构，会在 Qt 侧触发 fail-fast 崩溃
        （0xC0000409）。断开后这些对象就能安全回收。
        """
        for sig, conn in self._conns:
            try:
                sig.disconnect(conn)
            except Exception:  # noqa: BLE001 - 栈可能已被销毁
                pass
        self._conns = []
        self._stacks = []
        self._synced_keys = []

    @property
    def _main(self) -> "MainWindow | None":
        """弱引用解析：主窗口已被回收时返回 None。"""
        return self._main_ref()

    def _wanted_stacks(self) -> list[tuple[object, QUndoStack]]:
        """当前应展示的栈：全局 + 每个动作的独立栈。"""
        main = self._main
        if main is None:
            return []
        stacks: list[tuple[object, QUndoStack]] = [("global", main._undo_stack)]
        for action in main.actions:
            if action.undo_stack is not None:
                stacks.append((action, action.undo_stack))
        return stacks

    def _sync_stacks(self) -> None:
        """把 _stacks 同步到当前动作列表；只有栈集合变化时才重连信号。"""
        wanted = self._wanted_stacks()
        sig = [(key, id(stack)) for key, stack in wanted]
        if sig == self._synced_keys:
            return
        for old_sig, conn in self._conns:
            try:
                old_sig.disconnect(conn)
            except Exception:  # noqa: BLE001 - 栈可能已被销毁
                pass
        self._conns = []
        self._synced_keys = sig
        self._stacks = []
        for key, stack in wanted:
            signal = stack.indexChanged
            self._conns.append((signal, signal.connect(self._refresh)))
            self._stacks.append((key, stack))

    # ------------------------------------------------------------- 刷新
    def _active_key(self) -> object:
        """当前 undo 作用在哪个栈上（决定哪条记录高亮）。"""
        main = self._main
        if main is None:
            return "global"
        active = main._active_stack()
        if active is main._undo_stack:
            return "global"
        return main.image_viewer.current_action

    def _refresh(self, *_):
        self._sync_stacks()
        self.tree.clear()
        active_key = self._active_key()
        for key, stack in self._stacks:
            if key == "global" and stack.count() == 0:
                continue
            label = "全局历史" if key == "global" else f"动作：{key.name}"
            parent = QTreeWidgetItem(self.tree, [f"{label}（{stack.count() + 1}）"])
            parent.setForeground(0, QColor("#F06292"))
            is_active = key is active_key
            idx = stack.index()
            # 初始状态
            root_item = QTreeWidgetItem(parent, ["● 初始状态"])
            root_item.setData(0, Qt.ItemDataRole.UserRole, (key, 0))
            if is_active and idx == 0:
                self._highlight(root_item)
            for i in range(stack.count()):
                cmd = stack.command(i)
                text = cmd.text() or f"操作 {i + 1}"
                child = QTreeWidgetItem(parent, [text])
                child.setData(0, Qt.ItemDataRole.UserRole, (key, i + 1))
                if is_active and i + 1 == idx:
                    self._highlight(child)
            parent.setExpanded(is_active or stack.count() > 0)

    @staticmethod
    def _highlight(item: QTreeWidgetItem) -> None:
        item.setBackground(0, QColor(233, 30, 99, 60))
        item.setForeground(0, QColor("#ffffff"))

    # ------------------------------------------------------------- 跳转
    def _jump_to(self, item, *_):
        if item.childCount() > 0:
            return  # 点的是分组头，不跳转
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            return
        key, idx = payload
        if key == "global":
            return  # 全局历史只展示，不可点击撤回
        main = self._main
        if isinstance(key, Action):
            # 该记录属于某个动作：先把它切成当前动作（不动其他动作的数据），再跳转
            if main is not None and main.image_viewer.current_action is not key:
                main.select_action_for_history(key)
            stack = key.undo_stack
        else:
            stack = main._undo_stack if main is not None else None
        if stack is not None and 0 <= idx <= stack.count():
            stack.setIndex(int(idx))


class _FloatCard(QWidget):
    """无系统标题栏的悬浮卡片：自己画圆角背景 + 自绘标题栏（标题 + 关闭按钮）。

    用户要求："动作编辑与帧编辑的系统标题栏去掉，关闭按钮直接放到我们自己的标题栏右边"，
    并且"这几个窗口改为圆角显示"。所以这里用 FramelessWindowHint + 半透明背景，
    自己画一个圆角卡片，标题栏里放标题和 ✕；拖动标题栏即可移动窗口。
    """

    TITLE_H = 26
    # 用户要求：最大化后四个窗口用**和分界线同款的浅灰**（分隔线是 #3d3d3d）
    BG_COLOR = "#3d3d3d"
    BORDER_COLOR = "#555555"    # 描边：#6e6e6e 仍偏亮，用户要求再暗一点（保持 2px）
    BORDER_W = 2                # 描边宽度（像素）
    RADIUS = 10.0

    EDGE = 6                    # 拖边框改大小的感应宽度（像素）
    _corner_cache = None       # 圆角四角的 QRegion 模板（只与 RADIUS 有关，类级缓存）

    def __init__(self, title: str, parent=None, resizable: bool = False) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self.setObjectName("FloatCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 普通 QWidget 默认不画样式表背景，不设这个卡片就是"透明"的（历史记录实测踩到）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._drag_offset = None
        self._resize_edge = None        # 拖边框改大小时记的方向（'r'/'b'/'br'…）
        self._resize_origin = None
        self.resizable = bool(resizable)
        self.setMouseTracking(True)
        self._edge_cursor_on = False    # 边缘缩放光标是否正由 overrideCursor 接管
        self._did_grab = False          # 本次拖边是否由卡片 grabMouse（offscreen 不抓）

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)        # 给 1px 边框留位置
        outer.setSpacing(0)

        self.title_bar = QWidget()
        self.title_bar.setObjectName("FloatCardTitle")
        self.title_bar.setFixedHeight(self.TITLE_H)
        self.title_bar.setCursor(Qt.CursorShape.SizeAllCursor)   # 提示可拖动
        # 同上：子控件也要这个属性，否则标题栏样式表背景同样不画
        self.title_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb = QHBoxLayout(self.title_bar)
        tb.setContentsMargins(10, 0, 5, 0)
        tb.setSpacing(6)
        self.lbl_title = QLabel(title)
        tb.addWidget(self.lbl_title)
        tb.addStretch(1)
        # 用户要求：去掉关闭按钮 —— 最大化时这几个窗口不能被关掉
        outer.addWidget(self.title_bar)

        self.body = QWidget()
        self.body.setObjectName("FloatCardBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        outer.addWidget(self.body, 1)

        # ID 选择器只涂卡片自身/标题栏，避免级联污染内部控件（项目里的既有约定）
        self.setStyleSheet(
            "#FloatCardTitle { background: #333333; border-top-left-radius: 9px;"
            " border-top-right-radius: 9px; }"
            "#FloatCardTitle QLabel { color: #e0e0e0; font-size: 11px; font-weight: 600;"
            " background: transparent; }"
            "#FloatCardBody { background: transparent; }"
        )

    def paintEvent(self, event):
        """自绘卡片底：圆角**不透明**背景 + 1 像素**黑色**描边（用户要求）。

        必须自绘：普通 QWidget 不设 WA_StyledBackground 时压根不画样式表背景 ——
        之前历史记录看着透明就是这个原因。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        half = self.BORDER_W / 2.0
        rect = QRectF(self.rect()).adjusted(half, half, -half, -half)
        p.setPen(QPen(QColor(self.BORDER_COLOR), float(self.BORDER_W)))
        p.setBrush(QColor(self.BG_COLOR))
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.end()

    def content_parent_padding(self) -> int:
        """内容区相对窗口左上角多出来的高度（标题栏 + 边框）。"""
        return self.TITLE_H + 2

    def set_content(self, w: QWidget) -> None:
        """把面板塞进卡片内容区。"""
        self.body_layout.addWidget(w)
        if self.resizable:
            self._install_edge_filters(w)

    def _install_edge_filters(self, w: QWidget) -> None:
        """内容子控件铺满边缘，卡片自己收不到边缘的鼠标事件；递归装过滤器，
        保证在子控件上也能①显示缩放双向箭头 ②按下开始拖边。"""
        w.setMouseTracking(True)
        w.installEventFilter(self)
        for child in w.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    @classmethod
    def _corner_regions(cls):
        """渲染一张 4R×4R 圆角瓦片（与 paintEvent 同路径），取四个角的 R×R
        卡片**完全不透明**的像素逐行收成 QRegion，作为四角模板。
        遮罩边界与真实绘制逐像素一致：方角内容不可能露到圆角外，
        也不会误裁实心描边。只依赖 RADIUS/BORDER_W，全程类级只算一次。"""
        if cls._corner_cache is not None:
            return cls._corner_cache
        rad = int(round(cls.RADIUS))
        span = 4 * rad     # 瓦片足够大，圆角的栅格化才与真实大卡片一致
        tile = QImage(span, span, QImage.Format.Format_ARGB32)
        tile.fill(QColor(0, 0, 0, 0))
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        half = cls.BORDER_W / 2.0
        p.setPen(QPen(QColor(cls.BORDER_COLOR), float(cls.BORDER_W)))
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(0, 0, span, span).adjusted(half, half, -half, -half),
                          cls.RADIUS, cls.RADIUS)
        p.end()
        # 瓦片四个角的 R×R 即四个角模板：(左上, 右上, 左下, 右下)
        origins = {(0, 0): (0, 0), (1, 0): (3 * rad, 0),
                   (0, 1): (0, 3 * rad), (1, 1): (3 * rad, 3 * rad)}
        quads = {}
        for (qx, qy), (ox, oy) in origins.items():
            reg = QRegion()
            for y in range(oy, oy + rad):
                x0 = None
                for x in range(ox, ox + rad):
                    opaque = (tile.pixel(x, y) >> 24) & 255 == 255
                    if opaque and x0 is None:
                        x0 = x
                    if not opaque and x0 is not None:
                        reg = reg.united(QRect(x0 - ox, y - oy, x - x0, 1))
                        x0 = None
                if x0 is not None:
                    reg = reg.united(QRect(x0 - ox, y - oy, ox + rad - x0, 1))
            quads[(qx, qy)] = reg
        cls._corner_cache = quads
        return quads

    def _card_region(self) -> QRegion:
        """圆角外形：中间矩形带 + 四个与实际绘制一致的圆角模板。"""
        rad = int(round(self.RADIUS))
        w, h = self.width(), self.height()
        quads = self._corner_regions()
        region = QRegion(QRect(0, rad, w, h - 2 * rad))          # 中间整条
        region = region.united(QRect(rad, 0, w - 2 * rad, rad))  # 顶部直边
        region = region.united(QRect(rad, h - rad, w - 2 * rad, rad))  # 底部直边
        region = region.united(quads[(0, 0)].translated(0, 0))
        region = region.united(quads[(1, 0)].translated(w - rad, 0))
        region = region.united(quads[(0, 1)].translated(0, h - rad))
        region = region.united(quads[(1, 1)].translated(w - rad, h - rad))
        return region

    def resizeEvent(self, event):
        """圆角遮罩：内部面板是方角不透明底，不挖掉四角会把卡片圆角描边盖住。"""
        if QApplication.platformName() != 'offscreen':
            self.setMask(self._card_region())
        super().resizeEvent(event)

    def hideEvent(self, event):
        self._restore_edge_cursor()
        super().hideEvent(event)

    def take_content(self):
        item = self.body_layout.takeAt(0)
        return item.widget() if item is not None else None

    def _edge_at(self, pos) -> str:
        """这个点落在哪条可拖拽的边上（无边框窗口的缩放只能自己实现）。"""
        if not self.resizable:
            return ""
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        right, bottom = x >= w - self.EDGE, y >= h - self.EDGE
        left, top = x <= self.EDGE, y <= self.EDGE
        if bottom and right:
            return "br"
        if bottom and left:
            return "bl"
        if top and right:
            return "tr"
        if top and left:
            return "tl"
        if right:
            return "r"
        if bottom:
            return "b"
        if left:
            return "l"
        if top:
            return "t"
        return ""

    # 每条边/角对应的缩放光标：角上必须是斜双箭头，不能是十字移动光标
    _EDGE_CURSORS = {
        "r": Qt.CursorShape.SizeHorCursor, "l": Qt.CursorShape.SizeHorCursor,
        "b": Qt.CursorShape.SizeVerCursor, "t": Qt.CursorShape.SizeVerCursor,
        "br": Qt.CursorShape.SizeFDiagCursor, "tl": Qt.CursorShape.SizeFDiagCursor,
        "bl": Qt.CursorShape.SizeBDiagCursor, "tr": Qt.CursorShape.SizeBDiagCursor,
    }

    def _set_edge_cursor(self, edge: str) -> None:
        cur = self._EDGE_CURSORS.get(edge, Qt.CursorShape.SizeAllCursor)
        if self._edge_cursor_on:
            QApplication.changeOverrideCursor(cur)
        else:
            QApplication.setOverrideCursor(cur)
            self._edge_cursor_on = True

    def _restore_edge_cursor(self) -> None:
        if self._edge_cursor_on:
            QApplication.restoreOverrideCursor()
            self._edge_cursor_on = False

    def _begin_resize(self, global_pos) -> bool:
        """在卡片坐标里判断落在哪条边；命中就进入缩放（光标换箭头、抓取鼠标）。"""
        edge = self._edge_at(self.mapFromGlobal(global_pos)) if self.resizable else ""
        if not edge:
            return False
        self._resize_edge = edge
        self._resize_origin = (QPoint(global_pos), self.geometry())
        self._set_edge_cursor(edge)
        self._did_grab = False
        if QApplication.platformName() != 'offscreen':
            try:
                self.grabMouse()   # 拖出窗口外也不断拖（offscreen 平台会崩，跳过）
                self._did_grab = True
            except Exception:
                pass
        return True

    def _apply_resize(self, global_pos) -> None:
        if self._resize_edge is None or self._resize_origin is None:
            return
        start, geo = self._resize_origin
        now = QPoint(global_pos)
        dx, dy = now.x() - start.x(), now.y() - start.y()
        min_w, min_h = max(200, self.minimumWidth()), max(150, self.minimumHeight())
        rect = QRectF(geo)
        if "r" in self._resize_edge:
            rect.setWidth(max(min_w, geo.width() + dx))
        if "l" in self._resize_edge:
            new_w = max(min_w, geo.width() - dx)
            rect.setLeft(geo.right() - new_w + 1)
            rect.setWidth(new_w)
        if "b" in self._resize_edge:
            rect.setHeight(max(min_h, geo.height() + dy))
        if "t" in self._resize_edge:
            new_h = max(min_h, geo.height() - dy)
            rect.setTop(geo.bottom() - new_h + 1)
            rect.setHeight(new_h)
        self.setGeometry(rect.toRect())

    def _end_resize(self) -> None:
        if self._did_grab:
            try:
                self.releaseMouse()
            except Exception:
                pass
        self._did_grab = False
        self._resize_edge = None
        self._resize_origin = None
        self._restore_edge_cursor()

    def eventFilter(self, obj, event):
        """穿透到内容子控件：边缘悬停换缩放光标、在子控件上按下也能开始拖边。
        只在悬浮可见时工作；内容还原停靠后卡片隐藏，残留过滤器必须放行。"""
        if self.resizable and self.isVisible():
            t = event.type()
            if t == QEvent.Type.MouseMove:
                gp = event.globalPosition().toPoint()
                if self._resize_edge is not None:
                    self._apply_resize(gp)
                    return True
                edge = self._edge_at(self.mapFromGlobal(gp))
                if edge:
                    self._set_edge_cursor(edge)
                else:
                    self._restore_edge_cursor()
            elif (t == QEvent.Type.MouseButtonPress
                  and event.button() == Qt.MouseButton.LeftButton):
                if self._begin_resize(event.globalPosition().toPoint()):
                    event.accept()
                    return True
            elif (t == QEvent.Type.MouseButtonRelease
                  and event.button() == Qt.MouseButton.LeftButton
                  and self._resize_edge is not None):
                self._apply_resize(event.globalPosition().toPoint())
                self._end_resize()
                event.accept()
                return True
            elif t == QEvent.Type.Leave and self._resize_edge is None:
                self._restore_edge_cursor()
            elif t == QEvent.Type.ChildPolished:
                child = event.child()
                if isinstance(child, QWidget):
                    child.setMouseTracking(True)
                    child.installEventFilter(self)
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            if self._begin_resize(event.globalPosition().toPoint()):
                event.accept()
                return
            if pos.y() <= self.TITLE_H + 1:      # 标题栏 = 拖窗口
                self._drag_offset = pos
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._resize_edge is not None:
            self._apply_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_offset is not None:
            self.move(self.pos() + pos - self._drag_offset)
            event.accept()
            return
        if self.resizable:
            edge = self._edge_at(pos)
            if edge:
                self._set_edge_cursor(edge)
            else:
                self._restore_edge_cursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resize_edge is not None:
            self._apply_resize(event.globalPosition().toPoint())
            self._end_resize()
            event.accept()
            return
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    """主窗口：系统标题栏 + 菜单栏 + Logo名称栏 + 左侧边栏 + 原图区 + 右侧预览 + 状态栏。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        # Match the supplied reference layout.  Keep this as both the default
        # and the minimum so the adjustment panel is never vertically clipped.
        self.setMinimumSize(1000, 1000)
        self.resize(1000, 1000)
        self.setAcceptDrops(True)   # 支持拖入图片或 JSON 工程
        self.actions: list[Action] = []
        self._undo_stack = QUndoStack(self)
        from spritesheet.settings import undo_limit
        self._undo_stack.setUndoLimit(undo_limit())
        self._transform_history_state = None
        self._transform_history_pending = None
        self._restoring_transform_history = False
        # 快照记录基线：始终等于"最近一次落账后的文档状态"（current_only 格式）。
        # 任何动作内改动（选择/偏移/动画参数…）的 before 都取它，因此单次改动、
        # 交错改动都能正确记录，undo 一步只回退一步。
        self._last_snapshot_state: dict | None = None
        self._per_action_snap_timer: QTimer | None = None
        self._per_action_snap_text = ""
        self._project_path: Path | None = None
        self._project_json_path: str | None = None  # 当前工程关联的 JSON 文件路径
        self._project_dirty = False
        # ② 动图文档（以单张图片修改）：当前标签页的动图数据，普通工程时为 None。
        # 像素状态（帧 + 操作链）全在这个 AnimDoc 里，撤销也走它自己的操作链（见 anim_doc.py）。
        self._anim_doc: AnimDoc | None = None
        # 动图文档喂给预览/导出弹窗的那份"壳动作"（只装帧和逐帧时长）
        self._anim_preview_action: Action | None = None
        # ① 序列帧那边的"这份工程是从动图来的"记忆（那条横条 + 转回动图文档都靠它）。
        # 与 _anim_doc 互斥：文档模式时为 None，序列帧模式时才有值。
        self._anim_source: FramesSource | None = None
        # 工程显示名（标签页上那个）与"是否被用户改过名"。改名后不再被文件名覆盖。
        self._project_name = "未命名工程"
        self._name_custom = False
        # 标签页文档表：每项 {name, dirty, state}。state 为 None 表示这份正"活着"
        # （数据就在 self.actions / image_viewer / preview 里），否则是切走时抓的快照。
        self._documents: list[dict] = []
        self._act_undo = None
        self._build_ui()
        self._build_menu()
        self._transform_history_state = self._capture_transform_state()
        self._last_snapshot_state = self._capture_snapshot()
        # 起始标签页
        self._documents.append({"name": self._project_name, "dirty": False, "state": None})
        self.tabs.add_tab(self._project_name)
        self._refresh_tab_label()
        self._set_project_title()

        # 历史记录面板：装进独立悬浮卡片里（见 _ensure_history_card）。
        # 注意：**不要用 QDockWidget** —— 它被拖到主窗口上方会自动"吸"进去，
        # 用户明确不要这个行为。
        self._history_panel = HistoryPanel(self, self)
        self._history_panel.setObjectName("HistoryPanel")
        # 面板自己也要有底色：卡片是圆角半透明窗口，里面透明就会"看起来是透明弹窗"
        self._history_panel.setStyleSheet(
            "#HistoryPanel { background: #1f1f1f; border: none; }"
        )
        QTimer.singleShot(0, self._init_history_card)
        # 程序退出时断开历史面板到各 undo 栈的连接（防 Qt 析构崩溃）
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._history_panel.disconnect_all)

    def _capture_transform_state(self):
        """只存当前动作的变换状态，undo 不影响其他动作。"""
        cur = self.image_viewer.current_action
        if cur is None:
            return tuple()
        return tuple([(
            id(cur), cur,
            tuple(sorted((int(k), int(v)) for k, v in cur.frame_rotations.items())),
            tuple(sorted(int(k) for k in cur.frame_mirror_h)),
            tuple(sorted(int(k) for k in cur.frame_mirror_v)),
            tuple(sorted((int(k), tuple(float(x) for x in v))
                         for k, v in cur.frame_rotation_pivots.items())),
            tuple(sorted((int(k), tuple(float(x) for x in v))
                         for k, v in cur.frame_quads.items())),
            tuple(sorted((int(k), tuple(sorted(str(x) for x in v)))
                         for k, v in cur.frame_transform_modes.items())),
        )])

    def _restore_transform_state(self, state) -> None:
        if self._restoring_transform_history:
            return
        self._restoring_transform_history = True
        self._suppress_selection_snapshot = True
        # 停掉所有 pending debounce timer
        if self._per_action_snap_timer is not None:
            self._per_action_snap_timer.stop()
        self._per_action_snap_text = ""
        self._snapshot_before = None
        try:
            for _, action, rotations, mirror_h, mirror_v, pivots, quads, modes in state:
                if action not in self.actions:
                    continue
                action.frame_rotations = dict(rotations)
                action.frame_mirror_h = set(mirror_h)
                action.frame_mirror_v = set(mirror_v)
                action.frame_rotation_pivots = dict(pivots)
                action.frame_quads = dict(quads)
                action.frame_transform_modes = {k: set(v) for k, v in modes}
                action.invalidate_transforms()
            self._transform_history_state = self._capture_transform_state()
            cur = self.image_viewer.current_action
            if cur in self.actions:
                self.image_viewer.crop_frames_for_action(cur)
            if getattr(self, "preview", None) is not None:
                self.preview.adjust_panel._sync_transform_widgets()
                self.preview.adjust_panel._refresh_canvas_label()
                self.preview._refresh_thumbs()
                self.preview.canvas.update()
                self.preview.adjust_panel._transform_changed = False
        finally:
            self._restoring_transform_history = False
            self._suppress_selection_snapshot = False
            self._last_snapshot_state = self._capture_snapshot()

    def _begin_transform_history(self) -> None:
        self._suppress_selection_snapshot = True
        if not self._restoring_transform_history and self._transform_history_pending is None:
            self._transform_history_pending = self._capture_transform_state()

    def _transform_text(self) -> str:
        """根据当前变换模式生成历史记录文字。"""
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        mode = getattr(self.preview.canvas, "_transform_mode", "")
        fi = getattr(self.preview, "_frame_index", 0)
        if mode == "all":
            return f"变换全部帧{name}"
        if mode:
            return f"变换帧{fi+1}{name}({mode})"
        return f"变换帧{fi+1}{name}"

    def _finish_transform_history(self) -> None:
        self._suppress_selection_snapshot = False
        if self._transform_history_pending is None:
            return
        before = self._transform_history_pending
        self._transform_history_pending = None
        after = self._capture_transform_state()
        if before != after and not self._restoring_transform_history:
            self._active_stack().push(_TransformHistoryCommand(self, before, after, self._transform_text()))
            # 变换已单独落账 → 快照基线同步到最新，避免被快照 timer 再记一条
            self._last_snapshot_state = self._capture_snapshot()
        self._transform_history_state = after

    def _on_transform_history_changed(self) -> None:
        if self._restoring_transform_history:
            return
        current = self._capture_transform_state()
        if self._transform_history_pending is not None:
            return
        before = self._transform_history_state
        if before is not None and before != current:
            self._active_stack().push(_TransformHistoryCommand(self, before, current, self._transform_text()))
            self._last_snapshot_state = self._capture_snapshot()
        self._transform_history_state = current

    def _reset_transform_history(self) -> None:
        """替换全部内容（打开工程/首次载图/新建标签导入）时：清空所有 undo 历史再收尾。"""
        self._undo_stack.clear()
        for a in self.actions:
            if a.undo_stack is not None:
                a.undo_stack.clear()
        self._refresh_history_after_change()

    def _refresh_history_after_change(self) -> None:
        """内容/帧变化后的统一收尾：停 pending、刷新基线，**不清任何 undo 历史**。

        追加图片、导入 JSON 这类"在现有工程上叠加"的操作应该保留已有历史，
        只把快照基线切到最新状态，后续动作内记录才不会串到旧状态。
        """
        self._transform_history_pending = None
        self._transform_history_state = self._capture_transform_state()
        if self._per_action_snap_timer is not None:
            self._per_action_snap_timer.stop()
        self._per_action_snap_text = ""
        self._last_snapshot_state = self._capture_snapshot()
        if hasattr(self, "_history_panel"):
            self._history_panel.rebuild()

    def _flush_pending_snapshot(self) -> None:
        """切动作/删动作/切文档前把还没落账的改动记下来（归属它自己的栈）。"""
        if self._per_action_snap_timer is not None and self._per_action_snap_timer.isActive():
            self._finish_per_action_snapshot()

    def _active_stack(self) -> QUndoStack:
        """当前动作的 undo stack（动作内操作），没有则用全局 stack。"""
        cur = self.image_viewer.current_action
        if cur is not None and cur.undo_stack is not None:
            return cur.undo_stack
        return self._undo_stack

    def _do_undo(self) -> None:
        """撤销：优先当前动作的栈，动作栈撤不动了再落到全局栈。

        为什么要有"落到全局栈"这一步：抠除背景、添加原图这类**工程级**操作用
        ``_push_snapshot(..., current_only=False)`` 记在全局栈里，而以前这里只认
        ``_active_stack()`` —— 选中动作时它指向动作自己的栈，于是按 Ctrl+Z 会出现
        "点了完全没反应"（全局记录明明在，却够不到）。现在动作内操作先撤，
        撤完了才落到全局；顺序约定写进 NEXT.md。
        """
        doc = self._anim_doc
        if doc is not None:
            # 动图文档走它自己的操作链（originals + ops 重放），不走快照栈 ——
            # N 帧一份快照就是几十 MB、几十步就上 G（理由见 ui/anim_doc.py 开头）
            label = doc.undo_label()          # 撤之前先记下要撤的是哪一步
            if doc.undo(self.image_viewer):
                self._anim_doc_after_op(f"已撤销：{label}（画面已按操作链重算）")
                return
        # 文档里没得撤了**不 return**：继续往下落到全局栈 —— 那里可能压着
        # 「转为序列帧 / 转为动图文档」这种跨模式的记录（用户口径：转换要能 Ctrl+Z 退回，
        # 连"撤销后就退回动图文档"也要成立）。落到别的快照上也是对的：快照里带着
        # anim_doc/anim_source，恢复后模式与画面是一致的。
        stack = self._active_stack()
        if stack.canUndo():
            stack.undo()
            return
        if stack is not self._undo_stack and self._undo_stack.canUndo():
            self._undo_stack.undo()

    def _do_redo(self) -> None:
        doc = self._anim_doc
        if doc is not None:
            label = doc.redo_label()
            if doc.redo(self.image_viewer):
                self._anim_doc_after_op(f"已重做：{label}")
                return
        # 同 _do_undo：文档里没得重做了就落到全局栈（跨模式的那条记录在那边）
        stack = self._active_stack()
        if stack.canRedo():
            stack.redo()
            return
        if stack is not self._undo_stack and self._undo_stack.canRedo():
            self._undo_stack.redo()

    def _capture_snapshot(self, current_only: bool = True, focus_action: Action | None = None) -> dict:
        """纯数据快照：不编码任何 PNG。

        current_only=True（默认）：只存当前动作，undo 不影响其他动作。
        current_only=False：存全部动作（用于添加/删除动作）。
        focus_action：把某个动作当"当前动作"来抓（改非当前动作的名字/颜色时用），
        恢复时会把数据写回这个动作自己，而不是当前选中的动作。
        """
        viewer = self.image_viewer
        cur = focus_action if focus_action is not None else viewer.current_action
        cur_idx = self.actions.index(cur) if cur in self.actions else -1

        def _action_data(action):
            parts = []
            for frame_parts in action.frame_parts:
                parts.append([
                    [list(rect), int(rx), int(ry), int(ox), int(oy)]
                    for rect, rx, ry, ox, oy in frame_parts
                ])
            return {
                "name": action.name, "color": action.color,
                "selected_indices": list(action.selected_indices),
                "composite_groups": [list(g) for g in action.composite_groups],
                "frame_layouts": None if action.frame_layouts is None else [
                    [[int(i), int(x), int(y)] for i, x, y in layout]
                    for layout in action.frame_layouts
                ],
                "frame_layouts_base": action.frame_layouts_base,
                "frame_rects": [list(r) for r in action.frame_rects],
                "frame_parts": parts,
                "frame_count": len(action.frames),
                "fps": action.fps, "loop_mode": action.loop_mode,
                "padding": action.padding, "ground_enabled": action.ground_enabled,
                "ground_offset": action.ground_offset,
                "reuse_frame_settings": action.reuse_frame_settings,
                "show_anchor": action.show_anchor, "anchor": list(action.anchor),
                "frame_offsets": {str(k): list(v) for k, v in action.frame_offsets.items()},
                "frame_durations": {str(k): int(v) for k, v in action.frame_durations.items()},
                "frame_rotations": {str(k): v for k, v in action.frame_rotations.items()},
                "frame_mirror_h": sorted(action.frame_mirror_h),
                "frame_mirror_v": sorted(action.frame_mirror_v),
                "frame_rotation_pivots": {str(k): list(v) for k, v in action.frame_rotation_pivots.items()},
                "frame_quads": {str(k): list(v) for k, v in action.frame_quads.items()},
                "frame_transform_modes": {str(k): sorted(v) for k, v in action.frame_transform_modes.items()},
                "ref_frame": action.ref_frame, "ref_opacity": action.ref_opacity,
                "frame_opacity": action.frame_opacity, "bg_mode": action.bg_mode,
                "action_type": action.action_type, "move_direction": action.move_direction,
                "move_speed": action.move_speed,
            }

        if current_only and cur_idx >= 0:
            actions = [_action_data(a) if i == cur_idx else None for i, a in enumerate(self.actions)]
        else:
            actions = [_action_data(a) for a in self.actions]

        snap = {
            "current_only": current_only,
            "current_index": cur_idx,
            # 改名/改色等 current_only 快照记录焦点动作的对象 id：
            # 恢复时按 id 定位动作，避免添加/删除动作导致索引漂移后错位。
            "focus_action_id": id(cur) if current_only and cur_idx >= 0 else None,
            "actions": actions,
            "viewer": {
                "frames": [list(r) for r in viewer._frames],
                "frame_logical": {str(k): list(v) for k, v in viewer._frame_logical.items()},
                "ignored_frames": sorted(viewer._ignored_frames),
                "cleared_frames": sorted(viewer._cleared_frames),
            },
        }
        if not current_only:
            # 结构性快照携带动作对象引用：undo/redo 时复用原对象（保留各自的
            # 独立 undo 栈），而不是重建新对象把动作自己的历史清空。
            snap["action_objects"] = list(self.actions)
            # 同时携带原图引用（不拷贝，QPixmap 隐式共享）：追加图片/导入 JSON
            # 这类会改原图的操作，undo 时把原图指回追加前，redo 指回追加后。
            snap["pixmap"] = viewer._pixmap
            snap["source_images"] = list(viewer._source_images)
            # 每张原图在合成大图里的位置（拖动移位改它）：不存进快照的话，
            # 撤销"移动原图"就回不到原位
            snap["source_offsets"] = [tuple(o) for o in viewer._source_offsets]
            # 动图那两种状态也带上（引用，不复制帧）：这样"转为序列帧 / 转为动图文档"
            # 能被 Ctrl+Z 整条退回 —— 退回动图文档时操作链原样还在（见 anim_doc.py 开头）
            snap["anim_doc"] = self._anim_doc
            snap["anim_source"] = self._anim_source
        return snap

    def _apply_action_data(self, action: Action, raw: dict) -> None:
        """把快照/工程里的原始数据重灌到一个动作对象上（不动它的 undo_stack）。"""
        for attr in ("fps", "loop_mode", "padding", "ground_enabled", "ground_offset",
                     "reuse_frame_settings", "show_anchor", "ref_frame", "ref_opacity",
                     "frame_opacity", "bg_mode", "action_type", "move_direction", "move_speed"):
            if attr in raw:
                setattr(action, attr, raw[attr])
        action.name = str(raw.get("name", action.name))
        action.color = str(raw.get("color", action.color))
        action.selected_indices = [int(x) for x in raw.get("selected_indices", [])]
        action.composite_groups = [[int(x) for x in g] for g in raw.get("composite_groups", [])]
        layouts = raw.get("frame_layouts")
        action.frame_layouts = None if layouts is None else [
            [(int(i), int(x), int(y)) for i, x, y in layout] for layout in layouts
        ]
        action.frame_layouts_base = int(raw.get("frame_layouts_base", 0))
        action.frame_rects = [tuple(map(int, x)) for x in raw.get("frame_rects", [])]
        action.frame_parts = []
        for frame_parts in raw.get("frame_parts", []):
            parsed = []
            for part in frame_parts:
                if len(part) >= 5:
                    parsed.append((tuple(map(int, part[0])), int(part[1]), int(part[2]), int(part[3]), int(part[4])))
            action.frame_parts.append(parsed)
        action.anchor = tuple(float(x) for x in self._tuple2(raw.get("anchor"), (0.5, 0.5)))
        action.frame_offsets = {int(k): tuple(map(int, v)) for k, v in (raw.get("frame_offsets") or {}).items()}
        action.frame_durations = {int(k): max(1, int(v)) for k, v in (raw.get("frame_durations") or {}).items()}
        action.frame_rotations = {int(k): int(v) for k, v in (raw.get("frame_rotations") or {}).items()}
        action.frame_mirror_h = set(map(int, raw.get("frame_mirror_h", [])))
        action.frame_mirror_v = set(map(int, raw.get("frame_mirror_v", [])))
        action.frame_rotation_pivots = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_rotation_pivots") or {}).items()}
        action.frame_quads = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_quads") or {}).items()}
        action.frame_transform_modes = {int(k): set(v) for k, v in (raw.get("frame_transform_modes") or {}).items()}
        action.invalidate_transforms()

    def _restore_snapshot(self, snap: dict) -> None:
        """从轻量快照恢复工程：恢复 actions 数据，帧图从 atlas 重建。"""
        self._suppress_selection_snapshot = True
        # 停掉所有 pending debounce timer，避免 restore 后误触发 push
        if self._per_action_snap_timer is not None:
            self._per_action_snap_timer.stop()
        self._per_action_snap_text = ""
        self._snapshot_before = None
        try:
            viewer = self.image_viewer
            viewer._detect_generation += 1
            self._end_all_renames()
            current_only = snap.get("current_only", False)
            cur_idx = snap.get("current_index", -1)
            raw_list = snap.get("actions", [])

            if current_only and cur_idx >= 0:
                # 只恢复焦点动作，不动其他动作（该动作自己的 undo 栈原样保留）。
                # 优先按快照里的动作 id 定位：改名/改色后若添加/删除了其他动作，
                # 索引会漂移，按 cur_idx 会恢复到错误的动作上（别的动作被改名变色）。
                target = None
                focus_id = snap.get("focus_action_id")
                if focus_id is not None:
                    for candidate in self.actions:
                        if id(candidate) == focus_id:
                            target = candidate
                            break
                if target is None and cur_idx < len(self.actions):
                    target = self.actions[cur_idx]
                raw = raw_list[cur_idx] if cur_idx < len(raw_list) else None
                if target is not None and raw:
                    action = target
                    self._apply_action_data(action, raw)
                    viewer.crop_frames_for_action(action)
                    # 刷新左侧动作卡片（名字/颜色）
                    for ci in range(self.action_list.count()):
                        card = self.action_list.itemWidget(self.action_list.item(ci))
                        if card is not None and getattr(card, "_name", None) is not None:
                            if card.lbl_name.text() != action.name:
                                card.lbl_name.setText(action.name)
                                card._name = action.name
                    self.preview.set_action(action)
            else:
                # 全量恢复（添加/删除动作）
                self.actions.clear()
                self.action_list.clear()
                # 优先复用快照携带的动作对象：每个动作自己的独立 undo 栈原样保留，
                # undo"删除动作"后找回来的动作，它的历史还在。
                saved_objects = snap.get("action_objects")
                if saved_objects is not None and len(saved_objects) == len(raw_list):
                    for action, raw in zip(saved_objects, raw_list):
                        self._apply_action_data(action, raw)
                        if action.undo_stack is None:
                            action.undo_stack = QUndoStack(self)
                            from spritesheet.settings import undo_limit
                            action.undo_stack.setUndoLimit(undo_limit())
                        self.actions.append(action)
                        self._append_action_card(action)
                else:
                    for raw in raw_list:
                        action = Action(str(raw.get("name", "未命名动作")), str(raw.get("color", "#E91E63")))
                        action.undo_stack = QUndoStack(self)
                        from spritesheet.settings import undo_limit
                        action.undo_stack.setUndoLimit(undo_limit())
                        self._apply_action_data(action, raw)
                        self.actions.append(action)
                        self._append_action_card(action)
                viewer.set_actions(self.actions)
                viewer.set_current_action(None)
                # 若快照带原图（新快照），先还原原图再裁帧，追加图片/导入 JSON
                # 才能被完整撤销；旧快照没有该字段时保持当前原图（帧 rect 不依赖它）。
                if "pixmap" in snap:
                    viewer._pixmap = snap["pixmap"]
                    viewer._source_images = list(snap.get("source_images") or [])
                    # 偏移要跟着原图一起还原（旧快照没这个字段就留空，source_rects
                    # 会自己回退到重放拼装算法）
                    offsets = snap.get("source_offsets")
                    viewer._source_offsets = ([tuple(map(int, o)) for o in offsets]
                                              if offsets else [])
                vstate = snap.get("viewer") or {}
                if vstate.get("frames"):
                    viewer._frames = [tuple(map(int, r)) for r in vstate["frames"]]
                if vstate.get("frame_logical"):
                    viewer._frame_logical = {int(k): tuple(map(int, v)) for k, v in vstate["frame_logical"].items()}
                if "ignored_frames" in vstate:
                    viewer._ignored_frames = set(vstate["ignored_frames"])
                # 「被清空过」也跟着快照走：撤销要把框恢复回来，重做要再收起来
                if "cleared_frames" in vstate:
                    viewer._cleared_frames = set(vstate["cleared_frames"])
                for action in self.actions:
                    viewer.crop_frames_for_action(action)
                ai = snap.get("current_index", -1)
                action = self.actions[ai] if 0 <= ai < len(self.actions) else None
                if action is not None:
                    viewer.set_current_action(action)
                    self.action_list.setCurrentRow(ai)
                    self.preview.set_action(action)
                # 动图那两种状态跟着快照回来（"转为序列帧 / 转为动图文档"要能整条退回）。
                # 放在动作/原图都装完**之后**：界面切模式时会读 viewer 的当前状态。
                if "anim_doc" in snap or "anim_source" in snap:
                    self._anim_doc = snap.get("anim_doc")
                    self._anim_source = snap.get("anim_source")
                    self._apply_anim_doc_mode(fit=False)
            self._transform_history_state = self._capture_transform_state()
            viewer.canvas.update()
        except Exception as e:  # noqa: BLE001
            print(f"[undo] 恢复快照失败: {e}", file=sys.stderr)
        finally:
            self._suppress_selection_snapshot = False
            self._snapshot_before = None
            self._last_snapshot_state = self._capture_snapshot()
            if hasattr(self, "_history_panel"):
                self._history_panel.rebuild()

    def _push_snapshot(self, text: str, current_only: bool = True,
                       focus_action: Action | None = None,
                       forced: bool = False) -> None:
        """结构性/命名性操作开始前调用：抓 before 快照。

        focus_action：改的是非当前动作（改名/改色）时传它，记录与恢复都只落在这个动作。
        forced：**在动图文档里也要记一条全局快照**（只有"转成序列帧"用得上 —— 它要把
        文档整个换掉，得留一条能退回动图文档的记录；动图文档自己的撤销仍走操作链）。
        """
        if self._anim_doc is not None and not forced:
            # 动图文档的撤销走它自己的操作链（AnimDoc.ops）：这里**不记**快照，
            # 否则全局栈里会堆一堆"动图文档也用不上"的整帧快照（而且很吃内存）
            self._snapshot_before = None
            self._snapshot_text = ""
            return
        self._snapshot_before = self._capture_snapshot(current_only, focus_action)
        self._snapshot_text = text
        self._snapshot_current_only = current_only
        self._snapshot_focus = focus_action

    def _create_animation_action(self, name_hint: str, durations: list[int],
                                 first: int, total: int) -> Action:
        """给"一帧一张原图"的序列帧建动作：帧序 = 原图顺序、逐帧时长跟着动图。

        ① 导入与 ②→① 转换共用这一处 —— 两条路的建法必须一模一样，不然"转过去"和
        "导进来"出来的动作会长得不一样（用户口径里这俩是一回事）。
        """
        fps, overrides = fps_and_overrides(durations)
        action = Action(self._animation_action_name(name_hint), self._next_action_color())
        action.undo_stack = QUndoStack(self)
        from spritesheet.settings import undo_limit
        action.undo_stack.setUndoLimit(undo_limit())
        action.selected_indices = list(range(first, total))
        action.fps = fps
        # 逐帧时长一帧一条写清楚：fps 是整数，反推不出 80ms 这种值（12.5fps 只能取 13）
        action.frame_durations = overrides
        return action

    def _install_animation_action(self, action: Action) -> None:
        """把动作挂上界面（列表 / 卡牌 / 裁帧 / 预览）—— ① 导入与 ②→① 转换共用。"""
        viewer = self.image_viewer
        self.actions.append(action)
        viewer.set_actions(self.actions)
        self._append_action_card(action)
        # 裁剪出每一帧的像素（动作的 frames 就是在这里填上的）
        for item in self.actions:
            viewer.crop_frames_for_action(item)
        viewer.set_current_action(action)
        self.action_list.blockSignals(True)
        self.action_list.setCurrentRow(self.actions.index(action))
        self.action_list.blockSignals(False)
        self.preview.set_action(action)
        self.preview.set_source_pixmap(viewer._pixmap)

    def _remember_animation_frames(self, first: int, count: int, durations, action: Action,
                                   name: str, path: str) -> None:
        """记住"这份工程是从动图来的"（序列帧模式那条横条的根据）。"""
        self._anim_source = FramesSource(
            source_indices=list(range(first, first + count)),
            durations=[int(d) for d in durations],
            action=action, name=name, path=path,
            source_total=len(self.image_viewer._source_images))

    def _live_animation_frames(self) -> list[tuple[int, int, int, int]] | None:
        """把记下来的原图下标换算成**当前**矩形；对不上就返回 None（转换按钮据此禁用）。

        对不上有两种：那几张原图被删了（下标越界），或者工程里原图数量又变了
        （用户在别处加/删过原图）—— 那时"第 i 帧到底是哪一张"已经说不准了，
        与其猜一个，不如老老实实说不转（按钮禁用 + 写明原因）。
        """
        source = self._anim_source
        if source is None:
            return None
        rects = self.image_viewer.source_rects()
        if len(rects) != source.source_total:
            return None
        if any(i < 0 or i >= len(rects) for i in source.source_indices):
            return None
        return [tuple(int(v) for v in rects[i]) for i in source.source_indices]

    def _anim_frames_index(self) -> int:
        """序列帧模式下"现在看的是第几帧"：只有那个动图动作是当前动作时才有意义。"""
        source = self._anim_source
        if source is None or source.action is None or source.action not in self.actions:
            return 0
        if self.image_viewer.current_action is not source.action:
            return 0
        return max(0, min(int(self.preview._frame_index), max(0, source.count - 1)))

    def _push_snapshot_done(self) -> None:
        """结构性/命名性操作完成后调用：push snapshot command。"""
        if getattr(self, "_suppress_selection_snapshot", False):
            self._snapshot_before = None
            self._snapshot_text = ""
            return
        before = getattr(self, "_snapshot_before", None)
        if before is None:
            return
        current_only = getattr(self, "_snapshot_current_only", True)
        focus = getattr(self, "_snapshot_focus", None)
        after = self._capture_snapshot(current_only, focus)
        if before != after:
            if focus is not None and current_only and focus.undo_stack is not None:
                # 改的是别的动作：记到那个动作自己的栈里
                stack = focus.undo_stack
            elif current_only:
                stack = self._active_stack()
            else:
                stack = self._undo_stack
            stack.push(_SnapshotCommand(self, before, after, self._snapshot_text))
        self._snapshot_before = None
        self._snapshot_text = ""
        self._snapshot_focus = None
        # 结构性操作会改变 actions 列表/当前动作：快照基线同步到最新，
        # 避免后续动作内记录用错 before。
        self._last_snapshot_state = self._capture_snapshot()

    @staticmethod
    def _tuple2(value, default=(0, 0)) -> tuple:
        value = value if isinstance(value, (list, tuple)) else default
        return tuple(value[:2]) if len(value) >= 2 else tuple(default)

    def _project_manifest(self) -> tuple[dict, dict[str, bytes]]:
        """Collect the complete in-memory document into manifest + PNG sections."""
        sections: dict[str, bytes] = {}
        viewer = self.image_viewer
        if self._anim_doc is not None:
            # ② 动图文档走自己那一套：存 originals + 操作链（理由见 ui/anim_doc.py 开头）
            return self._anim_doc_manifest()

        def add_pixmap(name: str, pm: QPixmap | None) -> str | None:
            payload = _pixmap_bytes(pm)
            if not payload:
                return None
            sections[name] = payload
            return name

        combined_id = add_pixmap("images/combined.png", viewer._pixmap)
        source_ids = []
        for index, pm in enumerate(viewer._source_images):
            name = add_pixmap(f"images/source_{index:04d}.png", pm)
            if name:
                source_ids.append(name)

        actions = []
        for ai, action in enumerate(self.actions):
            frame_ids = []
            for fi, frame in enumerate(action.frames):
                name = add_pixmap(f"actions/{ai:04d}/frame_{fi:04d}.png", frame)
                frame_ids.append(name)
            parts = []
            for frame_parts in action.frame_parts:
                parts.append([
                    [list(rect), int(rx), int(ry), int(ox), int(oy)]
                    for rect, rx, ry, ox, oy in frame_parts
                ])
            actions.append({
                "name": action.name, "color": action.color,
                "selected_indices": list(action.selected_indices),
                "composite_groups": [list(group) for group in action.composite_groups],
                "frame_layouts": None if action.frame_layouts is None else [
                    [[int(i), int(x), int(y)] for i, x, y in layout]
                    for layout in action.frame_layouts
                ],
                "frame_layouts_base": action.frame_layouts_base,
                "frame_rects": [list(rect) for rect in action.frame_rects],
                "frame_parts": parts, "frame_sections": frame_ids,
                "fps": action.fps, "loop_mode": action.loop_mode,
                "padding": action.padding, "ground_enabled": action.ground_enabled,
                "ground_offset": action.ground_offset,
                "reuse_frame_settings": action.reuse_frame_settings,
                "show_anchor": action.show_anchor, "anchor": list(action.anchor),
                "frame_offsets": {str(k): list(v) for k, v in action.frame_offsets.items()},
                "frame_durations": {str(k): int(v) for k, v in action.frame_durations.items()},
                "frame_rotations": {str(k): v for k, v in action.frame_rotations.items()},
                "frame_mirror_h": sorted(action.frame_mirror_h),
                "frame_mirror_v": sorted(action.frame_mirror_v),
                "frame_rotation_pivots": {str(k): list(v) for k, v in action.frame_rotation_pivots.items()},
                "frame_quads": {str(k): list(v) for k, v in action.frame_quads.items()},
                "frame_transform_modes": {str(k): sorted(v) for k, v in action.frame_transform_modes.items()},
                "ref_frame": action.ref_frame, "ref_opacity": action.ref_opacity,
                "frame_opacity": action.frame_opacity, "bg_mode": action.bg_mode,
                "action_type": action.action_type, "move_direction": action.move_direction,
                "move_speed": action.move_speed,
            })

        thumb = None
        current_action_index = self.actions.index(viewer.current_action) if viewer.current_action in self.actions else -1
        # 缩略图受「软件设置 → 工程文件显示缩略图」控制：关掉时整个区段都不写，
        # 资源管理器就会回退到文件类型图标（SSTool.ico）。
        if show_project_thumbnail():
            if viewer.current_action and viewer.current_action.frames:
                index = min(self.preview._frame_index, len(viewer.current_action.frames) - 1)
                thumb = add_pixmap("thumbnail.png", action_frame_pixmap(viewer.current_action, index))
            if thumb is None:
                thumb = add_pixmap("thumbnail.png", viewer._pixmap)
        # 工程文件缩略图：正方形画布 + 灰色棋盘格铺底 + 内容居中 + 黑白粉描边 + 右下角标。
        # .sstool 以这张 PNG 开头，资源管理器（配好图片缩略图处理器后）直接显示它。
        if thumb and "thumbnail.png" in sections:
            preview_pm = _pixmap_from_bytes(sections["thumbnail.png"])
            badge = QPixmap(str(_APP_ROOT / "工程图标.png"))
            if not preview_pm.isNull():
                canvas = QPixmap(_THUMB_SIZE, _THUMB_SIZE)
                painter = QPainter(canvas)
                # ① 铺底：灰色棋盘格 —— 内容透明或装不满时由它兜底
                paint_checkerboard(painter, canvas.rect(), _THUMB_CHECKER_CELL)
                # ② 内容：等比装进「描边内缘再留白」的方框，居中
                stroke_total = sum(width for _c, width in _THUMB_STROKE)
                avail = max(1, _THUMB_SIZE - 2 * (stroke_total + _THUMB_PAD))
                scaled = preview_pm.scaled(
                    avail, avail,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    # 放大用最近邻（精灵图多为像素画，平滑会糊），缩小才用平滑
                    Qt.TransformationMode.FastTransformation
                    if max(preview_pm.width(), preview_pm.height()) < avail
                    else Qt.TransformationMode.SmoothTransformation,
                )
                painter.drawPixmap(
                    (_THUMB_SIZE - scaled.width()) // 2,
                    (_THUMB_SIZE - scaled.height()) // 2,
                    scaled,
                )
                # ③ 描边：黑 → 白 → 粉 三层同心，由外到内
                offset = 0.0
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for color, width in _THUMB_STROKE:
                    pen = QPen(QColor(color))
                    pen.setWidthF(float(width))
                    painter.setPen(pen)
                    half = width / 2.0
                    side_f = _THUMB_SIZE - 2 * (offset + half)
                    painter.drawRect(QRectF(offset + half, offset + half, side_f, side_f))
                    offset += width
                # ④ 角标：右下角，压在描边内缘里
                if not badge.isNull():
                    side = max(16, _THUMB_SIZE // _THUMB_BADGE_DIV)
                    badge = badge.scaled(
                        side, side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    margin = int(stroke_total) + max(2, side // 10)
                    painter.drawPixmap(
                        _THUMB_SIZE - badge.width() - margin,
                        _THUMB_SIZE - badge.height() - margin,
                        badge,
                    )
                painter.end()
                sections["thumbnail.png"] = _pixmap_bytes(canvas)
        manifest = {
            "format": "spritesheet-tool-project", "version": 1,
            "app_version": __version__, "combined_image": combined_id,
            "source_images": source_ids, "thumbnail": thumb,
            "viewer": {
                "frames": [list(x) for x in viewer._frames],
                "frame_logical": {str(k): list(v) for k, v in viewer._frame_logical.items()},
                "ignored_frames": sorted(viewer._ignored_frames),
                "cleared_frames": sorted(viewer._cleared_frames),
                "zoom": viewer._zoom, "offset": [viewer._offset.x(), viewer._offset.y()],
                "bg_mode": viewer._bg_mode, "compose_mode": viewer.compose_mode,
                "manual_compose": viewer._manual_compose,
                # 每张原图在合成大图里的位置（拖动移位改的是它）：存进工程，
                # 下次打开才是你摆好的样子
                "source_offsets": [list(o) for o in viewer._source_offsets],
            },
            "preview": {
                "action_index": current_action_index,
                "frame_index": self.preview._frame_index,
                "canvas_zoom": self.preview.canvas._zoom,
                "canvas_offset": [self.preview.canvas._offset.x(), self.preview.canvas._offset.y()],
                "fit": self.preview.canvas._fit,
            },
            "actions": actions,
        }
        if self._anim_source is not None:
            # 序列帧那份"从动图来"的记忆（那条 gif 横条 + 「转为动图文档」）：不存的话，
            # 工程存了再打开这条横条就没了，看着像功能丢了。动作对象存不下，
            # 记它的下标 + 名字，打开时按这两样重新认回来。
            source = self._anim_source
            index = self.actions.index(source.action) if source.action in self.actions else -1
            manifest["anim_source"] = frames_source_payload(source, index)
        return manifest, sections

    def _set_project_title(self) -> None:
        name = self._project_name or "未命名工程"
        marker = " *" if self._project_dirty else ""
        self.setWindowTitle(f"{name}{marker} - {__app_name__} v{__version__}")

    def _mark_project_dirty(self) -> None:
        if self._project_dirty:
            return
        self._project_dirty = True
        self._refresh_tab_label()
        self._set_project_title()

    # ---- 标签页：多工程 ----

    def _tab_name_for_path(self, path: Path | None) -> str:
        return Path(path).stem if path else "未命名工程"

    @staticmethod
    def _ui_interactive() -> bool:
        """当前是否在"有人看着"的界面里跑（offscreen = 回归脚本）。"""
        return os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"

    def _refresh_tab_label(self) -> None:
        """把当前文档的名字与脏标记同步到标签上。"""
        index = self.tabs.current_index()
        if index < 0:
            return
        self.tabs.set_name(index, self._project_name or "未命名工程")
        self.tabs.set_dirty(index, self._project_dirty)
        self.tabs.set_tooltip(index, str(self._project_path) if self._project_path else "尚未保存")

    def _capture_document(self) -> dict:
        """把当前文档状态**按引用**装进一个 dict（不做序列化、不复制像素）。

        为什么不用「切换标签时存一次工程文件」：实测 `_project_manifest()` 对一张
        3.8MB 素材要 **2.1 秒**（原图要重新编码成 PNG），切一次卡两秒没法用。
        文档本来就是内存里的对象，换标签只需要把引用换过去，再把几个控件刷新一遍。
        """
        v = self.image_viewer
        return {
            "actions": list(self.actions),
            "pixmap": v._pixmap,
            "source_images": list(v._source_images),
            "source_offsets": [tuple(o) for o in v._source_offsets],
            "frames": list(v._frames),
            "frame_logical": dict(v._frame_logical),
            "ignored_frames": set(v._ignored_frames),
            "cleared_frames": set(v._cleared_frames),
            # 标线跟着标签页走（每条 ["h"|"v", 图片坐标]，**拷贝一份**：
            # 存引用的话在另一个标签页里拖标线会改到这份文档的状态）
            "guides": [list(g) for g in v._guides],
            # 完美像素（像素画视图）也跟标签页走：只切视图，不进工程文件
            # （和标线同一口径 —— 它决定的是"怎么看"，不是"图上有什么"）
            "pixel_perfect": v.pixel_perfect,
            "zoom": v._zoom,
            "offset": QPointF(v._offset),
            "bg_mode": v._bg_mode,
            "compose_mode": v.compose_mode,
            "manual_compose": v._manual_compose,
            "current_action": v.current_action,
            "preview_action": self.preview._action,
            "preview_frame_index": self.preview._frame_index,
            "preview_zoom": self.preview.canvas._zoom,
            "preview_offset": QPointF(self.preview.canvas._offset),
            "preview_fit": self.preview.canvas._fit,
            "project_path": self._project_path,
            "project_json_path": self._project_json_path,
            "project_dirty": self._project_dirty,
            "project_name": self._project_name,
            "name_custom": self._name_custom,
            "undo_stack": self._undo_stack,
            "transform_state": self._transform_history_state,
            # ② 动图文档：整份数据（帧 + 操作链）跟着标签页走。
            # **按引用**存，跟 pixmap/source_images 一个道理：切标签页不能复制 N 帧像素。
            "anim_doc": self._anim_doc,
            "anim_source": self._anim_source,
        }

    @staticmethod
    def _blank_document() -> dict:
        """空白文档状态（新建标签页用），字段与 _capture_document 一致。"""
        return {
            "actions": [],
            "pixmap": None,
            "source_images": [],
            "source_offsets": [],
            "frames": [],
            "frame_logical": {},
            "ignored_frames": set(),
            "cleared_frames": set(),
            "guides": [],
            "pixel_perfect": False,
            "zoom": 1.0,
            "offset": QPointF(0, 0),
            "bg_mode": "checker",
            "compose_mode": False,
            "manual_compose": False,
            "current_action": None,
            "preview_action": None,
            "preview_frame_index": 0,
            "preview_zoom": 1.0,
            "preview_offset": QPointF(0, 0),
            "preview_fit": True,
            "project_path": None,
            "project_json_path": None,
            "project_dirty": False,
            "project_name": "未命名工程",
            "name_custom": False,
            "undo_stack": None,
            "transform_state": None,
            "anim_doc": None,
            "anim_source": None,
        }

    def _restore_document(self, doc: dict) -> None:
        """把文档状态装回控件（与 _capture_document 对称）。

        刷新顺序照着 `_apply_sstool_project` 来：先原图区数据，再动作列表，
        最后预览区；漏掉任何一处都会出现「切回来画面还是上一个工程」。
        """
        self._end_all_renames()
        viewer = self.image_viewer
        # 切文档时先掐掉上一份文档还在跑的元素检测，否则结果会落到新文档上
        viewer._detect_generation += 1
        # "退出完美像素后重贴紧"也一样要作废：现在它会**追加新元素**，
        # 上一份画面的连通域落到新文档上就会凭空多出一堆元素（切标签页正好会撞上）
        viewer._refit_generation += 1

        self.actions = list(doc["actions"])
        viewer._pixmap = doc["pixmap"]
        viewer._source_images = list(doc["source_images"])
        viewer._source_offsets = [tuple(o) for o in (doc.get("source_offsets") or [])]
        viewer._frames = list(doc["frames"])
        viewer._frame_logical = dict(doc["frame_logical"])
        viewer._ignored_frames = set(doc.get("ignored_frames") or [])
        viewer._cleared_frames = set(doc.get("cleared_frames") or [])
        # 标线是"每个标签页各一份"的视图状态（只当参考线，不进导出/识别）
        viewer.set_guides(doc.get("guides") or [])
        # 「已忽略」小字提示同理：它是按索引记的临时视图状态，切页必须清掉，
        # 否则会拿上一个文档的索引在一张完全不同的图上找元素
        viewer.clear_ignored_hint()
        # 完美像素也跟标签页走。这里**直接置位**、不调 set_pixel_perfect：
        # 那个入口会把视图打回"100% 居中"，而这份文档存的可能是用户在模式里
        # 又放大到 3× 之后的缩放，直接还原缩放/偏移才是他要回到的视图。
        viewer.pixel_perfect = bool(doc.get("pixel_perfect"))
        viewer._pp_prev = None
        viewer._sync_pixel_perfect_button()
        viewer._zoom = float(doc["zoom"])
        viewer._offset = QPointF(doc["offset"])
        viewer._bg_mode = doc["bg_mode"]
        viewer.compose_mode = bool(doc["compose_mode"])
        viewer._manual_compose = bool(doc["manual_compose"])

        self.action_list.clear()
        for action in self.actions:
            self._append_action_card(action)
        viewer.set_actions(self.actions)

        current = doc["current_action"]
        viewer.set_current_action(current)
        if current in self.actions:
            row = self.actions.index(current)
            self.action_list.blockSignals(True)
            self.action_list.setCurrentRow(row)
            self.action_list.blockSignals(False)

        self.preview.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.preview.set_action(doc["preview_action"])
        self.preview._frame_index = int(doc["preview_frame_index"])
        if doc["preview_action"] is not None:
            self.preview._sync_frame()
        self.preview.canvas._zoom = float(doc["preview_zoom"])
        self.preview.canvas._offset = QPointF(doc["preview_offset"])
        self.preview.canvas._fit = bool(doc["preview_fit"])
        # ② 动图文档：切回这种标签页要把画布/工具条/预览都切回"单帧 + 同步全部帧"那套，
        # 切到普通工程则要原样退回来（这里一处决定，别在别处再判一遍）
        self._anim_doc = doc.get("anim_doc")
        # ① 序列帧的"从动图来"记忆也跟着标签页走（引用：同一份来源对象，不在标签间改它）
        self._anim_source = doc.get("anim_source")
        self._apply_anim_doc_mode(fit=False)

        self._project_path = doc["project_path"]
        self._project_json_path = doc.get("project_json_path")
        self._project_dirty = bool(doc["project_dirty"])
        self._project_name = doc["project_name"]
        self._name_custom = bool(doc["name_custom"])
        # 左上角 JSON 徽标跟着工程走
        self.image_viewer.set_json_badge(bool(self._project_json_path), self._project_json_path or "")

        # 撤销栈每个文档一份；菜单里的「撤销」要跟着换到新的那份上
        stack = doc["undo_stack"]
        self._undo_stack = stack if stack is not None else QUndoStack(self)
        self._transform_history_state = doc["transform_state"]
        self._transform_history_pending = None
        if self._act_undo is not None:
            try:
                self._act_undo.triggered.disconnect()
            except TypeError:
                pass
            self._act_undo.triggered.connect(self._do_undo)
        if getattr(self, "_act_redo", None) is not None:
            try:
                self._act_redo.triggered.disconnect()
            except TypeError:
                pass
            self._act_redo.triggered.connect(self._do_redo)
        if self._transform_history_state is None:
            self._transform_history_state = self._capture_transform_state()

        # 清掉 snapshot 缓存和 pending timer，避免跨文档污染
        self._snapshot_before = None
        self._snapshot_text = ""
        self._snapshot_focus = None
        if self._per_action_snap_timer is not None:
            self._per_action_snap_timer.stop()
        self._per_action_snap_text = ""
        self._last_snapshot_state = self._capture_snapshot()
        if hasattr(self, "_history_panel"):
            self._history_panel.rebuild()

        viewer.canvas.update()
        viewer._update_rulers()
        # 工具状态跟着文档走一遍校验：切到**空白**文档时画布上没图了，画笔/橡皮/吸管
        # 必须弹回去 —— 否则工具还亮着、参数条还挂着，用户点画布会莫名弹出"添加图片"
        # 的文件对话框（工具只认"画布上按住左键"这一个手势，没图时这一下会落到
        # 「点击空白画布 = 添加图片」那条老分支上）。
        # 有图的文档之间切换则保持工具不弹：和 PS 一样，切文档不换工具。
        if viewer._pixmap is None or viewer._pixmap.isNull():
            self._set_active_tool(None)
        self._refresh_tab_label()
        self._set_project_title()

    def _switch_tab(self, index: int) -> None:
        if not (0 <= index < self.tabs.count()):
            return
        current = self.tabs.current_index()
        if index == current:
            return
        # 切文档前先把还没落账的动作内改动记下来（归属当前动作自己的栈），
        # 否则 _restore_document 停掉 timer 后记录就丢了
        self._flush_pending_snapshot()
        if 0 <= current < len(self._documents):
            slot = self._documents[current]
            slot["state"] = self._capture_document()
            slot["name"] = self._project_name
            slot["dirty"] = self._project_dirty
        self.tabs.set_current(index)
        doc = self._documents[index]
        self._restore_document(doc["state"])
        doc["state"] = None            # 现在它是"活着"的那份
        self.lbl_status.setText(f"已切换到工程：{self._project_name}")

    def _new_tab(self, name: str | None = None, switch: bool = True) -> int:
        """新建一个空白工程标签页。"""
        current = self.tabs.current_index()
        # 当前文档还有没落账的改动时先记下来，别让它跟着丢在切换里
        self._flush_pending_snapshot()
        if 0 <= current < len(self._documents):
            slot = self._documents[current]
            slot["state"] = self._capture_document()
            slot["name"] = self._project_name
            slot["dirty"] = self._project_dirty
        doc = self._blank_document()
        doc["project_name"] = name or self._next_untitled_name()
        self._documents.append({"name": doc["project_name"], "dirty": False, "state": doc})
        index = self.tabs.add_tab(doc["project_name"], select=switch)
        if switch:
            self._restore_document(doc)
            self._documents[index]["state"] = None
            self.lbl_status.setText("已新建空白工程")
        self._refresh_tab_label()
        return index

    def _next_untitled_name(self) -> str:
        used = {slot["name"] for slot in self._documents}
        index = self.tabs.count() + 1
        while f"未命名工程{index}" in used:
            index += 1
        return f"未命名工程{index}"

    def _rename_tab(self, index: int, name: str) -> None:
        """双击标签重命名：只改工程在软件里的显示名，**不动磁盘上的文件名**。"""
        if not (0 <= index < len(self._documents)):
            return
        self._documents[index]["name"] = name
        if index == self.tabs.current_index():
            self._project_name = name
            self._name_custom = True
            self._refresh_tab_label()
            self._set_project_title()
        self.lbl_status.setText(f"工程已重命名为「{name}」")

    def _ask_close_tab(self, name: str) -> str:
        """关闭有改动的标签时询问。返回 "save" / "discard" / "cancel"。

        单独抽出来是为了可测：回归里替换掉它就不必去 mock 模态对话框。
        offscreen 下不弹窗（理由见 ``_ask_quit_dirty``），按"不保存"处理。
        """
        if not self._ui_interactive():
            return "discard"
        box = QMessageBox(self)
        box.setWindowTitle("关闭工程")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"「{name}」有未保存的改动。")
        box.setInformativeText("关闭前要保存吗？")
        btn_save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return "cancel"
        return "save" if clicked is btn_save else "discard"

    def _close_tab(self, index: int) -> None:
        if not (0 <= index < self.tabs.count()):
            return
        # 关闭当前文档前先把没落账的改动记进它自己的栈，再随文档一起收走
        if index == self.tabs.current_index():
            self._flush_pending_snapshot()
        is_current = index == self.tabs.current_index()
        if is_current:
            name = self._project_name
            dirty = self._project_dirty
        else:
            name = self._documents[index]["name"]
            dirty = bool(self._documents[index]["state"]["project_dirty"]) if self._documents[index]["state"] else False
        if dirty:
            choice = self._ask_close_tab(name)
            if choice == "cancel":
                return
            if choice == "save":
                if not is_current:
                    self._switch_tab(index)
                    is_current = True
                if not self._save_project():
                    return
        self._documents.pop(index)
        self.tabs.remove_tab(index)
        if self.tabs.count() == 0:
            self._new_tab(switch=True)
            return
        if is_current:
            new_index = min(index, self.tabs.count() - 1)
            self.tabs.set_current(new_index)
            doc = self._documents[new_index]
            self._restore_document(doc["state"])
            doc["state"] = None
        else:
            self._refresh_tab_label()

    def _tab_open_target(self) -> int:
        """打开工程该落到哪个标签：当前标签是"没动过的空白页"就复用它（PS 的做法），
        否则新开一个标签，避免把正在编辑的工程顶掉。"""
        current = self.tabs.current_index()
        if current < 0:
            return self._new_tab(switch=False)
        if (not self.actions and self.image_viewer._pixmap is None
                and not self._project_dirty and self._project_path is None):
            return current
        return self._new_tab(name="新工程", switch=False)

    def _save_project(self, path: str | Path | None = None) -> bool:
        target = Path(path) if path else self._project_path
        if target is None:
            return self._save_project_as()
        try:
            manifest, sections = self._project_manifest()
            write_project(target, manifest, sections)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存工程失败", str(exc))
            return False
        self._project_path = target
        self._project_dirty = False
        # 文件名跟着工程走（除非用户双击标签改过名 —— 那种情况保留用户起的名字）
        if not self._name_custom:
            self._project_name = self._tab_name_for_path(target)
        self._refresh_tab_label()
        self._set_project_title()
        self.lbl_status.setText(f"工程已保存：{target.name}")
        return True

    def _dialog_start(self, default_name: str = "") -> str:
        """文件对话框的默认位置：优先当前工程所在目录，其次上次用过的目录（没记录过
        或目录已不存在就回退到桌面），最后拼上默认文件名。"""
        if self._project_path is not None:
            folder = str(self._project_path.parent)
        else:
            folder = last_directory()
        return str(Path(folder) / default_name) if default_name else folder

    def _remember_dialog_path(self, path: str | Path | None) -> None:
        set_last_directory(path)

    def _save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 SSTOOL 工程",
            self._dialog_start(f"{self._project_name or '工程'}.sstool"),
            "SSTOOL 工程 (*.sstool)"
        )
        if not path:
            return False
        if not path.lower().endswith(".sstool"):
            path += ".sstool"
        self._remember_dialog_path(path)
        return self._save_project(path)

    def _new_project(self) -> None:
        """新建工程 —— 现在会**新开一个标签页**（原来是把当前工程清空）。

        入口在 文件 → 新建工程（Ctrl+N），也可以双击标签栏空白处。
        """
        self._new_tab()

    def _open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 SSTOOL 工程", self._dialog_start(), "SSTOOL 工程 (*.sstool)")
        if path:
            self._remember_dialog_path(path)
            self._open_project(path)

    def _open_project(self, path: str) -> bool:
        """打开工程。落在哪个标签由 ``_tab_open_target`` 决定：

        当前标签是"没动过的空白页"就直接用它，否则新开一个标签 —— 免得把正在编辑的
        工程顶掉（PS 也是这个行为）。
        """
        index = self._tab_open_target()
        was_current = index == self.tabs.current_index()
        if not was_current:
            # 先切到目标标签（`_tab_open_target` 新开的空白页）再装工程内容：
            # 否则工程会被装进"当前活文档"的控件里，连带把它已有的内容、
            # 动作以及 undo 历史一起冲掉（`_reset_transform_history` 会 clear 栈）。
            self._switch_tab(index)
        try:
            manifest, sections = read_project(path)
            self._apply_sstool_project(manifest, sections)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程失败", str(exc))
            return False
        self.tabs.set_current(index)
        self._project_path = Path(path)
        self._project_json_path = None
        self.image_viewer.set_json_badge(False)
        self._project_dirty = False
        self._project_name = self._tab_name_for_path(self._project_path)
        self._name_custom = False
        self._documents[index]["state"] = None
        self._documents[index]["name"] = self._project_name
        self._documents[index]["dirty"] = False
        self._refresh_tab_label()
        self._set_project_title()
        self.lbl_status.setText(f"工程已打开：{self._project_path.name}"
                               + ("" if was_current else "（新标签页）"))
        return True

    def _anim_doc_manifest(self) -> tuple[dict, dict[str, bytes]]:
        """动图文档 → 工程清单 + PNG sections。

        帧只存 ``originals``（导入时那批原始帧）；用户做过的抠图/画笔/翻转都是**参数**，
        以操作链存进清单 —— 存盘体积小，而且重新打开后撤销栈还是活的。
        """
        doc = self._anim_doc
        sections: dict[str, bytes] = {}
        frame_names = []
        for index, pm in enumerate(doc.originals):
            name = f"anim/frame_{index:04d}.png"
            payload = _pixmap_bytes(pm)
            if not payload:
                raise ValueError(f"动图文档第 {index + 1} 帧是空的，存不下来")
            sections[name] = payload
            frame_names.append(name)
        current = _pixmap_bytes(doc.frame())
        if current:
            # 当前帧当缩略图：.sstool 本身在资源管理器里就是一张能看的图
            sections["thumbnail.png"] = current
        manifest = {
            "format": "spritesheet-tool-project",
            "version": 2,
            "anim_doc": anim_doc_payload(doc, frame_names),
        }
        return manifest, sections

    def _apply_anim_doc_project(self, sections: dict, payload: dict) -> None:
        """工程里存的是动图文档：按操作链还原，**不**走图集那套（没有元素/动作）。"""
        frames = []
        for name in payload.get("frames") or []:
            pm = _pixmap_from_bytes(sections.get(name, b""))
            if pm.isNull():
                raise ValueError(f"动图文档缺少帧数据：{name}")
            frames.append(pm)
        doc = anim_doc_from_payload(payload, frames)
        viewer = self.image_viewer
        viewer._detect_generation += 1
        self.actions = []
        self.action_list.clear()
        viewer.set_actions([])
        viewer.set_current_action(None)
        self._anim_doc = doc
        doc.rebuild(viewer)             # originals 过一遍 ops = 用户当初看到的那一帧帧
        self._apply_anim_doc_mode(fit=True)
        self.lbl_status.setText(
            f"动图文档已打开：{doc.count} 帧 · {len(doc.ops)} 步操作（Ctrl+Z 照样能退）")

    def _safe_open_project(self, path: str) -> None:
        """Safe slot for delayed drag/drop project loading."""
        try:
            self._open_project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程失败", str(exc))

    def _apply_sstool_project(self, manifest: dict, sections: dict[str, bytes]) -> None:
        if manifest.get("format") != "spritesheet-tool-project":
            raise ValueError("不是有效的 SSTOOL 工程清单")
        payload = manifest.get("anim_doc")
        if payload:
            # ② 动图文档：先分流出去 —— 下面那套会去找 combined_image，
            # 动图文档根本没有"合成大图"这个概念（画布上就是当前那一帧）
            self._apply_anim_doc_project(sections, payload)
            return
        viewer_state = manifest.get("viewer") or {}
        viewer = self.image_viewer
        viewer._detect_generation += 1
        combined = _pixmap_from_bytes(sections.get(manifest.get("combined_image", ""), b""))
        if combined.isNull():
            raise ValueError("工程缺少有效的原图数据")
        viewer._pixmap = combined
        viewer._source_images = [
            _pixmap_from_bytes(sections[name]) for name in manifest.get("source_images", []) if name in sections
        ]
        # Older projects may only contain the merged image.  Keep cropping
        # and re-export functional by treating that image as the sole source.
        if not viewer._source_images:
            viewer._source_images = [combined]
        # 每张原图的位置：新工程有 source_offsets；老工程没有就留空，
        # source_rects() 会回退到重放拼装算法（等价于原来的行为）
        offsets = viewer_state.get("source_offsets")
        viewer._source_offsets = ([tuple(map(int, o)) for o in offsets] if offsets else [])
        viewer._frames = [tuple(map(int, x)) for x in viewer_state.get("frames", [])]
        viewer._frame_logical = {int(k): tuple(map(int, v)) for k, v in (viewer_state.get("frame_logical") or {}).items()}
        viewer._ignored_frames = set(viewer_state.get("ignored_frames") or [])
        viewer._cleared_frames = set(viewer_state.get("cleared_frames") or [])
        # 完美像素是纯视图状态、**不进工程文件**：打开工程时一律回到普通视图，
        # 否则"上一份文档开着的像素模式"会跟到新打开的文件上（标线同理，只是标线
        # 本来就没进过工程清单）。
        viewer.pixel_perfect = False
        viewer._pp_prev = None
        viewer._sync_pixel_perfect_button()
        viewer._zoom = float(viewer_state.get("zoom", 1.0))
        viewer._offset = QPointF(*self._tuple2(viewer_state.get("offset"), (0, 0)))
        viewer._bg_mode = str(viewer_state.get("bg_mode", "checker"))
        viewer.compose_mode = bool(viewer_state.get("compose_mode", False))
        viewer._manual_compose = bool(viewer_state.get("manual_compose", False))

        self._end_all_renames()
        self.actions.clear()
        self.action_list.clear()
        for raw in manifest.get("actions", []):
            action = Action(str(raw.get("name", "未命名动作")), str(raw.get("color", "#E91E63")))
            action.undo_stack = QUndoStack(self)
            from spritesheet.settings import undo_limit
            action.undo_stack.setUndoLimit(undo_limit())
            for attr in ("fps", "loop_mode", "padding", "ground_enabled", "ground_offset",
                         "reuse_frame_settings", "show_anchor", "ref_frame", "ref_opacity",
                         "frame_opacity", "bg_mode", "action_type", "move_direction", "move_speed"):
                if attr in raw:
                    setattr(action, attr, raw[attr])
            action.selected_indices = [int(x) for x in raw.get("selected_indices", [])]
            action.composite_groups = [[int(x) for x in g] for g in raw.get("composite_groups", [])]
            layouts = raw.get("frame_layouts")
            action.frame_layouts = None if layouts is None else [
                [(int(i), int(x), int(y)) for i, x, y in layout] for layout in layouts
            ]
            action.frame_layouts_base = int(raw.get("frame_layouts_base", 0))
            action.frame_rects = [tuple(map(int, x)) for x in raw.get("frame_rects", [])]
            action.frame_parts = []
            for frame_parts in raw.get("frame_parts", []):
                parsed_parts = []
                for part in frame_parts:
                    if len(part) >= 5:
                        parsed_parts.append(
                            (tuple(map(int, part[0])), int(part[1]), int(part[2]), int(part[3]), int(part[4]))
                        )
                    elif len(part) >= 3:
                        # Projects written before logical origins were added.
                        rect = tuple(map(int, part[0]))
                        parsed_parts.append((rect, int(part[1]), int(part[2]), rect[0], rect[1]))
                action.frame_parts.append(parsed_parts)
            for fi, name in enumerate(raw.get("frame_sections", [])):
                frame = _pixmap_from_bytes(sections.get(name, b""))
                if frame.isNull():
                    raise ValueError(f"动作 {action.name} 缺少帧图像")
                action.frames.append(frame)
            action.anchor = tuple(float(x) for x in self._tuple2(raw.get("anchor"), (0.5, 0.5)))
            action.frame_offsets = {int(k): tuple(map(int, v)) for k, v in (raw.get("frame_offsets") or {}).items()}
            action.frame_durations = {int(k): max(1, int(v)) for k, v in (raw.get("frame_durations") or {}).items()}
            action.frame_rotations = {int(k): int(v) for k, v in (raw.get("frame_rotations") or {}).items()}
            action.frame_mirror_h = set(map(int, raw.get("frame_mirror_h", [])))
            action.frame_mirror_v = set(map(int, raw.get("frame_mirror_v", [])))
            action.frame_rotation_pivots = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_rotation_pivots") or {}).items()}
            action.frame_quads = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_quads") or {}).items()}
            action.frame_transform_modes = {int(k): set(v) for k, v in (raw.get("frame_transform_modes") or {}).items()}
            action.invalidate_transforms()
            self.actions.append(action)
            self._append_action_card(action)

        viewer.set_actions(self.actions)
        viewer.set_current_action(None)
        preview_state = manifest.get("preview") or {}
        action_index = int(preview_state.get("action_index", -1))
        action = self.actions[action_index] if 0 <= action_index < len(self.actions) else None
        if action is not None:
            viewer.set_current_action(action)
            if 0 <= action_index < self.action_list.count():
                self.action_list.blockSignals(True)
                self.action_list.setCurrentRow(action_index)
                self.action_list.blockSignals(False)
        self.preview.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.preview.set_action(action)
        frame_index = int(preview_state.get("frame_index", 0))
        if action and action.frames:
            self.preview._frame_index = max(0, min(frame_index, len(action.frames) - 1))
            self.preview._sync_frame()
        self.preview.canvas._zoom = float(preview_state.get("canvas_zoom", self.preview.canvas._zoom))
        self.preview.canvas._offset = QPointF(*self._tuple2(preview_state.get("canvas_offset"), (0, 0)))
        self.preview.canvas._fit = bool(preview_state.get("fit", True))
        viewer.canvas.update()
        viewer._update_rulers()
        self._reset_transform_history()
        # 序列帧那份"从动图来"的记忆：动作对象是刚从清单重建的，按"下标 + 名字"认回来
        # （认不回来也只是那条横条不能"跳到第 i 帧"，转换照样能用 —— 它只要原图下标）
        self._anim_source = frames_source_from_payload(manifest.get("anim_source") or {},
                                                       self.actions)
        self._apply_anim_doc_mode(fit=False)
        self._project_path = None
        self._project_dirty = False

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 名称栏（已去掉左侧软件图标，只留软件名；版本号与名字底对齐，画在同一个控件里）
        logo_bar = QWidget()
        logo_bar.setFixedHeight(64)
        logo_bar.setStyleSheet("background-color: #1a1a1a; border-bottom: 1px solid #333;")
        logo_layout = QHBoxLayout(logo_bar)
        logo_layout.setContentsMargins(12, 5, 8, 5)
        logo_layout.setSpacing(8)
        name_label = AppNameLabel(version=f"v{__version__}")
        logo_layout.addWidget(name_label, 0, Qt.AlignmentFlag.AlignVCenter)
        logo_layout.addStretch()
        main_layout.addWidget(logo_bar)

        # 工程标签栏紧贴在名称栏下面（菜单栏 → 软件名 → 标签 → 内容 → 状态栏）。
        # 交互：单击切换 / 双击重命名 / 双击空白处新建 / × 关闭 / + 新建
        self.tabs = ProjectTabBar()
        self.tabs.tab_selected.connect(self._switch_tab)
        self.tabs.tab_close_requested.connect(self._close_tab)
        self.tabs.tab_renamed.connect(self._rename_tab)
        self.tabs.new_tab_requested.connect(lambda: self._new_tab())
        self.tabs.tab_drop_requested.connect(self._on_tab_drop)
        main_layout.addWidget(self.tabs)

        # 内容区：侧边栏 | QSplitter(原图区 | 右侧预览)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # 左侧边栏
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("background-color: #1a1a1a; border-right: 1px solid #333;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_layout.setSpacing(4)

        self.btn_add_action = QPushButton("+ 添加图片")
        self.btn_add_action.setFixedHeight(32)
        self.btn_add_action.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        self.btn_add_action.clicked.connect(self._on_add_action_clicked)
        sidebar_layout.addWidget(self.btn_add_action)

        self.action_list = QListWidget()
        self.action_list.setItemDelegate(ActionItemDelegate(self.action_list))
        self.action_list.setStyleSheet(
            "QListWidget { background: #141414; border: none; border-radius: 4px; }"
            "QListWidget::item { height: 34px; padding: 0 4px; }"
        )
        self.action_list.currentRowChanged.connect(self._on_action_selected)
        sidebar_layout.addWidget(self.action_list, 1)

        content.addWidget(sidebar)

        # 右侧：QSplitter(原图区 | 预览区)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setStyleSheet("QSplitter::handle { background: #333; } QSplitter::handle:hover { background: #E91E63; }")

        self.image_viewer = ImageViewer()
        self._tool_shortcuts: list[QAction] = []   # 工具快捷键（挂在画布上，别让它们被回收）
        self.image_viewer.image_loaded.connect(self._on_image_loaded)
        self.image_viewer.json_selected.connect(self._import_project)
        self.image_viewer.json_dropped.connect(self._import_project)
        self.image_viewer.project_dropped.connect(self._safe_open_project)
        self.image_viewer.json_dropped_current.connect(
            lambda p: self._import_project(p, target="current"))
        self.image_viewer.image_path_dropped.connect(
            lambda p: self._open_path_smart(p, target="current"))
        self.image_viewer.selection_changed.connect(self._on_selection_changed)
        self.image_viewer.selection_changed.connect(self._mark_project_dirty)
        self.image_viewer.action_performed.connect(self._on_action_performed)
        self.image_viewer.action_performed_full.connect(self._on_action_performed_full)
        self.image_viewer.about_to_perform_full.connect(self._on_before_full_action)
        self.image_viewer.frames_updated.connect(self._on_frames_updated)
        # 「点别处就弹起」：变换手柄亮着时，点了原图（元素/框选/空白/平移）就收起手柄。
        # 只用原图这一个入口，**不用 app 级事件过滤器** —— 全局过滤器会误伤预览画布上的
        # 手柄点击（悬浮动画正常、一点击就退出），这个坑踩过一次就不再来第二次。
        self.image_viewer.canvas_pressed.connect(self._exit_preview_transform)
        # 左侧竖条工具条的撤销/重做按钮：与菜单、Ctrl+Z/Ctrl+Y 走同一套处理
        # （_do_undo/_do_redo 内部用 _active_stack()，栈空时安全地什么都不做）
        self.image_viewer.undo_requested.connect(self._do_undo)
        self.image_viewer.redo_requested.connect(self._do_redo)
        # 左侧竖条「抠除背景」：打开参数浮层（点第二下等于关掉/取消）
        self.image_viewer.key_requested.connect(lambda: self._on_tool_button_toggled("key"))
        self.image_viewer.source_tool_requested.connect(self._on_source_tool_toggled)
        self.image_viewer.source_selection_changed.connect(self._on_source_selection_changed)
        self.image_viewer.source_move_started.connect(self._on_source_move_started)
        self.image_viewer.source_move_finished.connect(self._on_source_move_finished)
        # 画笔 / 橡皮 / 吸管（左侧竖条三件套 + 工具选项条）
        self.image_viewer.paint_tool_requested.connect(self._on_tool_button_toggled)
        self.image_viewer.color_requested.connect(self._pick_paint_color)
        self.image_viewer.color_picked.connect(self._on_color_picked)
        self.image_viewer.paint_stroke_started.connect(self._on_paint_stroke_started)
        self.image_viewer.paint_stroke_finished.connect(self._on_paint_stroke_finished)
        # 「把当前工具收起来」：Esc、点画布外（查看器里的 app 级过滤器判断该不该收）
        self.image_viewer.tool_dismiss_requested.connect(self._dismiss_current_tool)
        # 退出完美像素后按当前像素重贴紧元素外框（只重裁、不重排帧）
        self.image_viewer.about_to_refit_frames.connect(self._on_before_refit_frames)
        self.image_viewer.frames_refit.connect(self._on_frames_refit)
        # 双击棋盘格加图片：与工具栏"添加图片"按钮同一个入口
        self.image_viewer.add_image_requested.connect(self._open_image)
        # PS 同款工具快捷键（B/E/I、[ ]、D/X）：建在画布建好之后
        self._setup_tool_shortcuts()

        # 左列：原图区 + 下方「动画设置」横条（整段动画参数）
        left_column = QWidget()
        left_column.setObjectName("LeftColumn")
        # ID 选择器只涂容器自身，避免级联污染 image_viewer 内部子控件
        left_column.setStyleSheet("#LeftColumn { background-color: #1f1f1f; }")
        self.left_layout = QVBoxLayout(left_column)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(0)
        self.left_layout.addWidget(self.image_viewer, 1)
        self.anim_bar = AnimSettingsBar()
        self.anim_bar.set_actions(self.actions)
        self.left_layout.addWidget(self.anim_bar)
        # ② 动图文档的横条（看第几帧的滑块 + 整段变换按钮）：跟「动画设置」横条**同一个位置**
        # 二选一显示 —— 动图文档里没有动作，那条 fps/循环/锚点全都用不上。
        self.anim_doc_bar = AnimDocBar()
        self.anim_doc_bar.setVisible(False)
        self.anim_doc_bar.frame_changed.connect(self._on_anim_bar_frame_changed)
        self.anim_doc_bar.convert_requested.connect(self._on_anim_doc_convert)
        self.anim_doc_bar.transform_requested.connect(self._on_anim_doc_transform)
        self.left_layout.addWidget(self.anim_doc_bar)
        self.splitter.addWidget(left_column)

        self.preview = ActionPreviewWidget()
        self.preview.set_actions(self.actions)
        # 用户拖动预览/帧编辑之间的分隔线后，也要刷新"默认尺寸"记录
        self.preview.preview_splitter.splitterMoved.connect(
            lambda *_: QTimer.singleShot(0, self._remember_panel_sizes))
        # 导出工程（.sstool）需要完整文档数据；provider 由主窗口注入
        self.preview._project_manifest = self._project_manifest
        # 导出产物以当前标签页的工程名开头
        self.preview._project_name_provider = lambda: self._project_name
        self.preview.adjust_panel.changed.connect(self._on_transform_history_changed)
        self.preview.adjust_panel.changed.connect(self._mark_project_dirty)
        self.preview.adjust_panel.changed.connect(self._on_adjust_panel_changed)
        self.preview.adjust_panel.align_requested.connect(self._on_auto_align)
        # 面板只负责按结果改提示文字，判断"精确还是只能估算"要问主窗口（要读原图）
        self.preview.adjust_panel.align_reason_provider = self._auto_align_reason
        # 「相似帧」开关：色块的实际计算/清理由主窗口做（要看全图元素，面板拿不到）
        self.preview.adjust_panel.similar_toggled.connect(self._on_similar_toggled)
        # 「相似帧」看的是全图元素：原图上有两个以上元素就能按（不必先加动作）
        self.preview.adjust_panel.similar_enable_provider = self._similar_enable_state
        self.preview.adjust_panel.extract_enable_provider = self._extract_enable_state
        self.preview.adjust_panel.extract_requested.connect(self._on_extract)
        # 空格 = 按住拖动（**应用级**过滤）：鼠标悬停在预览上按空格就能拖，不用先点画布拿焦点。
        # 只靠画布自己的 keyPressEvent 不管用 —— 空格是发给有焦点控件的（用户实测拖不动）。
        from PyQt6.QtWidgets import QApplication
        from spritesheet.ui.space_pan import SpacePanFilter
        self._space_pan = SpacePanFilter(self._space_pan_targets, self)
        QApplication.instance().installEventFilter(self._space_pan)
        # 换动作/换帧时，抽帧窗口开着就跟着刷
        self.preview.action_changed.connect(lambda _a: self._refresh_extract_dialog())
        self.preview.canvas.transform_edit_started.connect(self._begin_transform_history)
        self.preview.canvas.transform_edit_finished.connect(self._finish_transform_history)
        self.preview.toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        # 「点别处就弹起」的三个入口：原图（信号）+ 动作列表 + 动画设置横条（局部过滤器）
        self._install_transform_exit_hooks()
        # 「动画设置」横条：当前动作切换时同步；改动后刷新预览并标脏
        self.preview.action_changed.connect(self.anim_bar.set_action)
        self.anim_bar.changed.connect(self.preview._on_adjust_changed)
        self.anim_bar.changed.connect(self._mark_project_dirty)
        self.anim_bar.changed.connect(self._on_anim_bar_changed)
        # 底部横条「变换（全部帧）」模式 → 预览画布（作用于全部帧）
        self.anim_bar.mode_requested.connect(self.preview.adjust_panel.enter_transform_mode_all)
        # 画布变换模式变化（进入/退出/点空白）→ 同步底部横条按钮；
        # 传入发起方 owner，让两套变换工具按钮各自独立点亮/弹回
        self.preview.canvas.transform_mode_changed.connect(
            lambda m, c=self.preview.canvas: self.anim_bar.set_mode(
                m, owner=getattr(c, "_transform_owner", "")))
        # 当前帧切换 → 底部横条「微调（全部帧）」以当前帧偏移为起点
        self.preview.frame_changed.connect(self.anim_bar.set_current_frame)
        self.preview.frame_changed.connect(self._on_frame_changed)
        self.preview.canvas.frame_offset_dragged.connect(self._on_frame_offset_dragged)
        self.image_viewer._toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        self.image_viewer._toolbar.compose_toggled.connect(lambda _checked: self._mark_project_dirty())
        self.image_viewer._toolbar.history_requested.connect(self._open_history)
        # 程序退出（含未走 close 的路径）也要收尾原图区与更新检查线程，
        # 否则线程/过滤器会悬空
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_update_thread)
            app.aboutToQuit.connect(self.image_viewer.shutdown)
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([600, 320])

        content.addWidget(self.splitter, 1)
        main_layout.addLayout(content, 1)

        # 状态栏
        self.lbl_status = QLabel(f"{__app_name__} v{__version__}  |  重构版")
        self.lbl_status.setStyleSheet("color: #888; font-size: 11px; padding: 2px 8px; background: #1a1a1a; border-top: 1px solid #333;")
        main_layout.addWidget(self.lbl_status)

        # ---- 最大化时脱出来的三个悬浮窗 ----
        # 需求：窗口只有"默认大小"和"最大化"两种。默认大小时布局完全不变；
        # 最大化时把三块区域脱出来变成**独立悬浮窗**，而且**尺寸/比例必须和默认窗口里
        # 一模一样**（不是重新算一个大小）—— 内部按钮因此不会被拉乱。
        #   ① 动作编辑（原图区下方横条）
        #   ② 帧编辑（标题 + 参数面板 + 原图信息 + 导出）
        #   ③ 预览窗口（画布 + 播放控制条 + 缩略图）
        self._panels_floating = False
        self._float_records: list[dict] = []
        # 预览悬浮窗默认尺寸 = 画布方形再缩 30%（用户："有点大了，再缩小 30%"）
        self.PREVIEW_FLOAT_SCALE = 0.7
        # 四个独立窗口统一 1 像素灰色描边（用户要求）
        self.FLOAT_BORDER_COLOR = "#555555"
        # 无系统标题栏的圆角卡片（动作编辑 / 帧编辑 用；创建一次，反复复用）
        self._float_cards: dict[str, _FloatCard] = {}
        self._history_card: _FloatCard | None = None
        # 预览窗保留系统标题栏（用户说"预览窗口没问题"），只把内容做成圆角
        self.preview.preview_box.setObjectName("PreviewFloatBox")
        self.preview.preview_box.setStyleSheet(
            "#PreviewFloatBox { background: #1f1f1f; border: none; }"
        )
        # ★ "默认窗口"里三块各自的尺寸：最大化脱出时**按这个尺寸**做悬浮窗。
        # 必须在窗口还没最大化时记（最大化时 Qt 会先按新窗口重排一次布局，
        # 那时量到的就不是默认尺寸了）。见 _remember_panel_sizes。
        self._panel_sizes_normal: dict[str, QSize] = {}
        # 去抖计时器：布局稳定下来之后再记尺寸（resizeEvent 里量到的是重排前的旧值）
        self._panel_size_timer = QTimer(self)
        self._panel_size_timer.setSingleShot(True)
        self._panel_size_timer.setInterval(250)
        self._panel_size_timer.timeout.connect(self._remember_panel_sizes)

    def _schedule_panel_size_memory(self) -> None:
        self._panel_size_timer.start()

    def _panel_widgets(self):
        pv = self.preview
        return (("动作编辑", self.anim_bar),
                ("预览", pv.preview_box),
                ("帧编辑", pv.edit_box))

    def _preview_float_size(self, remembered: QSize) -> QSize:
        """预览悬浮窗的**默认**尺寸 = 画布方形（用户要求"默认方形"）。

        = 宽取默认窗口里的宽度 W，高 = W + 控制条 + 缩略图 + 间距，
        这样刚脱出来时中间那块画布是 W×W 的正方形；之后用户可以拖边框随意改大小。
        用户后来反馈"有点大" → 默认再缩 30%（PREVIEW_FLOAT_SCALE）。
        """
        pv = self.preview
        extra = (pv.ctrl_widget.height() + pv.thumb_list.height()
                 + 2 * pv.preview_box.layout().spacing())
        side = max(200, int(round(remembered.width() * self.PREVIEW_FLOAT_SCALE)))
        return QSize(side, side + max(0, extra))

    def _remember_panel_sizes(self) -> None:
        """窗口处于默认大小时，记下三块区域的尺寸（悬浮窗要沿用它们）。"""
        if self._panels_floating or self.isMaximized() or self.isFullScreen():
            return
        if not hasattr(self, "preview"):
            return
        self._panel_sizes_normal = {
            name: QSize(max(1, w.width()), max(1, w.height()))
            for name, w in self._panel_widgets() if w.width() > 1 and w.height() > 1
        }

    def _float_targets(self):
        """(标题, 控件, 塞回原布局的办法)。塞回时要保序，并恢复分隔线比例。"""
        pv = self.preview
        return (
            ("动作编辑", self.anim_bar,
             lambda w: self.left_layout.insertWidget(self._anim_bar_index, w)),
            ("预览", pv.preview_box,
             lambda w: (pv.preview_splitter.insertWidget(0, w),
                        pv.preview_splitter.setSizes(self._pv_sizes_normal))),
            ("帧编辑", pv.edit_box,
             lambda w: (pv.preview_splitter.insertWidget(1, w),
                        pv.preview_splitter.setSizes(self._pv_sizes_normal))),
        )

    # 历史记录悬浮卡片的默认尺寸（固定大小，与其它三块一致）
    HISTORY_CARD_SIZE = QSize(300, 520)

    def _ensure_history_card(self) -> "_FloatCard":
        """历史记录始终是**独立悬浮窗**（默认窗口下也不融进主窗口），与其它三块同款卡片。"""
        if self._history_card is None:
            card = _FloatCard("历史记录", self)
            card.set_content(self._history_panel)
            card.setFixedSize(self.HISTORY_CARD_SIZE.width() + 2,
                              self.HISTORY_CARD_SIZE.height() + card.content_parent_padding())
            card.move(self.x() + max(0, self.width() - self.HISTORY_CARD_SIZE.width() - 40),
                      self.y() + 80)
            self._history_card = card
        return self._history_card

    def _float_history_card(self) -> None:
        """最大化时：历史记录卡片如果之前显示着就保持在前面，否则保持隐藏。"""
        card = self._ensure_history_card()
        if card.isVisible():
            card.raise_()

    def _float_panels(self) -> None:
        """最大化：三块区域脱出成独立悬浮窗，尺寸 = 默认窗口里的原始尺寸。"""
        if self._panels_floating:
            return
        pv = self.preview
        # 先记下"默认窗口"里的尺寸、屏幕位置和还原办法
        self._anim_bar_index = max(0, self.left_layout.indexOf(self.anim_bar))
        self._pv_sizes_normal = pv.preview_splitter.sizes()
        self._splitter_sizes_normal = self.splitter.sizes()
        records = []
        for title, w, put_back in self._float_targets():
            remembered = self._panel_sizes_normal.get(title) or QSize(w.width(), w.height())
            is_preview = (w is pv.preview_box)
            records.append({
                "w": w,
                "title": title,
                # ★ 尺寸沿用"默认窗口"里记下的那一份；预览窗按"画布方形"给默认尺寸
                "size": self._preview_float_size(remembered) if is_preview else remembered,
                # ★ 四块都固定大小（用户要求"小窗口的大小是要固定"）
                "fixed": True,
                # 四块都用同样的无边框圆角卡片（自绘标题 + ✕ + 1px 灰描边）
                "is_preview": is_preview,
                "card": self._float_cards.get(title),
                "pos": w.mapToGlobal(QPoint(0, 0)),
                "parent": w.parentWidget(),
                "put_back": put_back,
            })
        self._float_records = records
        self._panels_floating = True

        # 卡片自己已有标题栏：两块面板内部的标题条重复了，悬浮时隐藏，还原时恢复
        self.anim_bar._bar_title.setVisible(False)
        pv.panel_title.setVisible(False)

        # 三块都飞走后，预览整块隐藏，腾出来的宽高全给原图区
        pv.hide()
        self.splitter.setSizes([max(1, sum(self._splitter_sizes_normal)), 0])

        # 历史记录：最大化时脱出成同样的无边框圆角卡片（显示在主窗口前面）
        self._float_history_card()
        self._sync_history_button()

        # 按默认布局的相对位置排列悬浮卡片：动作编辑在左，预览在右上，帧编辑在右下
        margin = 20
        avail_w = self.width()
        avail_h = self.height()
        # 先算每块卡片的最终尺寸
        sized = {}
        for rec in records:
            title = rec["title"]
            w = rec["w"]
            if rec["card"] is None:
                card = _FloatCard(title, self, resizable=rec["is_preview"])
                self._float_cards[title] = card
                rec["card"] = card
            rec["card"].set_content(w)
            target = rec["card"]
            size = rec["size"]
            if rec["card"] is not None:
                size = QSize(size.width() + 2, size.height() + rec["card"].content_parent_padding())
            if rec["is_preview"]:
                target.setMinimumSize(200, 170)
                target.setMaximumSize(16777215, 16777215)
                target.resize(size)
            else:
                target.setFixedSize(size)
            sized[title] = (target, size)
        # 按用户要求摆位置：
        #  - 动作编辑：底部水平居中
        #  - 预览：右上角，宽固定 250px
        #  - 帧编辑：右下角，和预览同列垂直对齐
        anim_t, anim_s = sized["动作编辑"]
        pv_t, pv_s = sized["预览"]
        fe_t, fe_s = sized["帧编辑"]
        # 动作编辑：底部水平居中
        anim_x = (avail_w - anim_s.width()) // 2
        anim_y = avail_h - anim_s.height() - margin
        anim_t.move(anim_x, anim_y)
        # 预览：右上角，宽固定 250px；和帧编辑在右边一列，中间隔 100px，整列垂直居中
        pv_w = 250
        pv_t.resize(pv_w, pv_t.height())
        pv_h = pv_t.height()
        gap = 100
        col_h = pv_h + gap + fe_s.height()
        col_y = (avail_h - col_h) // 2
        pv_x = avail_w - pv_w - margin
        pv_y = col_y
        pv_t.move(pv_x, pv_y)
        # 帧编辑：右下角，和预览同列（右对齐）
        fe_x = avail_w - fe_s.width() - margin
        fe_y = col_y + pv_h + gap
        fe_t.move(fe_x, fe_y)
        # 原图悬浮工具栏：顶部水平居中（和底部动作编辑居中对齐）
        # 延迟一帧再移动，确保 canvas 宽度已经更新到最大化后的尺寸
        def _center_toolbar():
            toolbar = getattr(self.image_viewer, "_toolbar", None)
            if toolbar is None:
                return
            canvas = toolbar.parentWidget()
            if canvas is None:
                return
            toolbar.move((canvas.width() - toolbar.width()) // 2, 6)
        QTimer.singleShot(0, _center_toolbar)
        # 显示
        for title, (target, _s) in sized.items():
            target.show()
            target.raise_()

    def _dock_panels(self) -> None:
        """还原窗口：三块按原顺序塞回原布局，并恢复默认窗口时的比例。"""
        if not self._panels_floating:
            return
        self._panels_floating = False
        for rec in self._float_records:
            w = rec["w"]
            if rec.get("card") is not None:
                # 先 hide 卡片再取出面板（卡片只是外壳，面板要回原布局）
                rec["card"].take_content()
                rec["card"].hide()
            w.hide()
            w.setWindowFlags(Qt.WindowType.Widget)
            w.setMinimumSize(0, 0)
            w.setMaximumSize(16777215, 16777215)
            w.setParent(rec["parent"])          # 回原父容器（QLayout 会自动接管）
            rec["put_back"](w)                  # 再插回原来的位置（保序）
            w.show()
        self._float_records = []
        # 回到停靠布局：面板内部标题条恢复显示
        self.anim_bar._bar_title.setVisible(True)
        self.preview.panel_title.setVisible(True)
        self.preview.show()
        if self._splitter_sizes_normal:
            self.splitter.setSizes(self._splitter_sizes_normal)

    def changeEvent(self, event) -> None:
        # 窗口只有两种状态：默认大小（布局照旧）/ 最大化（三块脱出成悬浮窗）
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMaximized():
                QTimer.singleShot(0, self._float_panels)
            elif self._panels_floating:
                QTimer.singleShot(0, self._dock_panels)
        super().changeEvent(event)

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        menubar.setStyleSheet("QMenuBar { background: #1a1a1a; color: #ddd; font-size: 12px; } QMenuBar::item:selected { background: #E91E63; }")

        # 布局：文件在左、编辑在右。
        # 文件菜单分组：① 素材（添加图片 / 导入 JSON 工程）—— 按之前的要求放在最上面
        #              ② 工程（新建 / 打开 / 保存 / 另存为）
        #              ③ 退出
        # 「检查更新」已移进 编辑 → 软件设置…（软件级开关和更新放一起）。
        file_menu = menubar.addMenu("文件")
        act_open = QAction("添加图片", self)
        act_open.triggered.connect(self._open_image)
        file_menu.addAction(act_open)
        act_import = QAction("导入 JSON 工程…", self)
        act_import.setShortcut("Ctrl+I")
        act_import.triggered.connect(self._import_json_dialog)
        file_menu.addAction(act_import)
        file_menu.addSeparator()
        act_new = QAction("新建工程", self)
        act_new.setShortcut(QKeySequence.StandardKey.New)
        act_new.triggered.connect(self._new_project)
        file_menu.addAction(act_new)
        act_open_project = QAction("打开工程…", self)
        act_open_project.setShortcut(QKeySequence.StandardKey.Open)
        act_open_project.triggered.connect(self._open_project_dialog)
        file_menu.addAction(act_open_project)
        act_save = QAction("保存工程", self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(lambda: self._save_project())
        file_menu.addAction(act_save)
        act_save_as = QAction("工程另存为…", self)
        act_save_as.triggered.connect(self._save_project_as)
        file_menu.addAction(act_save_as)
        file_menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        edit_menu = menubar.addMenu("编辑")
        act_undo = QAction("撤销", self)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        act_undo.triggered.connect(self._do_undo)
        edit_menu.addAction(act_undo)
        # 撤销栈是「每个文档一份」，切标签时要把它接到当前文档的那份上
        self._act_undo = act_undo

        act_redo = QAction("重做", self)
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_redo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        act_redo.triggered.connect(self._do_redo)
        edit_menu.addAction(act_redo)
        self._act_redo = act_redo

        act_history = QAction("历史记录面板", self)
        act_history.setShortcut(QKeySequence("Ctrl+H"))
        act_history.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        act_history.setCheckable(True)
        act_history.setChecked(False)
        act_history.triggered.connect(self._open_history)
        edit_menu.addAction(act_history)

        edit_menu.addSeparator()
        act_settings = QAction("软件设置…", self)
        act_settings.triggered.connect(self._open_settings)
        edit_menu.addAction(act_settings)

        # 帮助菜单：关于（GPL 声明 + 第三方组件）
        help_menu = menubar.addMenu("帮助")
        act_about = QAction("关于 SpriteSheetTool", self)
        act_about.triggered.connect(self._open_about)
        help_menu.addAction(act_about)
        # 启动后延迟自动检查一次更新（不阻塞界面；失败时静默）
        self._update_thread: UpdateCheckThread | None = None
        self._update_checked = False
        QTimer.singleShot(2500, self._check_update_auto)

    def _check_update_auto(self):
        """启动自动检查：仅检查一次，无更新/失败都静默，不打扰用户。"""
        if self._update_checked:
            return
        self._start_update_check(notify_no_update=False)

    def _check_update_manual(self):
        """手动检查：始终弹窗反馈结果（入口在 编辑 → 软件设置…）。"""
        self._start_update_check(notify_no_update=True)

    def _open_settings(self) -> None:
        """编辑 → 软件设置…：工程缩略图开关 + 辅助线颜色/网格门槛 + 检查更新。"""
        dialog = SettingsDialog(self, check_update=self._check_update_manual)
        # 辅助线颜色/网格门槛是"看着调"的东西：改完立刻重画，不用关弹窗再刷新
        dialog.view_colors_changed.connect(self._on_view_colors_changed)
        dialog.exec()

    def _on_view_colors_changed(self) -> None:
        """设置里改了标线/网格的颜色或门槛：查看器重新读一遍设置并重绘。

        查看器全软件只有一个（标签页靠快照换数据），所以重绘一处就全生效。
        """
        viewer = getattr(self, "image_viewer", None)
        if viewer is None:
            return
        viewer.reload_view_colors()
        viewer.canvas.update()

    def _history_shown(self) -> bool:
        """历史记录现在是"显示着"的吗（始终是独立悬浮卡片）。"""
        card = getattr(self, "_history_card", None)
        return bool(card is not None and card.isVisible())

    def _open_history(self) -> None:
        """工具栏"历史"/编辑菜单：显示/隐藏历史记录卡片。"""
        self._show_history(not self._history_shown())

    def _show_history(self, show: bool) -> None:
        """显示/隐藏历史记录。它始终是**独立悬浮窗**，不融进主窗口。"""
        card = self._ensure_history_card()
        card.setVisible(bool(show))
        if show:
            card.raise_()
        self._sync_history_button()

    # ---------------------------------------------------------------- 抠除背景

    def _on_source_move_started(self) -> None:
        """拖动原图开始：先抓 before 快照。

        没真的动成（比如被画布边界夹住了）时 ``_push_snapshot_done`` 会自己判等跳过，
        不会白记一条撤销 —— 所以这里不用提前判断，拖动结束统一收尾就行。
        """
        self._push_snapshot("移动原图", current_only=False)

    def _on_source_move_finished(self, changed: bool = True) -> None:
        """拖动结束：重裁各动作的帧、刷新预览、记一条全局撤销、标脏。

        拖动过程中只改几何（位置与帧矩形），每个动作缓存的帧图/变换统一在这里重算 ——
        帧一多的话，跟着每次鼠标移动重裁会明显卡顿。
        changed=False 表示这一把被画布边界夹住、一点没动：那就什么都不用做，
        尤其别说"已移动"（用户拖到边上没动，状态栏不该撒谎）。
        """
        if not changed:
            return
        viewer = self.image_viewer
        if self.actions:
            for action in self.actions:
                action.invalidate_transforms()
            for action in self.actions:
                viewer.crop_frames_for_action(action)
            self._on_frames_updated()
        viewer.canvas.update()
        viewer._update_rulers()
        self.preview.set_source_pixmap(viewer._pixmap)
        self._push_snapshot_done()
        self._mark_project_dirty()
        picked = viewer.selected_sources()
        rects = viewer.source_rects()
        places = "、".join(f"第 {i + 1} 张 → ({rects[i][0]}, {rects[i][1]})"
                           for i in picked[:3] if i < len(rects))
        self.lbl_status.setText(
            "已移动原图：" + places + ("…" if len(picked) > 3 else "")
            + ("（帧跟着一起走；Ctrl+Z 可撤销）" if self.actions else "")
        )

    def _on_source_selection_changed(self) -> None:
        """选中项变了：状态栏说清现在选的是哪几张（用户据此判断下一步抠哪张）。"""
        picked = self.image_viewer.selected_sources()
        if not picked:
            self.lbl_status.setText("已取消选择")
        elif len(picked) == 1:
            self.lbl_status.setText(f"已选中第 {picked[0] + 1} 张原图")
        else:
            numbers = "、".join(str(i + 1) for i in picked)
            self.lbl_status.setText(f"已选中 {len(picked)} 张原图（第 {numbers} 张）")

    # ------------------------------------------------- 画笔 / 橡皮 / 吸管 / 颜色选择

    # 状态栏提示：一个工具一句（PS 的口径：说清"怎么用 + 快捷键"）
    _TOOL_HINTS = {
        "brush": "画笔：按住左键涂抹（B 切工具 · [ ] 调粗细 · 颜色见左侧色块）",
        "eraser": "橡皮擦：按住左键擦成透明（E 切工具 · [ ] 调粗细）",
        "pick": "吸管：点一下取那一点的颜色当前景色（I 切工具）",
        "source": "原图选择：点一下选中某张原图；在棋盘格上拖一个框可以多选；"
                  "拖已选中的原图可以移动它（帧会跟着走）",
    }
    # 没图时每个工具各自的"为什么用不了"（沿用原图选择/抠图那两句的说法）
    _TOOL_NEEDS_IMAGE = {
        "source": "先添加图片，再选原图",
        "key": "先添加图片，再抠背景",
        "brush": "先添加图片，再用画笔",
        "eraser": "先添加图片，再用橡皮擦",
        "pick": "先添加图片，再用吸管",
    }

    def _set_active_tool(self, name: str | None) -> None:
        """单工具模式（PS 那套）：同一时刻只有一个工具亮着。

        为什么必须互斥：这几个工具都抢"画布上按住左键"这同一个手势 —— 画笔开着又留着
        「原图选择」，用户想涂两笔却把原图拖走了。

        这里是**唯一**改工具状态的地方：按钮勾选、查看器的三个模式开关、参数条显隐、
        状态栏提示全在这一处按 name 重设（幂等 —— 重复调用或从任意旧状态切过来都对）。
        按钮勾选走 blockSignals（见 VerticalToolBar.set_active_tool），
        所以"先全关再开一个"不会引出一串 toggled 回调互相打架。
        """
        viewer = self.image_viewer
        bar = viewer._v_toolbar
        name = name if name in ("source", "key", "brush", "eraser", "pick") else None
        if name is not None and (viewer._pixmap is None or viewer._pixmap.isNull()):
            # 没图：所有工具都没意义 —— 弹回去并说清原因，不做"点了没反应"的按钮
            bar.set_active_tool(None)
            viewer.set_source_tool(False)
            viewer.set_paint_tool(None)
            self._close_key_panel()
            self.lbl_status.setText(self._TOOL_NEEDS_IMAGE.get(name, "先添加图片，再用这个工具"))
            return
        bar.set_active_tool(name)
        viewer.set_source_tool(name == "source")
        viewer.set_paint_tool(name if name in ("brush", "eraser", "pick") else None)
        if name == "key":
            self._open_key_panel()
        else:
            self._close_key_panel()
        self.lbl_status.setText(self._TOOL_HINTS.get(name, ""))

    def _on_tool_button_toggled(self, name: str) -> None:
        """竖条上某个工具按钮被点了：统一交给 _set_active_tool（按钮现在的勾选态为准）。"""
        bar = self.image_viewer._v_toolbar
        checked = {key: button.isChecked() for key, button in bar.tool_buttons()}
        self._set_active_tool(name if checked.get(name) else None)

    # ---------------------------------------------------------------- 工具（单工具模式）

    def _on_source_tool_toggled(self, on: bool) -> None:
        """左侧竖条「原图选择」：先校验再真正开模式（不做点了没反应的按钮）。

        只要有图就能用 —— **不需要先建动作，也不要求有多张原图**：单张时"选哪张"没意义，
        但"拖动移位"照样有意义。校验与状态栏文案统一在 ``_set_active_tool`` 里。
        """
        self._set_active_tool("source" if on else None)

    def _dismiss_current_tool(self) -> None:
        """把**当前工具**收起来（Esc、或者点画布外那片界面时走这里）。

        只收竖条上那几个"手上拿着的工具"：画笔 / 橡皮 / 吸管 / 原图选择 / 抠除背景 ——
        它们抢的都是"画布上按住左键"这一个手势，收掉是用户明确要的（"按住空格或者其他
        按键要关闭画笔工具"）。**合成模式 / 复用模式 / 完美像素不收**：那三个是画布的
        帧语义与显示开关，不是手里的工具，顺手关掉就是擅自改用户状态了。

        手上本来就没有工具时什么也不做 —— Esc 在空画布上按一下必须没有任何副作用。
        """
        bar = self.image_viewer._v_toolbar
        # tool_buttons() 给的是 [(名字, 按钮), …]（不是 dict）：这里只关心"有没有工具亮着"
        if not any(button.isChecked() for _, button in bar.tool_buttons()):
            return
        self._set_active_tool(None)
        # _set_active_tool(None) 会把状态栏清空，这里补一句"刚刚发生了什么"，
        # 否则用户按了 Esc 只看到提示消失，不确定是收工具还是别的
        self.lbl_status.setText("已收起当前工具（B 画笔 · E 橡皮 · I 吸管）")

    def _on_before_refit_frames(self) -> None:
        """退出完美像素、即将重贴紧元素外框：抓 before 快照。

        记在**全局栈**（``current_only=False``）：帧列表不属于某个动作，而且"刚载图、
        还没建动作就开始涂像素画"是正常用法，这种时候也必须能撤销。
        """
        self._push_snapshot("重新贴紧元素外框", current_only=False)

    def _on_frames_refit(self, text: str) -> None:
        """元素外框重贴紧完成：重裁帧、刷预览、落一条撤销、标脏。

        和"检测完成"（``_on_frames_updated``）做的事基本一样，区别只有说法：
        **这不是重新识别了一遍元素** —— 老元素一个没多、一个没少、下标一个没动，
        只是外框跟着像素贴紧了；新画的那些只**追加在末尾**（位置由查看器算好）。
        所以状态栏不能沿用"已完成元素检测：共 N 个元素"那句话。

        追加出来的元素**不会自己变成帧**：帧来自 ``action.selected_indices``，
        要用户点一下选中它才进帧 —— 所以老动作已经排好的帧一个都不会动。
        """
        viewer = self.image_viewer
        for action in self.actions:
            viewer.crop_frames_for_action(action)
        if viewer.current_action:
            self.preview.set_action(viewer.current_action)
        self._transform_history_state = self._capture_transform_state()
        viewer.canvas.update()
        self._push_snapshot_done()
        self._mark_project_dirty()
        self.lbl_status.setText(text + "（Ctrl+Z 可撤销）")

    def _on_paint_stroke_started(self, tool: str) -> None:
        """一笔开始：先抓全量 before 快照（含原图引用）。

        顺序不能反：快照必须在**改像素之前**抓。笔画落在原图的**深拷贝**上再替换回列表
        （见 ImageViewer._paint_copy_of），所以快照里那份"涂之前"的引用不会被改坏。
        """
        if self._anim_doc is not None:
            # 动图文档：不抓快照，改记"这一笔的线段"（松手时同步到全部帧）
            self.image_viewer.start_stroke_record()
            return
        self._push_snapshot("画笔" if tool == "brush" else "橡皮擦", current_only=False)

    def _on_paint_stroke_finished(self, tool: str) -> None:
        """一笔结束：重裁帧、刷预览、记一条全局撤销、标脏。

        跟「抠除背景」应用后那套完全一样（原图换了就得重裁帧）—— 只重裁不重检，
        所以用户排好的帧、合成组、复用都不会被破坏。
        """
        viewer = self.image_viewer
        doc = self._anim_doc
        if doc is not None:
            # 动图文档：把这一笔（同一批线段 + 同样的工具/笔刷/颜色）记成一条操作，
            # 重放到每一帧上 —— 用户口径"所有改动影响全部帧"就是这一步在落实。
            # 注意**连当前这一帧也一起重放**：画笔已经把这一帧涂过了，只能以重放结果为准，
            # 不然当前帧会被涂两遍（这一帧和别的帧就不一样了）。
            segments = viewer.take_stroke_record() or []
            if segments:
                op = StrokeOp(tool=tool, size=int(viewer.brush_size),
                              color=viewer.paint_color.name(),
                              pixel_perfect=bool(viewer.pixel_perfect),
                              segments=segments,
                              label="画笔" if tool == "brush" else "橡皮")
                doc.apply(op, viewer)
                self._anim_doc_after_op(
                    f"已把这一笔同步到全部 {doc.count} 帧（Ctrl+Z 可撤销）")
            return
        if self.actions:
            for action in self.actions:
                action.invalidate_transforms()
            for action in self.actions:
                viewer.crop_frames_for_action(action)
            self._on_frames_updated()
        viewer.canvas.update()
        viewer._update_rulers()
        self.preview.set_source_pixmap(viewer._pixmap)
        self._push_snapshot_done()
        self._mark_project_dirty()
        self.lbl_status.setText(self._TOOL_HINTS.get(tool, "") + "（Ctrl+Z 可撤销这一笔）")

    def _on_color_picked(self, color) -> None:
        """吸管取到色：写进前景色（取完保持吸管模式，方便连着取几个色）。"""
        viewer = self.image_viewer
        viewer.set_paint_color(color)
        viewer.sync_paint_colors()
        self.lbl_status.setText(f"已取色 {color.name().upper()}（I 继续取色 · B 切画笔）")

    def _pick_paint_color(self, which: str = "fg") -> None:
        """色块被点了：开调色板。前景/背景分开（PS 那对色块）。"""
        viewer = self.image_viewer
        current = viewer.paint_bg_color if which == "bg" else viewer.paint_color
        title = "选择背景色" if which == "bg" else "选择前景色"
        color = QColorDialog.getColor(current, self, title)
        if not color.isValid():
            return                       # 用户取消：保持原色，什么都不动
        viewer.set_paint_color(color, background=(which == "bg"))
        viewer.sync_paint_colors()
        self.lbl_status.setText(
            f"{'背景' if which == 'bg' else '前景'}色 = {color.name().upper()}")

    def _swap_paint_colors(self) -> None:
        """X：前景/背景互换（PS 同款）。"""
        viewer = self.image_viewer
        fg, bg = viewer.paint_color, viewer.paint_bg_color
        viewer.set_paint_color(bg)
        viewer.set_paint_color(fg, background=True)
        viewer.sync_paint_colors()

    def _reset_paint_colors(self) -> None:
        """D：恢复默认黑白（PS 同款：前景黑、背景白）。"""
        viewer = self.image_viewer
        viewer.set_paint_color(QColor("#000000"))
        viewer.set_paint_color(QColor("#FFFFFF"), background=True)
        viewer.sync_paint_colors()

    def _nudge_brush_size(self, delta: int) -> None:
        """[ / ]：调笔刷粗细（PS 同款按键）。细笔慢调、粗笔快调。"""
        viewer = self.image_viewer
        step = 1 if viewer.brush_size < 10 else (5 if viewer.brush_size < 50 else 10)
        viewer.set_brush_size(viewer.brush_size + delta * step)
        viewer._paint_bar.set_brush_size(viewer.brush_size)
        self.lbl_status.setText(f"笔刷粗细 {viewer.brush_size} px（[ 细 · ] 粗）")

    def _setup_tool_shortcuts(self) -> None:
        """PS 同款工具快捷键（B/E/I、[ ]、D/X）。

        作用域刻意选 ``WidgetWithChildrenShortcut`` 挂在画布上，而**不是**
        ApplicationShortcut：这些是**单字母**键，全局生效的话用户在"重命名动作"的
        输入框里打 b/e/i 就会被抢走（PS 也是"光标不在输入框里才响应工具键"）。
        画布在鼠标按下时就会拿到焦点，正常用不会失效。
        """
        canvas = self.image_viewer.canvas
        bindings = [
            ("B", lambda: self._shortcut_tool("brush")),
            ("E", lambda: self._shortcut_tool("eraser")),
            ("I", lambda: self._shortcut_tool("pick")),
            ("[", lambda: self._nudge_brush_size(-1)),
            ("]", lambda: self._nudge_brush_size(1)),
            ("D", self._reset_paint_colors),
            ("X", self._swap_paint_colors),
        ]
        for key, slot in bindings:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(slot)
            canvas.addAction(action)
            self._tool_shortcuts.append(action)

    def _shortcut_tool(self, name: str) -> None:
        """快捷键切工具：同一个键再按一次 = 关掉（与点按钮两下的行为一致）。"""
        viewer = self.image_viewer
        target = None if viewer.paint_tool == name else name
        viewer._v_toolbar.set_active_tool(target)
        self._set_active_tool(target)

    def _open_key_panel(self) -> None:
        """左侧竖条「抠除背景」：打开参数浮层（预览不动原图，点"应用"才落地）。"""
        viewer = self.image_viewer
        button = viewer._v_toolbar.btn_key
        if not button.isChecked():
            self._close_key_panel()          # 再点一下按钮 = 关掉（等于取消）
            return
        if viewer._pixmap is None or viewer._pixmap.isNull():
            button.setChecked(False)
            self.lbl_status.setText("先添加图片，再抠背景")
            return
        panel = getattr(self, "_key_panel", None)
        if panel is None:
            panel = KeyPanel(viewer, viewer.canvas)
            panel.applied.connect(self._apply_key_panel)
            panel.cancelled.connect(self._close_key_panel)
            panel.pick_mode_changed.connect(self._on_key_pick_mode)
            self._key_panel = panel
        panel.start()

    def _close_key_panel(self) -> None:
        """关掉面板：清掉预览。原图、撤销栈、帧编排全程没被碰过。"""
        panel = getattr(self, "_key_panel", None)
        if panel is not None and panel.isVisible():
            panel.cancel()
        self.image_viewer._preview_pixmap = None
        self.image_viewer.canvas.update()
        button = self.image_viewer._v_toolbar.btn_key
        if button.isChecked():
            # blockSignals：这里只是"把按钮弹回去"。若让它发 toggled 信号，会再绕回
            # _on_tool_button_toggled → _set_active_tool → _close_key_panel，白跑一圈
            # （虽然能收敛，但状态来回设两遍，出问题时难查）
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)

    def _on_key_pick_mode(self, on: bool) -> None:
        """吸管模式：画布换十字光标并抢焦点（Esc 退出由面板自己处理）。"""
        canvas = self.image_viewer.canvas
        if on:
            canvas.setCursor(Qt.CursorShape.CrossCursor)
            canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            canvas.unsetCursor()
        self.lbl_status.setText("吸管：在原图上点一下，取那一点的颜色当背景色" if on else "")

    def _apply_key_panel(self, keyed, stats: dict) -> None:
        """把抠好的结果换成原图：记一条全局撤销，Ctrl+Z 能整个撤回来。"""
        viewer = self.image_viewer
        if keyed is None or keyed.isNull():
            return
        doc = self._anim_doc
        if doc is not None:
            # 动图文档：把"同一套抠图参数"记成一条操作同步到全部帧。
            # 为什么重算而不是把这一帧的结果硬贴给别的帧：每帧背景可能不同 ——
            # 面板本来就是"每张原图各自猜色"，这里按**同一套规则**对每帧各抠一遍，
            # 跟用户手动逐帧抠的结果一致（而且能撤销、能重做）。
            panel = getattr(self, "_key_panel", None)
            params = dict(getattr(panel, "_params", {}) or {})
            raw_color = getattr(panel, "_bg_color", None)
            color = tuple(raw_color) if raw_color is not None else None
            # 面板选了「抠全部」时它自己就是"每块各自猜色"（key_panel._run_blocks），
            # 这里照搬同一条口径：猜色规则一致，重放出来的结果才和用户手上看到的一样
            per_frame = getattr(panel, "_scope", "single") == "all"
            doc.apply(KeyOp(params=params, color=color, per_frame_guess=per_frame,
                            label="抠除背景"), viewer)
            self._anim_doc_after_op(
                f"已把这次抠背景同步到全部 {doc.count} 帧（Ctrl+Z 可撤销）")
            return
        # 先抓 before（含旧原图引用），再换图 —— 顺序反了撤销就回不去了
        self._push_snapshot("抠除背景", current_only=False)
        viewer._preview_pixmap = None
        viewer._pixmap = keyed
        # 每张原图也换成抠过的那一份（按原图分块从合成图上切）。
        # **必须同步**：抠图只算出/替换了合成大图 `_pixmap`，而"按原图重拼"（拖动原图、
        # 画布长大）与导出、重裁帧读的都是 `_source_images` —— 不同步的话，用户抠完背景
        # 一拖动原图、或用画笔涂两笔，抠掉的底色就会原样回来（用户报过的症状）。
        # 抠图只改像素不改几何，所以按同一套分块切出来写回即可。
        rects = viewer.source_rects()
        if len(rects) == len(viewer._source_images) and rects:
            keyed_image = keyed.toImage()
            viewer._source_images = [
                QPixmap.fromImage(keyed_image.copy(QRect(x, y, w, h)))
                for (x, y, w, h) in rects
            ]
        if not self.actions:
            # 还没编排过动作：抠完背景就该重新认元素，直接重检一遍。
            # 不用 set_pixmap()：那个会把 _source_images 重置成单张，多张原图的信息会丢。
            viewer._frames = []
            viewer._start_frame_detection(keyed.toImage(), base_frames=[], y_offset=0)
            viewer.fit_view()
        else:
            # 已经排过帧了：只换原图，绝不重检 —— 重检会把用户排好的帧全毁掉
            for action in self.actions:
                action.invalidate_transforms()
            for action in self.actions:
                viewer.crop_frames_for_action(action)
            self._on_frames_updated()
        viewer.canvas.update()
        viewer._update_rulers()
        self.preview.set_source_pixmap(keyed)
        self._push_snapshot_done()
        self._mark_project_dirty()
        button = viewer._v_toolbar.btn_key
        if button.isChecked():
            button.setChecked(False)
        self.lbl_status.setText(
            f"已抠除背景：清掉 {stats.get('changed_pct', 0.0):.1f}% 的像素，"
            f"内容占比 {stats.get('content_before_pct', 0.0):.1f}% → "
            f"{stats.get('content_after_pct', 0.0):.1f}%"
            # 抠图是全局操作，记在全局栈（与"添加原图"同一套）。Ctrl+Z 会**先撤动作内
            # 的操作**，动作栈撤空了才轮到它 —— 所以选中动作时这里说清回退入口，
            # 免得用户以为点了个没反应的操作。
            + ("（已记入全局历史：Ctrl+Z 会先撤动作内的操作，Ctrl+H 可直达这一条）"
               if viewer.current_action is not None else "")
        )

    def _init_history_card(self) -> None:
        """启动后建好历史记录卡片（默认隐藏，用户点工具栏按钮或 Ctrl+H 再打开）。"""
        card = self._ensure_history_card()
        card.hide()
        self._sync_history_button()

    def _sync_history_button(self, *_args) -> None:
        """历史记录显示时，原图工具栏里的"历史"按钮保持激活。"""
        toolbar = getattr(getattr(self, "image_viewer", None), "_toolbar", None)
        setter = getattr(toolbar, "set_history_active", None)
        if setter is not None:
            setter(self._history_shown())

    def _open_about(self) -> None:
        """帮助 → 关于 SpriteSheetTool：GPL 声明 + 第三方组件清单。"""
        dialog = AboutDialog(self)
        dialog.exec()
    def _start_update_check(self, notify_no_update: bool):
        if getattr(self, "_update_thread", None) is not None and self._update_thread.isRunning():
            return  # 上一次检查还没结束
        self._update_checked = True
        self._notify_no_update = notify_no_update
        self._update_thread = UpdateCheckThread(__version__, UPDATE_URL, self)
        self._update_thread.result_ready.connect(self._on_update_result)
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.start()

    def _on_update_thread_finished(self):
        self._update_thread = None

    def _on_update_result(self, info: dict | None):
        if not info:
            if self._notify_no_update:
                QMessageBox.information(self, "检查更新", f"当前已是最新版本 v{__version__}")
            return
        status = info.get("status", "")
        if status == "update":
            self._show_update_dialog(info)
        elif self._notify_no_update:
            if status == "latest":
                # 服务器版本与本地相同时不必赘述；只有服务器更旧（本地是自编译/未发布的
                # 版本）时才补一句，否则会出现「已是最新 v1.1.21（服务器最新 v1.1.18）」
                # 这种自相矛盾的提示。
                server = str(info.get("version", ""))
                detail = f"（服务器最高 v{server}）" if server and server != __version__ else ""
                QMessageBox.information(
                    self, "检查更新", f"当前已是最新版本 v{__version__}{detail}"
                )
            elif status == "no_installer":
                QMessageBox.warning(
                    self, "检查更新",
                    "服务器目录里没有找到安装包。\n\n"
                    "请先把 SpriteSheetTool_v.X.Y.Z.exe 上传到：\n"
                    f"{info.get('url', '')}",
                )
            elif status == "error":
                QMessageBox.warning(
                    self, "检查更新",
                    f"检查更新失败：\n{info.get('reason', '网络异常')}",
                )

    def _show_update_dialog(self, info: dict):
        version = info.get("version", "")
        url = info.get("url", "")
        notes = info.get("notes", "")
        msg = f"发现新版本 <b>v{version}</b>（当前 v{__version__}）"
        if notes:
            msg += f"<br><br>更新说明：<br>{notes}"
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(msg)
        if url:
            download_btn = box.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        else:
            download_btn = None
        box.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if download_btn is not None and box.clickedButton() is download_btn and url:
            self._download_and_install_update(version, url)

    def _download_and_install_update(self, version: str, url: str):
        """弹深色下载窗口，下载完成后运行安装包并退出本程序。"""
        dialog = UpdateDownloadDialog(version, url, self)
        dialog.download_ok.connect(self._launch_installer)
        dialog.exec()

    def _launch_installer(self, installer_path: str):
        """静默安装更新，然后关闭本程序；安装器装完会自动拉起新版本。"""
        import subprocess
        try:
            # /SILENT：显示进度条但不询问；/NORESTART 由 [Run] 段负责启动新版本
            subprocess.Popen(
                [installer_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            QMessageBox.warning(self, "启动安装程序失败", f"无法启动安装包：\n{exc}")
            return
        # 稍等片刻让安装器进程真正起来（它会自动关闭并重启本程序）
        QTimer.singleShot(800, self.close)

    def _stop_update_thread(self) -> None:
        """关闭时收尾「检查更新」线程。

        它与元素检测线程是同一类隐患：线程还在跑而宿主先被析构，退出时会挂住或崩溃。
        更新检查是网络请求、可能阻塞到超时，所以这里先断开信号，等一小会儿，
        仍未结束就强制终止（此时它只等在网络上，没有需要保护的共享状态）。
        """
        thread = getattr(self, "_update_thread", None)
        if thread is None:
            return
        self._update_thread = None
        try:
            if thread.isRunning():
                thread.result_ready.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            if thread.isRunning() and not thread.wait(1500):
                thread.terminate()
                thread.wait(500)
        except RuntimeError:
            pass

    def _dirty_tab_names(self) -> list[tuple[int, str]]:
        """列出所有有未保存改动的标签：(索引, 名字)。"""
        out = []
        current = self.tabs.current_index()
        for index in range(self.tabs.count()):
            if index == current:
                dirty = self._project_dirty
                name = self._project_name
            else:
                state = self._documents[index]["state"] if index < len(self._documents) else None
                dirty = bool(state and state["project_dirty"])
                name = self._documents[index]["name"] if index < len(self._documents) else ""
            if dirty:
                out.append((index, name))
        return out

    def _ask_quit_dirty(self, names: list[str]) -> str:
        """退出前询问多个未保存的工程。返回 "save" / "discard" / "cancel"。

        offscreen（回归脚本）下**不弹窗**，直接按"不保存"处理 —— 否则任何
        "改点东西再 win.close()" 的脚本都会卡在模态框上，跑批时表现为假死。
        真实运行时 QT_QPA_PLATFORM 不是 offscreen，照常弹窗。
        """
        if not self._ui_interactive():
            return "discard"
        shown = "\n".join(f"· {name}" for name in names[:8])
        more = f"\n… 以及另外 {len(names) - 8} 个" if len(names) > 8 else ""
        box = QMessageBox(self)
        box.setWindowTitle("退出前保存")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"有 {len(names)} 个工程还没保存：")
        box.setInformativeText(f"{shown}{more}\n\n退出前要保存它们吗？")
        btn_save = box.addButton("全部保存", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("不保存退出", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return "cancel"
        return "save" if clicked is btn_save else "discard"

    def _save_all_dirty_tabs(self) -> bool:
        """逐个保存有改动的标签；任何一个取消就整体放弃（返回 False）。"""
        for index, _name in self._dirty_tab_names():
            if index != self.tabs.current_index():
                self._switch_tab(index)
            if not self._save_project():
                return False
        return True

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 延迟一帧再检查：最大化过程中 isMaximized() 还没更新，直接拦会把最大化打断
        QTimer.singleShot(0, self._enforce_default_size)
        # 默认大小下的三块区域尺寸（最大化脱出成悬浮窗时沿用它们）
        self._schedule_panel_size_memory()

    def _enforce_default_size(self) -> None:
        """默认大小状态下禁止手动拖拽调整：非最大化时强制保持 1000x1000。"""
        if self.isMaximized():
            return  # 最大化状态不限制
        if getattr(self, '_forcing_size', False):
            return
        if self.width() != 1000 or self.height() != 1000:
            self._forcing_size = True
            self.resize(1000, 1000)
            self._forcing_size = False

    def closeEvent(self, event) -> None:
        """关闭窗口前：先处理未保存的标签，再收尾原图区。

        收尾这两件事不做会导致随机闪退（原生 access violation）：检测线程在跑时宿主被
        析构、或 app 级事件过滤器指向已释放对象。参见 ``ImageViewer.shutdown``。
        """
        if self._panels_floating:
            # 三块此刻是独立顶层窗口，主窗口关掉后它们会残留 → 先收回去
            self._dock_panels()
        dirty = self._dirty_tab_names()
        if dirty:
            choice = self._ask_quit_dirty([name for _i, name in dirty])
            if choice == "cancel":
                event.ignore()
                return
            if choice == "save" and not self._save_all_dirty_tabs():
                event.ignore()
                return
        self._stop_update_thread()
        self.image_viewer.shutdown()
        if hasattr(self, "_history_panel") and self._history_panel is not None:
            self._history_panel.disconnect_all()
        # 关窗前把抠图面板的钩子清掉：viewer._pick_target 指向面板，
        # 面板（弱引用之外）又挂在画布上，留着就是一对循环引用，退出时容易在 Qt
        # 析构里 fail-fast。
        if getattr(self, "_key_panel", None) is not None:
            self._close_key_panel()
        # 抽帧窗口 + 各种进度框：**退出前先停表、再关**。
        # 用户实测：关掉抽帧/导出窗口时刷屏
        # `QWindowsBackingStore::flush: GetDC failed` + `External WM_DESTROY`。
        # 根因是"原生窗口先被销毁，而定时器/重绘还在跑"——hideEvent/closeEvent 在
        # 程序退出销毁原生窗口这条路上**不一定触发**，所以必须在这里主动停表。
        dlg = getattr(self, "_extract_dialog", None)
        if dlg is not None:
            try:
                dlg._timer.stop()               # 播放表
                dlg._recalc.stop()              # 相似度攒批表
                dlg._pending_reapply = False
                busy = getattr(dlg, "_busy", None)
                if busy is not None:
                    busy.close()
                dlg.close()
            except Exception as e:  # noqa: BLE001
                print(f"[shutdown] 收尾抽帧窗口失败: {e!r}", file=sys.stderr)
        for attr in ("_similar_progress", "_align_progress", "_import_progress"):
            prog = getattr(self, attr, None)
            if prog is not None:
                try:
                    prog.close()
                except Exception:  # noqa: BLE001
                    pass
        super().closeEvent(event)

    def _handle_animated_path(self, path: str) -> bool:
        """动图专用入口：问"转序列帧 / 以单张图片修改"，然后走对应那条路。

        返回 True = 这条路已经处理完了（含用户按取消），调用方**别再**当静态图导入；
        返回 False = 不是真动图（静态 gif/webp、单帧、坏文件都算），调用方照旧走普通流程。
        """
        if not is_animated_file(path):
            return False
        anim = read_animation(path)
        if anim is None or anim.count <= 1:
            # 静态图静默处理（用户口径）：不问，也不报错，就当普通图片进去
            return False
        name = Path(path).stem
        warning = size_warning(anim.count, anim.width, anim.height)
        if warning:
            # 大动图先问一句：几百帧全部同步抠图/重放会明显卡，别让用户以为是死机
            box = QMessageBox(self)
            box.setWindowTitle("这张动图有点大")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f"「{name}」{anim.count} 帧 · {anim.width}×{anim.height}")
            box.setInformativeText(f"{warning}\n要继续吗？")
            box.setStandardButtons(QMessageBox.StandardButton.Yes
                                   | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return True
        mode = ask_animation_mode(self, anim, name)
        if mode == MODE_FRAMES:
            self._import_animation_as_frames(anim, name, "动图", path=path)
            return True
        if mode == MODE_DOC:
            self._open_animation_document(anim, name, path)
            return True
        self.lbl_status.setText("已取消：这张动图没有导入")
        return True

    def _open_image(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加图片", self._dialog_start(),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        for path in paths:
            self._remember_dialog_path(path)
            # 导入图片时自动检索同目录同名 JSON，有则优先导入工程（JSON 会连带加载配套图片）
            json_candidate = Path(path).with_suffix(".json")
            if json_candidate.is_file():
                try:
                    self._import_project(str(json_candidate))
                    continue
                except Exception:  # noqa: BLE001 - JSON 导入失败时回退为只导入图片
                    pass
            # 真动图先问"转序列帧 / 以单张图片修改"（静态图在那边会返回 False，照旧往下走）
            if self._handle_animated_path(path):
                continue
            pm = QPixmap(path)
            if not pm.isNull():
                self._on_image_loaded(pm)

    # ---- 导入 JSON 工程 ----

    def _import_json_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 JSON 工程", self._dialog_start(), "精灵图 JSON (*.json)"
        )
        if path:
            self._remember_dialog_path(path)
            self._import_project(path)

    def _ask_import_target(self, existing_actions: int, project) -> str:
        """询问导入目标：加入当前工程 / 新建工程 / 取消。"""
        box = QMessageBox(self)
        box.setWindowTitle("导入工程")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"当前工程有 {existing_actions} 个动作。")
        box.setInformativeText(
            f"识别格式：{project.format_label}\n"
            f"配套图片：{project.atlas_path.name}\n"
            f"动作数量：{len(project.actions)} 个\n\n"
            "要加入当前工程，还是新建一个标签页？"
        )
        btn_current = box.addButton("加入当前工程", QMessageBox.ButtonRole.AcceptRole)
        btn_new = box.addButton("新建工程", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_current)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_new:
            return "new"
        if clicked is btn_current:
            return "current"
        return "cancel"

    def _import_project(self, path_str: str, target: str = "ask") -> None:
        """导入 JSON（自动识别格式）。

        target:
          - "ask"：当前工程有内容时弹窗询问（加入当前 / 新建工程 / 取消）
          - "current"：直接加入当前工程（追加动作，不清空现有）
          - "new"：新建标签页后导入（清空式）
        """
        from spritesheet.importer import ImportError_, load_project

        try:
            project = load_project(path_str)
        except ImportError_ as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", f"解析 {path_str} 时出错：{exc}")
            return

        pm = QPixmap(str(project.atlas_path))
        if pm.isNull():
            QMessageBox.warning(self, "导入失败", f"无法读取配套图片：\n{project.atlas_path}")
            return

        has_content = bool(self.actions) or self.image_viewer._pixmap is not None
        append = False
        if target == "ask" and has_content:
            choice = self._ask_import_target(len(self.actions), project)
            if choice == "cancel":
                return
            if choice == "new":
                self._new_tab(switch=True)
                append = False
            else:
                append = True
        elif target == "new":
            self._new_tab(switch=True)
            append = False
        else:  # current 或空工程
            append = bool(has_content)

        try:
            self._apply_imported_project(project, pm, append=append)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "导入失败", f"应用工程时出错：{exc}")
            return

        # 左上角 JSON 徽标
        self._project_json_path = str(path_str)
        self.image_viewer.set_json_badge(True, str(path_str))

    def _end_all_renames(self) -> None:
        """结束所有卡片的编辑状态与待执行的延迟回调（销毁卡片前必须先做）。"""
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            card = self.action_list.itemWidget(item) if item is not None else None
            if isinstance(card, ActionCardWidget):
                try:
                    card._rename_timer.stop()   # 取消待触发的重命名收尾
                    card.end_rename()
                except (RuntimeError, AttributeError):
                    pass

    def _apply_imported_project(self, project, pm: QPixmap, append: bool = False) -> None:
        """把解析好的工程套用到界面：图集当原图、图块当元素、动作重建。

        若 JSON 带 elements（本工具格式），用其中的 origin 还原每个元素的**逻辑坐标**，
        于是合成帧的拼接间距与导出前完全一致 —— 图集块数、画面、合成组结构都不变。
        若缺少 elements（第三方格式），退化为把合成帧按 parts 拼成整块。
        """
        viewer = self.image_viewer
        atlas = pm
        # 导入前抓 before：把"导入"本身记成一条全局操作（快照含原图引用，
        # 因此撤销能把导入的图片、元素、动作整体撤掉）
        self._push_snapshot(
            f"导入 {getattr(project, 'format_label', 'JSON')}：{len(project.actions)} 个动作",
            current_only=False,
        )

        # 3) 载入原图（append 模式：拼到当前原图下方，新 tiles 的 y 加偏移）
        if append and viewer._pixmap is not None and not viewer._pixmap.isNull():
            old_h = viewer._pixmap.height()
            old_frames = list(viewer._frames)         # 保留旧 tiles
            old_logical = dict(viewer._frame_logical)  # 保留旧 logical
            viewer.append_pixmap(atlas)
            y_off = old_h + 16
        else:
            old_frames = []
            old_logical = {}
            viewer.set_pixmap(atlas)
            y_off = 0

        # 收集图块：旧 tiles 保留在前，新 tiles 加 y_off 追加在后
        tiles: list[tuple[int, int, int, int]] = []
        tile_index: dict[tuple[int, int, int, int], int] = {}
        for r in old_frames:
            if r not in tile_index:
                tile_index[r] = len(tiles)
                tiles.append(r)
        for act in project.actions:
            for fr in act.frames:
                for pp in fr.parts:
                    r = pp.rect
                    rr = (r[0], r[1] + y_off, r[2], r[3]) if append else r
                    if rr not in tile_index:
                        tile_index[rr] = len(tiles)
                        tiles.append(rr)
        viewer._detect_generation += 1  # 作废自动侦测，用精确 tiles
        viewer._frames = tiles

        # 4) 还原元素逻辑坐标：旧 logical 保留，新 elements 的 rect 加 y_off
        logical = dict(old_logical)
        for el in project.elements:
            rect = _rect_from_dict(el.get("rect") or {})
            o = el.get("origin") or {}
            rr = (rect[0], rect[1] + y_off, rect[2], rect[3]) if append else rect
            if rr in tile_index:
                logical[tile_index[rr]] = (int(o.get("x", 0)), int(o.get("y", 0)))
        viewer._frame_logical = logical

        viewer.canvas.update()
        viewer._update_rulers()

        # 5) 重建动作
        # append 模式不清空现有动作，只追加；非 append 才清空重建
        if not append:
            self._end_all_renames()
            self.actions.clear()
            self.action_list.clear()
            viewer.set_current_action(None)
            self.preview.set_action(None)

        total_frames = 0
        # 动作名/颜色在工程内唯一：导入的 JSON 可能带重名/同色动作，
        # append 模式下还要避开已有动作；否则卡片无法区分、导出语义混乱。
        used_names = {a.name for a in self.actions}
        used_colors = {a.color for a in self.actions}
        for ai, imported in enumerate(project.actions):
            color = imported.color or ""
            if color in used_colors or not color:
                for c in MACARON_COLORS:
                    if c not in used_colors:
                        color = c
                        break
            if not color:
                color = "#E91E63"
            name = imported.name
            if name in used_names:
                n2 = 2
                while f"{name}{n2}" in used_names:
                    n2 += 1
                name = f"{name}{n2}"
            action = Action(name, color)
            used_names.add(name)
            used_colors.add(color)
            # 导入的动作也要有自己的独立 undo 栈：否则它的选择/变换/偏移会被
            # 记到全局栈上（归属错乱），历史面板也不会有它的分组
            action.undo_stack = QUndoStack(self)
            from spritesheet.settings import undo_limit
            action.undo_stack.setUndoLimit(undo_limit())
            action.fps = imported.fps
            action.loop_mode = imported.loop
            action.anchor = imported.anchor
            # 动作类型 / 移动参数 / 地面线（仅本工具格式提供；TP/Aseprite 不含）
            action.action_type = imported.action_type or "static"
            if imported.move:
                action.move_direction, action.move_speed = imported.move
            if imported.ground:
                action.ground_enabled, action.ground_offset = imported.ground
            layouts: list[list[tuple[int, int, int]]] = []
            kept_frames: list = []      # 与 layouts 一一对应（丢弃无有效部件的帧）
            for fr in imported.frames:
                layout = []
                for p in fr.parts:
                    r = p.rect
                    if append:
                        r = (r[0], r[1] + y_off, r[2], r[3])
                    if r in tile_index:
                        layout.append((tile_index[r], p.pos[0], p.pos[1]))
                if layout:
                    layouts.append(layout)
                    kept_frames.append(fr)
            if layouts:
                action.frame_layouts = layouts
                # 导入的帧全部视为基线（非用户复用），Alt 点击不会误取消
                action.frame_layouts_base = len(layouts)
                # selected_indices 覆盖布局中用到的全部元素（用于界面高亮，
                # 也让「布局是否仍适用」的判定成立）
                ids: list[int] = []
                for layout in layouts:
                    for tile_id, _rx, _ry in layout:
                        if tile_id not in ids:
                            ids.append(tile_id)
                action.selected_indices = ids
            viewer.crop_frames_for_action(action)
            # 逐帧时长：只记录与 fps 默认值不同的帧（kept_frames 与最终帧同序）
            default_ms = max(1, round(1000 / max(1, action.fps)))
            for _fi, _fr in enumerate(kept_frames):
                _ms = max(1, int(getattr(_fr, "duration_ms", 0) or default_ms))
                if _ms != default_ms:
                    action.frame_durations[_fi] = _ms
            self._restore_imported_canvas(action, imported, kept_frames)
            total_frames += len(action.frames)
            self.actions.append(action)
            self._append_action_card(action)

        viewer.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.btn_add_action.setText("+ 添加动作")
        if self.actions:
            self.action_list.setCurrentRow(0)
        if append:
            # 追加导入：保留当前工程已有的 undo 历史，只把基线刷到最新
            self._push_snapshot_done()
            self._refresh_history_after_change()
        else:
            # 新建/首次导入：从干净历史起步，"导入"成为新工程的第一条记录
            self._reset_transform_history()
            self._push_snapshot_done()

        msg = (f"已导入 {project.format_label}：{len(self.actions)} 个动作、"
               f"{total_frames} 帧、{len(tiles)} 个图块")
        self.lbl_status.setText(msg)
        notices = list(project.warnings)
        if notices:
            QMessageBox.information(
                self, "导入完成",
                f"{msg}\n\n提示：\n" + "\n".join(f"· {n}" for n in notices),
            )

    @staticmethod
    def _restore_imported_canvas(action: Action, imported, kept_frames: list) -> None:
        """按导入记录还原画布几何（padding + 逐帧偏移）。

        导入后的帧是用图块重建的**紧贴小图**，若直接交给 ``compute_preview_layout``，
        它会按各帧自身尺寸重新居中 —— 帧尺寸不一致时帧间相对位置就被破坏（动画抖动）。
        这里用 ``padding``（四周留边）与 ``frame_offsets``（逐帧微调）把内容摆回原先
        在画布中的位置：

            内容落点 = padding + 居中补偿(max-尺寸)//2 + frame_offset

        令其等于导入记录的目标位置（本工具格式的 frames[].offset；TP/Aseprite 为
        帧原点 + 部件 pos），反解出 frame_offset 即可。画布留边优先用文件里写明的
        padding，其次由 sourceSize 与帧尺寸反推。
        """
        n = min(len(action.frames), len(kept_frames))
        if n <= 0:
            return
        sizes = [(action.frames[i].width(), action.frames[i].height()) for i in range(n)]
        max_w = max(s[0] for s in sizes)
        max_h = max(s[1] for s in sizes)

        pad = imported.padding
        if pad is None:
            targets = [f.canvas_size for f in kept_frames[:n] if f.canvas_size]
            if targets:
                target_w = max(t[0] for t in targets)
                target_h = max(t[1] for t in targets)
                pad = max(0, min((target_w - max_w) // 2, (target_h - max_h) // 2))
            else:
                pad = 0
        action.padding = int(pad)

        offsets: dict[int, tuple[int, int]] = {}
        for i in range(n):
            pos = kept_frames[i].canvas_pos
            if pos is None:
                continue
            dx = int(pos[0]) - action.padding - (max_w - sizes[i][0]) // 2
            dy = int(pos[1]) - action.padding - (max_h - sizes[i][1]) // 2
            if dx or dy:
                offsets[i] = (dx, dy)
        action.frame_offsets = offsets
        action.invalidate_transforms()

    # ---- 拖拽：图片 / JSON ----

    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if Path(url.toLocalFile()).suffix.lower() in (SUPPORTED_IMAGE_EXT | {".json", ".sstool"}):
                        event.acceptProposedAction()
                        return
        except Exception:  # noqa: BLE001 - 拖放回调异常会让 PyQt abort（闪退）
            pass

    def dragMoveEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
        except Exception:  # noqa: BLE001
            pass

    def dropEvent(self, event):
        try:
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        except Exception:  # noqa: BLE001
            return
        if not paths:
            return
        event.acceptProposedAction()
        if len(paths) > 1 or Path(paths[0]).is_dir():
            # 多文件 / 拖文件夹：用户口径 **B2** = 依次追加成一张一张原图
            # （以前这里只取 urls()[0]，文件夹还会静默什么都不发生 —— 一并修掉）
            QTimer.singleShot(0, lambda ps=list(paths): self._import_image_paths(ps))
            return
        path = paths[0]
        # 单个文件：保持原来的智能打开（自动找同名 JSON、询问新建/加入当前）
        QTimer.singleShot(0, lambda p=path: self._open_path_smart(p, target="ask"))

    def _load_image_with_progress(self, path: str):
        """读一张图：**分块读文件**，大文件时给真进度条（用户要求：不要匀速假条）。

        为什么这才是"真"进度：单个大图在 Qt/Pillow 里是一次性解码的，中途拿不到百分比；
        但**读文件**是我自己干的，可以按字节报。所以进度分两段，各自如实标注：
        读取（真实字节百分比）→ 解码（一次跳完）→ 之后检测元素交给调用方。
        """
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtWidgets import QProgressDialog
        try:
            total = os.path.getsize(path)
        except OSError:
            return QPixmap(path)
        prog = None
        data = bytearray()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)          # 1MB 一块
                if not chunk:
                    break
                data += chunk
                if prog is None and total > (8 << 20):   # 8MB 以上才值得弹条
                    prog = QProgressDialog("正在读取图片……", "", 0, max(1, total), self)
                    prog.setWindowTitle("导入图片")
                    prog.setWindowModality(Qt.WindowModality.ApplicationModal)
                    prog.setCancelButton(None)
                    prog.setMinimumDuration(0)
                    self._import_progress = prog        # 留引用：老坑
                    prog.show()
                if prog is not None:
                    prog.setMaximum(max(1, total))
                    prog.setValue(len(data))
                    prog.setLabelText(f"正在读取图片…… {len(data) // 1024} / "
                                      f"{max(1, total) // 1024} KB")
                    QApplication.processEvents()
        if prog is not None:
            prog.setLabelText("正在解码图片……（解码是一次性的，没有中间百分比）")
            QApplication.processEvents()
        pm = QPixmap()
        if not pm.loadFromData(bytes(data)):
            pm = QPixmap(path)                    # 兜底：交回 Qt 自己读
        if prog is not None:
            prog.close()
        return pm

    def _expand_import_paths(self, paths):
        """把拖进来的路径展开成"要导入的图片文件列表"（文件夹取一层，按名字排序）。"""
        files: list[str] = []
        skipped = 0
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.is_file() and child.suffix.lower() in SUPPORTED_IMAGE_EXT:
                        files.append(str(child))
                    elif child.is_file():
                        skipped += 1
            elif p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXT:
                files.append(str(p))
            else:
                skipped += 1
        return files, skipped

    def _import_image_paths(self, paths) -> None:
        """B2（用户选的口径）：多文件 / 拖文件夹 → **依次追加**成一张一张原图。

        不拼 atlas、也不各开标签页，就是按顺序喂给 `_on_image_loaded`
        （第一张建立工程，其余走现成的"追加图片"那条路，撤销/历史都是它原有的语义）。
        进度是**真百分比**："第 n/N 个"，每个文件完成跳一格。
        """
        from PyQt6.QtWidgets import QProgressDialog
        files, skipped = self._expand_import_paths(paths)
        if not files:
            self.lbl_status.setText("拖进来的东西里没有可导入的图片"
                                    + (f"（跳过 {skipped} 个不支持的文件）" if skipped else ""))
            return
        prog = None
        if len(files) > 1:
            prog = QProgressDialog("正在导入图片……", "", 0, len(files), self)
            prog.setWindowTitle("导入图片")
            prog.setWindowModality(Qt.WindowModality.ApplicationModal)
            prog.setCancelButton(None)
            prog.setMinimumDuration(0)
            prog.setValue(0)
            self._import_progress = prog
            prog.show()
            QApplication.processEvents()
        ok = 0
        for n, path in enumerate(files, 1):
            # 程序已经在退出（用户关窗口/被关掉）就立刻收手：再往死掉的窗口上刷进度、
            # 再往正在拆的工程里塞图，正是 GetDC failed / External WM_DESTROY 刷屏的来源。
            if QApplication.closingDown():
                break
            if prog is not None:
                prog.setValue(n - 1)
                prog.setLabelText(f"正在导入第 {n}/{len(files)} 个：{Path(path).name}")
                QApplication.processEvents()
            pm = self._load_image_with_progress(path)
            if pm.isNull():
                skipped += 1
                continue
            self._on_image_loaded(pm)
            ok += 1
            if prog is not None:
                prog.setValue(n)
                QApplication.processEvents()
        if prog is not None:
            prog.close()
        note = f"已导入 {ok} 张原图"
        if ok:
            telemetry.note("import_files", ok)     # 统计：按"成功导入了几张"计
        if skipped:
            note += f"（跳过 {skipped} 个不支持或读不出的）"
        self.lbl_status.setText(note)

    def _open_path_smart(self, path: str, target: str = "ask") -> None:
        """智能打开拖入/菜单选择的文件：图片自动找同名 JSON，JSON 走导入。

        target: "ask" = 询问新建/加入当前；"current" = 直接加入当前工程。
        """
        suffix = Path(path).suffix.lower()
        if suffix == ".sstool":
            self._safe_open_project(path)
            return
        if suffix == ".json":
            self._import_project(path, target=target)
            return
        if suffix in SUPPORTED_IMAGE_EXT:
            # 图片自动找同目录同名 JSON
            json_sidecar = Path(path).with_suffix(".json")
            if json_sidecar.is_file():
                self._import_project(str(json_sidecar), target=target)
                return
            # 真动图先问一句怎么处理（静态图返回 False，走下面的普通导入）
            if self._handle_animated_path(path):
                return
            # 普通图片：走**分块读 + 真进度**（大文件才弹条），再交给 _on_image_loaded
            pm = self._load_image_with_progress(path)
            if not pm.isNull():
                self._on_image_loaded(pm)
            return
    def _on_tab_drop(self, path: str) -> None:
        """拖入标签栏：新建一个标签页并打开该文件（不顶掉当前工程）。"""
        suffix = Path(path).suffix.lower()
        if suffix == ".sstool":
            self._new_tab(switch=True)
            self._safe_open_project(path)
        elif suffix == ".json":
            self._import_project(path, target="new")
        else:
            self._new_tab(switch=True)
            self._open_path_smart(path, target="current")

    def _on_image_loaded(self, pm: QPixmap):
        """首次载入或追加原图；追加时保留已有动作和帧排序。"""
        if self._anim_doc is not None:
            # 动图文档里又载入普通图片 = 用户要开一份普通工程：先把动图文档收干净。
            # 不收就会落到下面的"追加原图"分支 —— 那等于"往动图文档里塞第二张原图"，
            # 而动图文档的原图区只有当前这一帧（根本不是这个模型）。
            self._anim_doc = None
            self.actions = []
            self.action_list.clear()
            self.image_viewer.set_animation_document_mode(False)
            self.image_viewer.reset_document()
            self._apply_anim_doc_mode()
        if self._anim_source is not None and self._anim_doc is None and not self.actions:
            # 从动图来的序列帧工程里又载入普通图片：这份工程不再是"那个动图"了，
            # 那条 gif 横条要收掉（不然"第几帧"对应哪张已经说不准，按钮还得禁用着）
            self._anim_source = None
            self._apply_anim_doc_mode()
        has_image = self.image_viewer._pixmap is not None and not self.image_viewer._pixmap.isNull()
        if not has_image:
            self.image_viewer.set_pixmap(pm)
            self.actions.clear()
            self.action_list.clear()
            self.image_viewer.set_actions(self.actions)
            self.image_viewer.set_current_action(None)
            self.preview.set_action(None)
        else:
            if pm is None or pm.isNull():
                return
            # 追加前抓 before（含旧原图引用），把"加图"本身记成一条全局操作：
            # Ctrl+Z 能把新加的图和它检测出的元素一起撤掉。
            self._push_snapshot(
                f"添加第 {len(self.image_viewer._source_images) + 1} 张原图",
                current_only=False,
            )
            if not self.image_viewer.append_pixmap(pm):
                self._snapshot_before = None
                self._snapshot_text = ""
                return
            # 新图追加到帧列表末尾，原有 selected_indices 无需改动；
            # 重新裁剪所有动作以刷新当前源图的帧缓存。
            for action in self.actions:
                self.image_viewer.crop_frames_for_action(action)
            if self.image_viewer.current_action:
                self.preview.set_action(self.image_viewer.current_action)
            self._push_snapshot_done()
        # 同步最新原图给预览组件，供导出弹窗的原图预览使用
        self.preview.set_source_pixmap(self.image_viewer._pixmap)
        if has_image:
            # 追加图片：**保留**已有 undo 历史，只把快照基线/面板刷到最新
            self._refresh_history_after_change()
        else:
            # 首次载图（从空白开始）：旧历史本来只有空栈，清掉无妨
            self._reset_transform_history()
        self.btn_add_action.setText("+ 添加动作")
        source_count = len(self.image_viewer._source_images)
        verb = "已加载" if source_count == 1 else "已添加"
        # 还没有动作时**默认打开「原图选择 / 移动」**（用户要求）：这种时候画布上能拖的
        # 只有原图 —— 框选元素没有地方存（selected_indices 属于动作），点了只会提示
        # "请先添加动作"。先切按钮再写状态栏，免得加载提示被工具提示盖掉。
        if source_count == 1 and not self.actions:
            self.image_viewer._v_toolbar.btn_source.setChecked(True)
        self.lbl_status.setText(
            f"{verb}第 {source_count} 张原图：{pm.width()}×{pm.height()}，"
            f"当前共检测到 {len(self.image_viewer._frames)} 个元素"
        )
        self._mark_project_dirty()

    def _on_add_action_clicked(self):
        """根据是否已加载图片切换按钮文字。"""
        if self.image_viewer._pixmap is None or self.image_viewer._pixmap.isNull():
            self._open_image()
        else:
            self._create_action()

    def _append_action_card(self, action: Action) -> ActionCardWidget:
        """把动作加入列表并创建卡片（供新建动作与导入复用）。"""
        item = QListWidgetItem()
        self.action_list.addItem(item)
        card = ActionCardWidget(action.name, action.color)
        card.color_changed.connect(lambda c, a=action: self._on_action_color_changed(a, c))
        card.name_changed.connect(lambda n, a=action: self._on_action_name_changed(a, n))
        card.delete_requested.connect(lambda a=action: self._delete_action(a))
        card.clicked.connect(lambda a=action: self._select_action_by_obj(a))
        item.setSizeHint(QSize(0, 34))
        self.action_list.setItemWidget(item, card)
        item.setData(Qt.ItemDataRole.UserRole, action)
        return card

    def _next_action_color(self) -> str:
        """给新动作挑一个没用过的主题色（手动加动作、导入动图共用同一套规则）。"""
        used_colors = {a.color for a in self.actions}
        for color in MACARON_COLORS:
            if color not in used_colors:
                return color
        return "#E91E63"

    def _animation_action_name(self, hint: str) -> str:
        """导入动图自动建的动作名：优选用文件名（比"动作7"有信息量），重名就加序号。"""
        base = (hint or "").strip() or "动图"
        used = {a.name for a in self.actions}
        if base not in used:
            return base
        n = 2
        while f"{base} {n}" in used:
            n += 1
        return f"{base} {n}"

    def _import_animation_as_frames(self, anim: Animation, name_hint: str = "",
                                    source_label: str = "", path: str = "") -> bool:
        """「① 转成序列帧」：一次性铺进画布 + 每帧整帧一个元素 + 自动建动作（带逐帧时长）。

        为什么自动建动作：用户口径是"导入后自动建一个动作、按顺序放 N 帧、并带上原动图的
        逐帧时长" —— 不然进来还得手动挑一遍元素才能预览/导出 GIF，等于白导。
        撤销：整次导入是**一条**全局记录（老规矩，见"添加原图"）。
        """
        viewer = self.image_viewer
        had_image = viewer._pixmap is not None and not viewer._pixmap.isNull()
        label = source_label or f"动图（{anim.count} 帧）"
        self._push_snapshot(f"导入{label}", current_only=False)
        if not viewer.import_frames(anim.frames):
            # 一张都铺不进去：把已经压进栈的记录撤掉，别留一条"没发生"的撤销
            self._snapshot_before = None
            self._snapshot_text = ""
            return False

        # 新元素正好在末尾、顺序就是帧序（布局是从左到右、从上到下），取末尾 N 个即可
        total = len(viewer._frames)
        first = max(0, total - anim.count)
        action = self._create_animation_action(name_hint, anim.durations, first, total)
        self._install_animation_action(action)
        # 记住"这份工程是从动图来的"：序列帧模式下那条横条 + 「转为动图文档」都靠它
        self._remember_animation_frames(first, anim.count, anim.durations, action,
                                        name_hint or "动图", path)
        self._push_snapshot_done()
        if had_image:
            self._refresh_history_after_change()
        else:
            self._reset_transform_history()
        self._mark_project_dirty()
        self._apply_anim_doc_mode(fit=False)     # 序列帧模式也要露出那条 gif 横条
        self.lbl_status.setText(
            f"已导入动图 {anim.count} 帧（横向铺开，每帧一个元素），"
            f"并建了动作「{action.name}」（逐帧时长跟着原动图）"
        )
        return True

    # ---------------------------------------------------------------- ② 动图文档
    def _apply_anim_doc_mode(self, fit: bool = False) -> None:
        """把界面切到（或切回）动图文档该有的样子：画布只露一帧、横条二选一、预览用壳动作。

        所有进出路径都只调这一处（切标签页、新建动图文档、导入普通图片），
        免得"哪儿记得收、哪儿忘了收"。
        """
        viewer = self.image_viewer
        doc = self._anim_doc
        # 三种状态：动图文档 / 从动图来的序列帧工程 / 普通工程
        if doc is not None:
            self.anim_doc_bar.set_doc(doc)
            self.anim_doc_bar.set_convert_enabled(True)
            self.anim_doc_bar.setVisible(True)
            # 动图文档里没有动作：「动画设置」横条（fps/循环/锚点）全是空的，同一个位置二选一
            self.anim_bar.setVisible(False)
        elif self._anim_source is not None:
            # ① 序列帧（用户口径：序列帧模式也显示这个 gif 工具）。这条横条**额外**加一行，
            # 「动画设置」横条照样在 —— 序列帧还是正常工程，fps/锚点那些还得能用。
            rects = self._live_animation_frames()
            self.anim_doc_bar.set_frames_state(self._anim_source, rects,
                                               self._anim_frames_index())
            self.anim_doc_bar.set_convert_enabled(
                rects is not None,
                "转不了：那几帧原图被删过、或工程里原图数量变了（第几帧对应哪张已经说不准）")
            self.anim_doc_bar.setVisible(True)
            self.anim_bar.setVisible(True)
        else:
            self.anim_doc_bar.set_doc(None)
            self.anim_doc_bar.setVisible(False)
            self.anim_bar.setVisible(True)
        viewer.set_animation_document_mode(doc is not None)
        if doc is None:
            self._anim_preview_action = None
            self.preview._project_manifest = self._project_manifest
            return
        viewer.set_animation_frame(doc.frame(), fit=fit)
        # 动图文档不玩元素/动作：把动作列表清掉，别让「当前动作」指着一份不存在的编排
        viewer.set_actions([])
        self._refresh_anim_doc_views()

    def _refresh_anim_doc_views(self) -> None:
        """动图文档改动之后：刷画布上那一帧、预览播放、导出用的壳动作、帧数横条。"""
        doc = self._anim_doc
        if doc is None:
            return
        viewer = self.image_viewer
        viewer.set_animation_frame(doc.frame(), fit=False)
        action = doc.synthetic_action()
        self._anim_preview_action = action
        self.preview.set_actions([action])
        self.preview.set_action(action)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.anim_doc_bar.refresh()

    def _on_anim_bar_frame_changed(self, value: int) -> None:
        """横条上「查看第几帧」（两种模式共用这条信号）：两种都**只看**，不动数据。"""
        if self._anim_doc is not None:
            self._on_anim_doc_frame_changed(value)
            return
        source = self._anim_source
        if source is None:
            return
        action = source.action
        if action is None or action not in self.actions:
            self.lbl_status.setText("那个动图动作已经不在了（被删掉或改过），说不出这是第几帧")
            return
        index = max(0, min(int(value), max(0, source.count - 1)))
        if self.image_viewer.current_action is not action:
            # 滑块说的就是"那个动图动作的第几帧"：当前动作不是它就先切过去
            self._select_action_by_obj(action)
        # 走预览自己的切帧入口：缩略图、微调控件、帧号标签一起同步。直接摸
        # `preview._frame_index` 只会动播放指针，右边「当前帧」面板还停在上一个帧上。
        self.preview._on_slider_changed(index)
        self.anim_doc_bar.refresh()
        self.lbl_status.setText(f"看第 {index + 1} / {source.count} 帧（只切当前帧，不改数据）")

    # ---------------------------------------------------------------- 两种模式互转
    def _on_anim_doc_convert(self) -> None:
        """横条上那个转换按钮：按当前模式决定方向（动图文档 ⇄ 序列帧）。"""
        if self._anim_doc is not None:
            self._convert_anim_doc_to_frames()
        elif self._anim_source is not None:
            self._convert_frames_to_anim_doc()

    def _live_animation_durations(self) -> list[int]:
        """转换时用的逐帧时长：动作还在就用动作里**现在**的（用户可能改过 fps），
        否则退回记下来的原动图时长。"""
        source = self._anim_source
        if source is None:
            return []
        action = source.action
        if action is not None and action in self.actions and action.frames:
            count = min(len(action.frames), source.count)
            return [int(action.frame_duration_ms(i)) for i in range(count)]
        return list(source.durations)

    def _convert_anim_doc_to_frames(self) -> None:
        """②→①：拿**现在这一帧帧**（改过的样子）铺成序列帧工程。

        用户口径「换载入模式但是修改过的不还原」—— 用的是 ``doc.frames``（操作链已经
        作用过的那一帧帧），**不是**重新读一遍 gif。于是操作链在这里"烙进像素"：转过去
        就是普通图集工程、动图文档那条撤销链到此为止；转换本身记**一条**全局撤销，
        Ctrl+Z 能整条退回动图文档（用户口径）。
        """
        doc = self._anim_doc
        if doc is None:
            return
        frames = list(doc.frames)
        durations = list(doc.durations)
        name, path = doc.name, doc.path
        viewer = self.image_viewer
        # 先记快照再动 doc：_push_snapshot 平时在动图文档里是**早退**的，这里要用 forced
        self._push_snapshot("转为序列帧", current_only=False, forced=True)
        self._anim_doc = None
        viewer.reset_document()
        if not viewer.import_frames(frames):
            # 铺不进去：把刚记的撤销撤掉，并把用户的动图文档**原样还回来**
            self._snapshot_before = None
            self._snapshot_text = ""
            self._anim_doc = doc
            self._apply_anim_doc_mode(fit=False)
            self.lbl_status.setText("转不了：这几帧铺不出序列帧（画布已经恢复成动图文档）")
            return
        first = max(0, len(viewer._frames) - len(frames))
        action = self._create_animation_action(name, durations, first, len(viewer._frames))
        self._install_animation_action(action)
        self._remember_animation_frames(first, len(frames), durations, action,
                                        name or "动图", path)
        self._apply_anim_doc_mode(fit=True)
        self._push_snapshot_done()
        self._refresh_history_after_change()
        self._mark_project_dirty()
        self.lbl_status.setText(
            f"已转为序列帧：{len(frames)} 帧铺成原图 + 建了动作「{action.name}」"
            f"（改动都留在图上，Ctrl+Z 可退回动图文档）")

    def _convert_frames_to_anim_doc(self) -> None:
        """①→②：把序列帧工程这几帧收成动图文档（用**现在的像素**，改过也带着走）。"""
        source = self._anim_source
        rects = self._live_animation_frames()
        if source is None or rects is None:
            self.lbl_status.setText(
                "转不了：这份工程不是从动图来的，或那几帧原图动过（第几帧对应哪张说不准）")
            self._apply_anim_doc_mode(fit=False)      # 顺手把横条/按钮状态刷对
            return
        viewer = self.image_viewer
        composite = viewer._pixmap
        frames = [composite.copy(int(x), int(y), int(w), int(h)) for x, y, w, h in rects]
        durations = self._live_animation_durations()
        self._push_snapshot("转为动图文档", current_only=False)
        doc = AnimDoc(frames, durations, name=source.name or "动图", path=source.path)
        self._anim_source = None          # 进文档模式：横条改由 _anim_doc 说话
        self.actions = []
        self.action_list.clear()
        viewer.set_actions([])
        viewer.set_current_action(None)
        self._anim_doc = doc
        doc.rebuild(viewer)
        self._apply_anim_doc_mode(fit=True)
        self._push_snapshot_done()
        self._refresh_history_after_change()
        self._mark_project_dirty()
        self.lbl_status.setText(
            f"已转为动图文档：{doc.count} 帧（用的是它们现在的像素，改过的都带着）"
            f" · Ctrl+Z 可退回序列帧")

    def _open_animation_document(self, anim: Animation, name_hint: str = "",
                                 path: str = "") -> bool:
        """② 以单张图片修改：开一个动图文档。

        当前标签页**是空的**就用它；已经有内容就新开一个标签页 —— 动图文档跟图集工程不是
        一回事，塞进有内容的标签页会把用户那份编排顶掉（用户口径里没要求这个）。
        """
        viewer = self.image_viewer
        has_content = bool(self.actions) or (viewer._pixmap is not None
                                             and not viewer._pixmap.isNull())
        if has_content:
            self._new_tab(switch=True)
        doc = AnimDoc(anim.frames, anim.durations, name=name_hint or "动图", path=path)
        self._anim_doc = doc
        self.actions = []
        self.action_list.clear()
        viewer.set_current_action(None)
        self._apply_anim_doc_mode(fit=True)
        self._mark_project_dirty()
        self.lbl_status.setText(
            f"动图文档：{doc.count} 帧（画布上只显示当前帧，所有改动会同步到全部帧）"
        )
        return True

    def _on_anim_doc_frame_changed(self, value: int) -> None:
        """「查看第几帧」：只换"看的是哪一帧"，**不改编辑范围**（用户口径）。"""
        doc = self._anim_doc
        if doc is None:
            return
        doc.index = max(0, min(int(value), doc.count - 1))
        self.image_viewer.set_animation_frame(doc.frame(), fit=False)
        self.preview.set_source_pixmap(self.image_viewer._pixmap)
        self.anim_doc_bar.refresh()
        self.lbl_status.setText(
            f"看第 {doc.index + 1} / {doc.count} 帧（改动照样作用于全部帧）")

    def _on_anim_doc_transform(self, kind: str) -> None:
        """整段变换（翻转/旋转）：每帧做同样的像素变换，记成一条可撤销的操作。"""
        doc = self._anim_doc
        if doc is None:
            return
        label = {"flip_h": "左右翻转", "flip_v": "上下翻转",
                 "rot90": "转 90°", "rot180": "转 180°"}.get(kind, "变换")
        doc.apply(TransformOp(kind, label=label), self.image_viewer)
        self._refresh_anim_doc_views()
        self._mark_project_dirty()
        self.lbl_status.setText(f"已对全部 {doc.count} 帧{label}（Ctrl+Z 可撤销）")

    def _anim_doc_after_op(self, text: str) -> None:
        """动图文档里一条破坏性操作落地之后：刷视图 + 标脏 + 状态文字。"""
        self._refresh_anim_doc_views()
        self._mark_project_dirty()
        self.lbl_status.setText(text)

    def _create_action(self):
        """无图片时打开文件对话框；有图片时直接创建动作卡牌。"""
        # 选一个未被使用的颜色
        color = self._next_action_color()
        used_names = {a.name for a in self.actions}
        n = len(self.actions) + 1
        while f"动作{n}" in used_names:
            n += 1
        name = f"动作{n}"
        action = Action(name, color)
        action.undo_stack = QUndoStack(self)
        from spritesheet.settings import undo_limit
        action.undo_stack.setUndoLimit(undo_limit())
        self._push_snapshot(f"添加动作「{name}」", current_only=False)
        self.actions.append(action)
        self._transform_history_state = self._capture_transform_state()
        self._mark_project_dirty()
        self.image_viewer.set_actions(self.actions)

        card = self._append_action_card(action)

        # 切换动作前结束所有卡牌的编辑状态
        for i in range(self.action_list.count()):
            w = self.action_list.itemWidget(self.action_list.item(i))
            if isinstance(w, ActionCardWidget):
                w.end_rename()

        self.action_list.setCurrentRow(self.action_list.count() - 1)
        # 新建动作直接进入重命名状态
        card.start_rename()
        self.lbl_status.setText(f"已添加动作「{name}」，共 {len(action.frames)} 帧")
        # 有动作了：把"没有动作时默认打开的原图拖动"退回去，画布拖动回到它的主要用途
        # —— 框选元素。只在这个"从无到有"的节点上切一次，用户中途手动切的照样算数。
        if len(self.actions) == 1 and self.image_viewer._v_toolbar.btn_source.isChecked():
            self.image_viewer._v_toolbar.btn_source.setChecked(False)
        self._push_snapshot_done()
        self._history_panel.rebuild()

    def _on_action_color_changed(self, action: Action, color: str):
        # 颜色在工程内唯一：撞色时自动换成未使用的色板颜色
        used = {a.color for a in self.actions if a is not action}
        if color in used:
            for c in MACARON_COLORS:
                if c not in used:
                    color = c
                    break
        self._push_snapshot(f"改动作颜色「{action.name}」→ {color}", focus_action=action)
        action.color = color
        if self.image_viewer.current_action is action:
            self.image_viewer.action_color = color
        self.image_viewer.refresh()
        self._mark_project_dirty()
        self._push_snapshot_done()

    def _on_action_name_changed(self, action: Action, name: str):
        # 动作名在工程内唯一：改成重名时自动跳号（保持其它动作名字不动）
        used = {a.name for a in self.actions if a is not action}
        if name in used:
            n2 = 2
            while f"{name}{n2}" in used:
                n2 += 1
            name = f"{name}{n2}"
        self._push_snapshot(f"改动作名「{action.name}」→「{name}」", focus_action=action)
        action.name = name
        self._mark_project_dirty()
        self._push_snapshot_done()

    def _delete_action(self, action: Action):
        """删除动作卡片并将选择切换到相邻动作。"""
        if action not in self.actions:
            return
        # 先把当前动作还没落账的改动记下来（归属它自己的栈），
        # 避免删除后 pending timer 触发时把 before/after 串到别的动作
        if getattr(self, "_per_action_snap_timer", None) is not None and self._per_action_snap_timer.isActive():
            self._finish_per_action_snapshot()
        self._per_action_snap_text = ""
        self._push_snapshot(f"删除动作「{action.name}」", current_only=False)
        row = next((i for i in range(self.action_list.count())
                    if self.action_list.item(i).data(Qt.ItemDataRole.UserRole) is action), -1)
        if row < 0:
            return
        current_row = self.action_list.currentRow()
        was_current = self.image_viewer.current_action is action
        item = self.action_list.takeItem(row)
        card = self.action_list.itemWidget(item) if item else None
        if card is not None:
            card.deleteLater()
        del item
        self.actions.remove(action)
        self._transform_history_state = self._capture_transform_state()
        self._mark_project_dirty()
        self.image_viewer.set_actions(self.actions)

        if self.actions:
            if current_row > row:
                target_row = current_row - 1
            elif current_row == row:
                target_row = min(row, self.action_list.count() - 1)
            else:
                target_row = current_row
            if target_row >= 0:
                self.action_list.setCurrentRow(target_row)
        elif was_current:
            self.image_viewer.set_current_action(None)
            self.preview.set_action(None)
            self.lbl_status.setText("已删除动作，当前没有动作")
        self._push_snapshot_done()
        self._history_panel.rebuild()

    def _select_action_by_obj(self, action: Action):
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is action:
                self.action_list.setCurrentRow(i)
                break

    def select_action_for_history(self, action: Action) -> None:
        """历史面板跳转用：把某个动作切成当前动作（不额外标脏）。"""
        if self.image_viewer.current_action is action:
            return
        for i in range(self.action_list.count()):
            if self.action_list.item(i).data(Qt.ItemDataRole.UserRole) is action:
                self.action_list.setCurrentRow(i)
                break

    def _on_action_selected(self, row: int):
        if row < 0 or row >= len(self.actions):
            return
        action = self.actions[row]
        # 切动作前先把上一个动作还没落账的改动记下来（归属它自己的栈）
        if getattr(self, "_per_action_snap_timer", None) is not None and self._per_action_snap_timer.isActive():
            self._finish_per_action_snapshot()
        self._per_action_snap_text = ""
        self.image_viewer.set_current_action(action)
        # 裁剪帧
        self.image_viewer.crop_frames_for_action(action)
        self.preview.set_action(action)
        # 快照基线切到新动作，动作内记录之间互不串
        self._last_snapshot_state = self._capture_snapshot()
        self.lbl_status.setText(f"当前动作：{action.name}，{len(action.frames)} 帧")

    def _on_selection_changed(self):
        """选择图块：刷新帧 + 落账为一条动作内记录。"""
        if self.image_viewer.current_action:
            self.image_viewer.crop_frames_for_action(self.image_viewer.current_action)
            self.preview.set_action(self.image_viewer.current_action)
            self._transform_history_state = self._capture_transform_state()
            cur = self.image_viewer.current_action
            name = f"「{cur.name}」" if cur else ""
            n = len(cur.selected_indices) if cur else 0
            # 选择图块改变帧组成：debounce 后统一记录
            self._schedule_per_action_snapshot(f"选择图块{name}({n}个)")

    def _exit_preview_transform(self) -> None:
        """点了原图/动作列表/动画设置横条等"别处"时，收起右侧「帧编辑」的变换手柄。

        注意：**不要**用 app 级事件过滤器来做这件事 —— 全局过滤器会连着预览画布上的
        手柄点击一起吃掉（表现为"手柄悬浮动画正常、一点击就退出变换"），
        所以这里只挂少数几个明确入口（见 `_install_transform_exit_hooks`）。
        """
        panel = getattr(getattr(self, "preview", None), "adjust_panel", None)
        if panel is not None:
            panel._exit_transform_mode()

    def _install_transform_exit_hooks(self) -> None:
        """给"框外"的几处入口挂上"点这里就收起变换手柄"的钩子。

        用**局部事件过滤器**（只装在这几个控件上）而不是 app 级全局过滤器：
        全局过滤器会把预览画布上的手柄点击也一起吃掉 —— 用户实测"手柄悬浮动画正常、
        一点击就退出变换"，就是它干的。

        挂的对象只有：左侧动作列表的 viewport、底部「动画设置」横条及其子控件
        （原图区走 `ImageViewer.canvas_pressed` 信号）。预览区一律不挂。
        """
        targets = [self.action_list.viewport()]
        targets += list(self.anim_bar.findChildren(QWidget))
        targets.append(self.anim_bar)
        for w in targets:
            if w is not None:
                w.installEventFilter(self)

    def eventFilter(self, obj, event):
        """只有白名单里的那几个控件会走到这里（见 _install_transform_exit_hooks）。"""
        if event.type() == QEvent.Type.MouseButtonPress:
            self._exit_preview_transform()
        return super().eventFilter(obj, event)

    def _on_action_performed(self, text: str):
        """右键菜单操作：用具体文字记录到当前动作栈。"""
        if self.image_viewer.current_action is None:
            return
        # 数据已在右键方法里改完，before 用上次落账状态；先抓 before 再刷新界面
        before = self._last_snapshot_state
        after = self._capture_snapshot()
        self.image_viewer.crop_frames_for_action(self.image_viewer.current_action)
        self.preview.set_action(self.image_viewer.current_action)
        self._transform_history_state = self._capture_transform_state()
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        if before is not None and before != after:
            self._active_stack().push(_SnapshotCommand(self, before, after, f"{text}{name}"))
        self._last_snapshot_state = after

    def _on_before_full_action(self):
        """全量操作前调用：抓一份全量 before（数据还没改），存起来等 _on_action_performed_full 配对。"""
        self._pending_full_before = self._capture_snapshot(current_only=False)

    def _on_action_performed_full(self, text: str):
        """影响所有动作的右键操作（如合并为一整块重映射了全部动作的元素索引）：
        用全量快照记录，undo 才能把所有动作的索引和 viewer._frames 一起还原。"""
        if self.image_viewer.current_action is None:
            return
        before = getattr(self, "_pending_full_before", None)
        after = self._capture_snapshot(current_only=False)
        for action in self.actions:
            self.image_viewer.crop_frames_for_action(action)
        self.preview.set_action(self.image_viewer.current_action)
        self._transform_history_state = self._capture_transform_state()
        self.image_viewer.canvas.update()
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        if before is not None and before != after:
            self._active_stack().push(_SnapshotCommand(self, before, after, f"{text}{name}"))
        self._last_snapshot_state = after
        self._pending_full_before = None
        self._mark_project_dirty()

    # ------------------------------------------------------------- 动作内统一记录
    def _schedule_per_action_snapshot(self, text: str) -> None:
        """动作内任何数据改动（选择/偏移/动画参数/切帧）都走这里：

        只重启一个合并 timer。fire 时用「上次落账后的状态」当 before、
        「当前状态」当 after，有变化才 push —— 单次改动、交错改动都能正确记录，
        且 undo 一步只回退一步（不再跨步回滚）。
        """
        if getattr(self, "_suppress_selection_snapshot", False):
            return
        if self._per_action_snap_timer is None:
            self._per_action_snap_timer = QTimer(self)
            self._per_action_snap_timer.setSingleShot(True)
            self._per_action_snap_timer.timeout.connect(self._finish_per_action_snapshot)
        self._per_action_snap_text = text
        self._per_action_snap_timer.start(400)

    def _finish_per_action_snapshot(self) -> None:
        """动作内记录统一出口。"""
        text = self._per_action_snap_text
        self._per_action_snap_text = ""
        if getattr(self, "_suppress_selection_snapshot", False):
            return
        after = self._capture_snapshot()
        before = self._last_snapshot_state
        if before is not None and before != after:
            self._active_stack().push(_SnapshotCommand(self, before, after, text or "修改"))
        self._last_snapshot_state = after

    def _on_anim_bar_changed(self):
        """动画参数(fps/时长/锚点等)改动后 debounce 记录。"""
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        self._schedule_per_action_snapshot(f"修改动画参数{name}")

    def _on_frame_changed(self, idx: int):
        """预览区切换帧序号：并入同一合并窗口（切帧本身不改数据，不会多记）。"""
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        self._schedule_per_action_snapshot(f"切换帧{name} → 帧{idx+1}")

    def _on_frame_offset_dragged(self, idx: int, nx: int, ny: int):
        """画布上拖动微调帧偏移：debounce 后记录。"""
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        self._schedule_per_action_snapshot(f"微调帧偏移{name}")

    def _on_adjust_panel_changed(self):
        """adjust_panel 改动（偏移按钮/滑块/重置/时长…）：并入统一记录。

        注意：旋转/镜像等变换类改动会被 ``_on_transform_history_changed``
        先行落账并刷新快照基线，因此这里不会重复记录。
        """
        cur = self.image_viewer.current_action
        name = f"「{cur.name}」" if cur else ""
        self._schedule_per_action_snapshot(f"微调帧偏移{name}")

    def _grid_align_offsets(self, action):
        """路①：这张精灵图**横竖均分** → 每一格就是"正常那一帧的框"。

        用户口径：拖进来那些灰线是**紧切**的线，一个都不动；点对齐时参考的是整张精灵图的
        均分状态 —— 紧切框落在格里的哪个位置，就是它在原图里的哪个位置，跟"正常那一帧"
        一比差值（误差）就出来了。所以这条路**不是估的**，是读图上本来就有的信息。

        锚点算法跟平时一样：同一条公式对"格子"和"紧切框"各算一次，差值就是误差 ——
        不新增锚点选项，也不在画布上画均分线（用户明确说不用显示）。

        返回 (偏移字典, 说明)；判不出均分 / 有带变换或多元素合成的帧 → None（交给后面的路）。
        """
        viewer = self.image_viewer
        pixmap = getattr(viewer, "_pixmap", None)
        if pixmap is None or pixmap.isNull():
            return None
        total = len(action.frames)
        rects = action.frame_rects
        if total < 4 or len(rects) != total:
            return None
        for i in range(total):
            if action.frame_rotations.get(i) or i in action.frame_mirror_h \
                    or i in action.frame_mirror_v or action.frame_quads.get(i):
                return None               # 带变换的帧：帧内位置变了，先不掺和
            parts = action.frame_parts[i] if i < len(action.frame_parts) else None
            if not parts or len(parts) != 1:
                return None               # 多元素合成的帧：内容在帧内的原点不止一个
        index_of = detect_sheet_grid(rects, pixmap.width(), pixmap.height())
        if not index_of:
            return None
        cols, rows = grid_shape(index_of)
        canvas_w, canvas_h = pixmap.width(), pixmap.height()
        local_boxes, frame_sizes, content_in_frame = [], [], []
        for i in range(total):
            cell = cell_rect(index_of, i, canvas_w, canvas_h, cols, rows)
            r = rects[i]
            local_boxes.append((r[0] - cell[0], r[1] - cell[1], r[2], r[3]))
            frame_sizes.append(action_frame_size(action, i))
            part = action.frame_parts[i][0]
            content_in_frame.append((part[1], part[2]))
        max_w = max(s[0] for s in frame_sizes)
        max_h = max(s[1] for s in frame_sizes)
        offsets = compute_source_anchor_offsets(local_boxes, frame_sizes, content_in_frame,
                                                max_w, max_h, action.padding)
        return offsets, f"按精灵图均分格（{cols}×{rows}）把每帧放回它那一格里的位置（不是估的）"

    def _on_extract(self) -> None:
        """「抽帧」按钮：开抽帧窗口（500×600，预览 + 进度条 + 缩略图 + 4 个抽帧选项）。

        窗口只开一个、反复点就复用（跟预览一样，不叠一堆窗口）。非模态：开着窗口
        照样能操作主界面。选项按钮这版只占位、禁用并写明。
        """
        from spritesheet.ui.extract_dialog import ExtractDialog
        dialog = getattr(self, "_extract_dialog", None)
        if dialog is None:
            dialog = ExtractDialog(parent=self)
            self._extract_dialog = dialog     # 留着引用：绝不能让它被回收
        # 用户要求：抽帧窗口开了就**先处理它**，不能同时操作主界面 → 应用级模态
        self._extract_origin = None       # 每次打开对话框都重新记"抽帧前"的原始状态
        dialog.setModal(True)             # 用户要求：开了就**先处理它**，不能同时操作主界面
        dialog.show()                     # 先显示：刷新逻辑判了 isVisible，关着就不干活
        self._refresh_extract_dialog()
        dialog.raise_()
        dialog.activateWindow()

    def _refresh_extract_dialog(self) -> None:
        """抽帧窗口开着的时候，把颜色回调和当前动作推过去（关着就什么都不做）。

        整个包在 try 里：**Qt 槽里抛异常会直接把进程打死**（0xC0000409 那一类，
        连 traceback 都看不到），抽帧窗口刷不动绝不能连累主界面。这里出错就打一行日志。
        """
        try:
            dialog = getattr(self, "_extract_dialog", None)
            if dialog is None or not dialog.isVisible():
                return
            dialog.color_provider = self._similar_color_for_frame
            dialog.set_action(self.image_viewer.current_action)
        except Exception as e:  # noqa: BLE001
            print(f"[extract] 刷新抽帧窗口失败: {e!r}", file=sys.stderr)

    def _extract_state_fields(self):
        """抽帧要动的字段（每帧结构与选中元素都在这）。元素编号是全局的，绝不重排。"""
        return ("selected_indices", "composite_groups", "frame_layouts", "frame_layouts_base",
                "frame_offsets", "frame_durations", "frame_rotations", "frame_mirror_h",
                "frame_mirror_v", "frame_rotation_pivots", "frame_quads", "frame_transform_modes")

    def _capture_extract_state(self, action):
        import copy
        return {name: copy.deepcopy(getattr(action, name, None))
                for name in self._extract_state_fields()}

    def _restore_extract_state(self, action, state) -> None:
        for name, value in (state or {}).items():
            setattr(action, name, value)

    def _on_extract_decimate(self, mode: str, tolerance: int, from_current: bool = False,
                             progress=None):
        """抽帧窗口按档位抽帧：**实时**落到当前动作（可撤销），返回 (新帧数, 说明文字)。

        基准永远是**这个动作的所有帧**（打开窗口时记下的原始帧表）：换档位、调完相似度再抽，
        都先还原到那份原始帧表再算，不叠着抽、也不拿抽过一次的结果当基准
        （用户明确：「每次切换按钮 都是从这个动作所有帧选择而不是当前的帧数」）。

        为什么删的是"选中元素"而不是帧条目：帧是从 selected_indices / composite_groups /
        frame_layouts 派生出来的（image_viewer.crop_frames_for_action 重建），而且元素编号是
        全局的，删条目会让后面所有元素重编号（撤销、布局、合成组都得跟着重映射，不值当）。
        """
        from spritesheet.ui.similar import (compensate_offsets, decimate_indices, group_elements,
                                            plan_decimate, remap_frame_dict, remap_frame_set)
        from spritesheet.ui.transform import action_frame_pixmap, action_frame_size
        action = self.image_viewer.current_action
        if action is None or len(action.frames) < 2:
            return (len(action.frames) if action else 0, "帧太少，抽不动")
        if self._action_has_composite(action):
            # 用户口径：有合成帧时点了要提示"合并为一帧"。这里**只提示、不记快照、不改数据**，
            # 所以档位按钮点了也只是弹这句话，什么都不会被抽掉。
            return (len(action.frames),
                    "这个动作里有合成帧（多个元素并成了一帧），抽帧按元素抽，用不了")
        if getattr(self, "_extract_origin", None) is None:
            # 第一次抽：记下原始状态（换档还原用）+ 推一份撤销快照（Ctrl+Z 回到抽之前）
            self._extract_origin = (action, self._capture_extract_state(action))
            self._push_snapshot("抽帧", current_only=True)
        elif self._extract_origin[0] is action:
            self._restore_extract_state(action, self._extract_origin[1])
            # **必须重建帧**：还原的只是"派生源"（选中元素/合成组/布局/每帧字典），
            # 而 action.frames 里还留着上一次抽完的那份 —— 不重建的话下一档算的就是
            # "当前帧"而不是原始帧，四颗按钮来回切会越抽越少。
            # 用户口径：「这几个按钮始终是按照原动作……计算的帧数来用的」，
            # 用户实测的「切换就会混乱」就是这里漏了重建。
            action._transform_cache = {}
            action._base_size_cache = {}
            action._pivot_out_cache = {}
            self.image_viewer.crop_frames_for_action(action)
        else:
            self._extract_origin = (action, self._capture_extract_state(action))

        pixmaps = [action_frame_pixmap(action, i) for i in range(len(action.frames))]
        # 抽帧**绝不能让人物挪位置**：落点是 padding + (max−size)//2 + frame_offsets[i]，
        # max 是"本动作最大那一帧"的尺寸。抽掉最大的那帧后 max 变小，(max−size)//2 跟着变小，
        # 所有保留下来的帧就会整体往左上挪 —— 用户实测「抽帧以后后面的元素会位移」就是这个。
        # 所以这里先把旧尺寸/旧偏移记下来，重建后逐帧补偿，让每帧落点**逐帧不变**。
        sizes_old = {i: action_frame_size(action, i) for i in range(len(action.frames))}
        offsets_old = dict(action.frame_offsets)
        marks = group_elements(pixmaps, tolerance=int(tolerance), progress=progress)
        # 统计：抽帧四档分开计数。放在这一步（真开始抽了）而不是函数入口 ——
        # 被"有合成帧/帧太少"拦住的点击不该算成一次抽帧。
        telemetry.note(f"decimate_{mode}")
        keep = decimate_indices(pixmaps, marks, mode)
        plan = plan_decimate(len(pixmaps), list(action.selected_indices),
                             list(action.composite_groups or []), action.frame_layouts,
                             action.frame_layouts_base, keep,
                             # 元素总数要传：frame_source_table 必须跟 crop_frames_for_action
                             # 一样把"索引越界"的部件跳过，否则帧号会整体错位
                             n_elements=len(self.image_viewer._frames))
        if not plan["removed_frames"]:
            return (len(action.frames), "没有相邻的相似帧，这一档抽不掉东西")

        action.selected_indices = [i for i in action.selected_indices
                                   if i not in set(plan["drop_elements"])]
        if action.composite_groups:
            action.composite_groups = plan["keep_composite"]
        if action.frame_layouts is not None:
            action.frame_layouts = plan["keep_layouts"] or None
        action.frame_layouts_base = plan["base_after"]
        frame_map = plan["frame_map"]
        action.frame_offsets = remap_frame_dict(action.frame_offsets, frame_map)
        action.frame_durations = remap_frame_dict(action.frame_durations, frame_map)
        action.frame_rotations = remap_frame_dict(action.frame_rotations, frame_map)
        action.frame_rotation_pivots = remap_frame_dict(action.frame_rotation_pivots, frame_map)
        action.frame_quads = remap_frame_dict(action.frame_quads, frame_map)
        action.frame_mirror_h = remap_frame_set(action.frame_mirror_h, frame_map)
        action.frame_mirror_v = remap_frame_set(action.frame_mirror_v, frame_map)
        # 参照帧（洋葱皮）也是按帧号记的：**跟着重排**，被抽掉就清掉。
        # 光靠 crop_frames_for_action 那句"越界才清"不够 —— 帧数不变时它会留着，
        # 于是洋葱皮会指着另一帧，看起来像"某某帧被换了"。这是个很容易漏的每帧字段。
        _ref = int(getattr(action, "ref_frame", -1) or -1)
        action.ref_frame = frame_map.get(_ref, -1) if _ref >= 0 else -1
        if getattr(action, "frame_transform_modes", None):
            action.frame_transform_modes = remap_frame_dict(action.frame_transform_modes, frame_map)
        # 帧重建 + 缓存清掉（尺寸/变换缓存是按旧帧号算的，留着会错位）
        action._transform_cache = {}
        action._base_size_cache = {}
        action._pivot_out_cache = {}
        self.image_viewer.crop_frames_for_action(action)
        # 逐帧补偿：让每个保留帧的内容落点跟抽帧前**一模一样**（见上面 sizes_old/max_old）
        sizes_new = {i: action_frame_size(action, i) for i in range(len(action.frames))}
        action.frame_offsets = compensate_offsets(sizes_old, offsets_old, sizes_new, frame_map)
        self._push_snapshot_done()
        self.preview.set_action(action) if hasattr(self.preview, "set_action") else None
        return (len(action.frames),
                f"抽帧完成：{len(pixmaps)} → {len(action.frames)} 帧（Ctrl+Z 可撤回）")

    def _commit_extract_state(self) -> None:
        """点「确定」：这一轮抽帧就算定了，下次打开窗口重新记原始状态。"""
        self._extract_origin = None

    def _cancel_extract_state(self):
        """点「取消」：把这一轮抽帧整个撤回到打开窗口时的样子。"""
        origin = getattr(self, "_extract_origin", None)
        self._extract_origin = None
        if origin is None:
            return (0, "没有要撤回的抽帧")
        action, state = origin
        self._restore_extract_state(action, state)
        action._transform_cache = {}
        action._base_size_cache = {}
        action._pivot_out_cache = {}
        self.image_viewer.crop_frames_for_action(action)
        self.preview.set_action(action) if hasattr(self.preview, "set_action") else None
        return (len(action.frames), "已撤回这次抽帧")

    def _space_pan_targets(self):
        """空格要喂「正在拖」状态的预览控件：右侧预览画布 + 抽帧窗口的预览（开着才有）。

        这两个控件都有 `_panning` / `_pan_start` 与左键拖动逻辑，喂状态即可，不用它们拿焦点。
        """
        targets = [getattr(self.preview, "canvas", None)]
        dialog = getattr(self, "_extract_dialog", None)
        if dialog is not None:
            targets.append(getattr(dialog, "preview", None))
        return targets

    def _action_has_composite(self, action) -> bool:
        """这个动作里有没有"合成帧"（多个元素并成一帧）。

        三种来源都算：composite_groups / 帧里有多个部件 / 导入布局里一帧列了多个部件。
        用户在意的就是这类帧 —— 它们没有"单独一个元素"，所以相似色块标不出来，
        抽帧也没法按元素抽（用户口径：有合成帧就灰 / 点了就提示"合并为一帧"）。
        """
        if action is None:
            return False
        if getattr(action, "composite_groups", None):
            return True
        for parts in (getattr(action, "frame_parts", None) or ()):
            if len(parts) > 1:
                return True
        for layout in (getattr(action, "frame_layouts", None) or ()):
            if len(layout) > 1:
                return True
        return False

    def _extract_enable_state(self):
        """「抽帧」能不能按。

        动图文档模式下 `self.actions` 是空的、`current_action` 是 None（GIF/WebP 是"单张图片
        修改"那条路，没有动作），抽帧窗口开了也没有动作可抽 —— 所以这里直接禁用并写明原因。
        这也是为什么不需要给动图文档的操作链加"抽帧 op"：那种模式压根没有抽帧这件事。
        """
        if self._anim_doc is not None:
            return False, "这是动图文档（不是动作工程），没有可抽的动作"
        if self.image_viewer.current_action is None:
            return False, "先选一个动作"
        return True, ""

    def _similar_enable_state(self):
        """「相似帧」能不能按：原图上元素 ≥2 就能（不用先加动作，用户要求直接看原图也行）。

        但**当前动作里有合成帧时不给按** —— 合成帧是多个元素并成一帧，没有"一个元素"
        可以标色，按下去那些帧也只是空白（用户口径：有合成帧这个按钮就灰）。
        """
        if self._action_has_composite(self.image_viewer.current_action):
            return False, "当前动作里有合成帧（多个元素并成了一帧），彩块标不了"
        count = len(getattr(self.image_viewer, "_frames", None) or ())
        if count >= 2:
            return True, ""
        if count == 1:
            return False, "原图上只有 1 个元素，没有可比的帧"
        return False, "原图上还没有元素（先拖一张图进来）"

    def _similar_color_for_frame(self, action, i: int):
        """帧缩略图问色：这一帧对应的元素属于哪个相似组（找不到就返回 None，不标）。"""
        marks = self.image_viewer.similar_marks
        if not marks or action is None:
            return None
        rects = action.frame_rects
        if i < 0 or i >= len(rects):
            return None
        r = rects[i]
        key = (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
        idx = getattr(self, "_similar_index_of", {}).get(key)
        return marks.get(idx) if idx is not None else None

    def _similar_group_of_frame(self, action, i: int):
        """这一帧的元素属于第几组相似帧（没组就 None）—— 循环长度提示要用组号。"""
        rects = action.frame_rects
        if i < 0 or i >= len(rects):
            return None
        r = rects[i]
        key = (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
        idx = getattr(self, "_similar_index_of", {}).get(key)
        return getattr(self, "_similar_groups", {}).get(idx)

    def _on_similar_toggled(self, on: bool) -> None:
        """「相似帧」开关：按下 = 全图元素两两比、同组标同色；松开 = 两处标记一起清掉。

        用户口径：① 阈值在软件设置里（默认 0 = 一模一样）；② 全图识别，新加的图也一起找；
        ③ 不给文字信息，只用颜色；④ 缩略图色块在右上角（右下角是帧序号）；
        ⑤ 用途是找循环帧 —— 所以状态栏顺手报出最可能的循环长度。
        **纯显示**：一个字节的工程数据都不改，所以不落撤销、也不需要撤销。
        """
        from spritesheet.settings import similar_tolerance
        from spritesheet.ui.similar import group_color, group_elements, loop_hint
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication, QProgressDialog

        viewer = self.image_viewer
        if not on:
            viewer.similar_marks = {}
            self._similar_index_of = {}
            self._similar_groups = {}
            self.preview.similar_color_provider = None      # 缩略图问色的是预览控件
            self.preview._refresh_thumbs()
            viewer.update()
            self.lbl_status.setText("相似帧：已松开，标记清掉了")
            return

        frames = list(viewer._frames or ())
        pixmap = getattr(viewer, "_pixmap", None)
        pixmaps = []
        for r in frames:
            x, y, w, h = int(r[0]), int(r[1]), int(r[2]), int(r[3])
            pixmaps.append(pixmap.copy(x, y, w, h)
                           if pixmap is not None and not pixmap.isNull() else None)
        tolerance = similar_tolerance()
        # 元素多的时候（你那张 289 个）要几百毫秒到一两秒，先弹个**忙碌进度条**说明在干活，
        # 免得看起来像没反应（用户要求"出个模态窗口或者进度条提示正在检测"）。
        progress = QProgressDialog("正在检测相似帧……", "", 0, len(pixmaps), self)
        progress.setWindowTitle("相似帧")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setCancelButton(None)          # **真进度条**（用户要求）：不给取消，算完就关
        progress.setMinimumDuration(0)
        progress.setFixedWidth(320)
        progress.setValue(0)
        self._similar_progress = progress       # 留引用：绝不能 deleteLater 一个还带着窗口的对象
        progress.show()
        QApplication.processEvents()

        def _tick(done, all_units):
            # 计算期间在**应用级模态**下刷进度：用户点不到主界面，所以这里 processEvents 是安全的
            progress.setMaximum(max(1, int(all_units)))
            progress.setValue(min(int(done), int(all_units)))
            progress.setLabelText(f"正在检测相似帧…… {min(int(done), int(all_units))}"
                                  f"/{int(all_units)}")
            QApplication.processEvents()

        groups = group_elements(pixmaps, tolerance, progress=_tick)
        telemetry.note("similar_frames")       # 统计：相似帧算了一次
        progress.close()
        QApplication.processEvents()
        self._similar_index_of = {(int(r[0]), int(r[1]), int(r[2]), int(r[3])): i
                                  for i, r in enumerate(frames)}
        self._similar_groups = dict(groups)
        viewer.similar_marks = {i: group_color(g) for i, g in groups.items()}
        self.preview.similar_color_provider = self._similar_color_for_frame
        self.preview._refresh_thumbs()
        viewer.update()

        if not groups:
            self.lbl_status.setText(
                f"相似帧：全图 {len(frames)} 个元素里没有长得一样的"
                f"（容差 {tolerance}%，设置里可调）")
            return
        group_count = len(set(groups.values()))
        action = viewer.current_action
        hint = ""
        if action is not None and len(action.frames) >= 2:
            hint = loop_hint({i: g for i in range(len(action.frames))
                              if (g := self._similar_group_of_frame(action, i)) is not None})
        text = f"相似帧：找到 {group_count} 组相似帧（共 {len(groups)} 帧）"
        if hint:
            text += f"；{hint}"
        self.lbl_status.setText(text)

    def _auto_align_reason(self, action) -> str:
        """能不能按「原图锚点」**精确**补回偏移；不能就说清是为什么。

        用户要求"你可以自己确定"，所以这里全部自己看、不弹窗问：
          * 用到的原图必须是**同尺寸的整帧画面** —— 尺寸不一致，或者原图的 alpha 外框
            几乎顶满原图本身（说明原图自己就已经是紧切过的小图），精确就无从谈起
          * 抽查最多 8 帧就够判断（几百帧逐个扫会把界面卡住）
        返回 "" = 精确可用；否则是一句人话原因。
        """
        viewer = self.image_viewer
        if viewer is None or not getattr(viewer, "_source_images", None):
            return "这一份工程里没有可用的原图（帧是直接拼出来的）"
        total = len(action.frames)
        picked = sorted({int(round(k * (total - 1) / max(1, min(8, total) - 1)))
                         for k in range(min(8, total))}) if total > 1 else [0]
        rects = viewer.source_rects()
        if not rects:
            return "拿不到原图的坐标（工程里没存原图位置）"
        sizes = set()
        for i in picked:
            frect = action.frame_rects[i] if i < len(action.frame_rects) else None
            if not frect:
                return "有帧记不清自己是从哪块原图切下来的"
            cx, cy = frect[0] + frect[2] / 2.0, frect[1] + frect[3] / 2.0
            sx = sy = None
            for (rx, ry, rw, rh) in rects:
                if rx <= cx < rx + rw and ry <= cy < ry + rh:
                    sx, sy = rx, ry
                    break
            if sx is None:
                return "有帧落不到任何一张原图里（工程状态对不上）"
            idx = (viewer._source_offsets.index((sx, sy))
                   if (sx, sy) in list(viewer._source_offsets or []) else None)
            if idx is None or idx >= len(viewer._source_images):
                return "原图和合成图对不上号，算不准"
            pm = viewer._source_images[idx]
            if pm is None or pm.isNull():
                return "有一张原图读不出来了"
            sizes.add((pm.width(), pm.height()))
            if len(sizes) > 1:
                return "这些原图的尺寸不一样，不像「一整帧完整画面」"
            bounds = _alpha_bounds(pm)
            # 外框几乎顶满原图 = 原图自己就是紧切过的小图（正常切割那个框已经不存在了）
            if bounds is not None and bounds.width() >= pm.width() * 0.98 \
                    and bounds.height() >= pm.height() * 0.98:
                return "原图本身就已经是紧切过的小图（四周没有留白）"
        return ""

    def _source_anchor_data(self, action):
        """精确那条路要用的数据：每帧内容在**原图坐标系**里的框 + 帧内偏移。

        返回 None = 有帧满足不了（多元素合成的帧、带变换的帧）；调用方据此走估算路。
        """
        viewer = self.image_viewer
        rects = viewer.source_rects()
        total = len(action.frames)
        local_boxes: list[tuple[int, int, int, int] | None] = [None] * total
        content_in_frame: list[tuple[int, int] | None] = [None] * total
        frame_sizes: list[tuple[int, int] | None] = [None] * total
        for i in range(total):
            if action.frame_rotations.get(i) or action.frame_mirror_h & {i} \
                    or action.frame_mirror_v & {i} or action.frame_quads.get(i):
                return None                      # 带变换的帧：帧内位置变了，先不猜
            parts = action.frame_parts[i] if i < len(action.frame_parts) else None
            if not parts or len(parts) != 1:
                return None                      # 多元素合成的帧：帧内内容位置不止一个
            (_src, rx, ry, _sx, _sy) = parts[0]
            frect = action.frame_rects[i] if i < len(action.frame_rects) else None
            if not frect:
                return None
            cx, cy = frect[0] + frect[2] / 2.0, frect[1] + frect[3] / 2.0
            origin = None
            for (ox, oy, ow, oh) in rects:
                if ox <= cx < ox + ow and oy <= cy < oy + oh:
                    origin = (ox, oy)
                    break
            if origin is None:
                return None
            local_boxes[i] = (frect[0] - origin[0], frect[1] - origin[1], frect[2], frect[3])
            content_in_frame[i] = (rx, ry)
            frame_sizes[i] = action_frame_size(action, i)
        max_w = max(s[0] for s in frame_sizes if s)
        max_h = max(s[1] for s in frame_sizes if s)
        return local_boxes, frame_sizes, content_in_frame, max_w, max_h

    def _confirm_estimate_align(self, reason: str) -> bool:
        """只能估算时先弹个提示：说清为什么估、可能不准、建议手动核对。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("自动对齐（只能估算）")
        box.setText("这次只能估算，可能不准 —— 要不要继续？")
        box.setInformativeText(
            f"{reason}\n\n"
            "原始位置（正常切割那个框）已经不在工程里了，只能按帧间重合度去猜。\n"
            "「身型展开」的那几帧（尾巴/手臂伸出去、紧切框变大）仍可能对不齐，"
            "点完之后建议对着预览手动核对、必要时拖一下那几帧的逐帧偏移。\n\n"
            "只改逐帧偏移数据，锚点 / padding / 帧图像都不动，一条 Ctrl+Z 可退回。")
        use_it = box.addButton("用估算值", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(use_it)
        box.exec()
        return box.clickedButton() is use_it

    def _on_auto_align(self) -> None:
        """「自动对齐」：算好逐帧偏移写回去。

        用户口径：**只改帧的偏移数据** —— 锚点、padding、帧图像、尺寸一概不碰。
        三条路，自己判断走哪条（用户要求"你可以自己确定"）：
          * **均分格**（首选）：这张精灵图本身是横竖均分的 → 每格 = 正常那一帧的框，
            紧切框在格里的落点就是它在原图里的落点，差值（误差）就是偏移。**不是估的**。
          * **精确**：原图是整帧画面时，按「原图锚点 − 紧切框中心」把每帧放回它在原图里的位置
            （用户原话："正常切割记一次锚点、紧切再记一次，差值就是偏移量"）。
          * **估算**：原图已经是紧切小图 / 带变换 / 多元素合成时，原始位置已经丢了，
            只能按帧间重合度去猜 —— 先弹提示说明"可能不准、建议手动核对"。
        一条撤销：改前/改后当场各抓一份快照，Ctrl+Z 能退回对齐前的偏移。
        """
        action = self.image_viewer.current_action
        if action is None or len(action.frames) < 2:
            self.lbl_status.setText("自动对齐：先选一个动作（至少两帧）")
            return
        # 真进度条（用户要求）：对齐要逐帧量内容/取投影，元素多时会等几秒，
        # 所以先摆出一条确定性进度条，算哪条路就报哪条路的进度。
        from PyQt6.QtWidgets import QProgressDialog
        prog = QProgressDialog("正在自动对齐……", "", 0, max(2, len(action.frames)), self)
        prog.setWindowTitle("自动对齐")
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setCancelButton(None)          # 不给取消：算完就关（应用级模态下刷进度是安全的）
        prog.setMinimumDuration(0)
        prog.setValue(0)
        self._align_progress = prog         # 留引用：带着窗口对象被回收是老坑
        prog.show()
        QApplication.processEvents()

        def _align_tick(done, all_units):
            prog.setMaximum(max(1, int(all_units)))
            prog.setValue(min(int(done), int(all_units)))
            prog.setLabelText(f"正在自动对齐…… {min(int(done), int(all_units))}/{int(all_units)}")
            QApplication.processEvents()

        # 路①：这张精灵图本身是「横竖均分」的 → 每一格就是正常那一帧的框，
        # 紧切框在格里的位置就是原图里的位置，差值就是误差（用户口径，不是估的）
        prog.setLabelText("正在自动对齐…… 判断原图是不是均分格")
        QApplication.processEvents()
        grid_offsets = self._grid_align_offsets(action)
        if grid_offsets is not None:
            offsets, how = grid_offsets
            tail = "锚点、padding、帧图像都没动 · Ctrl+Z 可退回"
        else:
            reason = self._auto_align_reason(action)
            data = None if reason else self._source_anchor_data(action)
            if data is None and not reason:
                reason = "这些帧是合成的或带了变换，原图里的位置推不出来"
            if data is not None:
                local_boxes, frame_sizes, content_in_frame, max_w, max_h = data
                prog.setLabelText("正在自动对齐…… 按原图锚点把每帧放回去")
                QApplication.processEvents()
                offsets = compute_source_anchor_offsets(
                    local_boxes, frame_sizes, content_in_frame, max_w, max_h, action.padding)
                how = "精确：按「原图锚点 − 紧切框中心」把每帧放回它在原图里的位置"
                tail = "锚点、padding、帧图像都没动 · Ctrl+Z 可退回"
            else:
                if not self._confirm_estimate_align(reason):
                    prog.close()             # 估算那条路用户点了取消：进度条必须收掉再退出
                    self.lbl_status.setText("自动对齐：已取消（只能估算的那条路）")
                    return
                prog.setLabelText("正在自动对齐…… 按帧间重合度估算（逐帧量内容）")
                QApplication.processEvents()
                offsets = compute_align_offsets(action, progress=_align_tick)
                how = f"估算（{reason}）：按帧间重合度对齐"
                tail = "锚点、padding、帧图像都没动 —— 估的，建议手动核对那几帧 · Ctrl+Z 可退回"
        prog.close()                        # 偏移算完了，进度条立刻收（写数据是瞬时的）
        if not offsets:
            self.lbl_status.setText("自动对齐：这些帧本来就在该在的位置上，偏移没变")
            return
        changed_offsets = {i: v for i, v in offsets.items()
                           if action.frame_offsets.get(i, (0, 0)) != v}
        if not changed_offsets:
            # 原图就是整帧时算出来全是 0：本来就不飘，别写一堆没用的 (0,0) 进工程
            self.lbl_status.setText("自动对齐：这些帧本来就在该在的位置上，偏移没变")
            return
        merged = dict(action.frame_offsets)
        merged.update(offsets)
        changed = len(changed_offsets)
        # 改前/改后**当场各抓一份**（不靠动作内那条 400ms 合并 timer 的旧基线）：
        # 按钮是一次点击、一锤子买卖，快照里必须带着"改之前的样子"，
        # 否则 Ctrl+Z 可能退到一个跟当前动作对不上的旧状态
        self._push_snapshot("自动对齐", current_only=True)
        # 统计：只有"真的改了偏移"才算一次自动对齐（上面那些"本来就在该在的位置上"
        # 的早退不算，否则计数会把"点了没事发生"也记进去）
        telemetry.note("auto_align")
        action.frame_offsets = merged
        action.invalidate_transforms()
        self.preview.set_action(action)
        self.preview.update()
        if self.preview.canvas is not None:
            self.preview.canvas.update()
        self.preview.adjust_panel._refresh_canvas_label()
        self._push_snapshot_done()
        self._mark_project_dirty()
        self.lbl_status.setText(f"已自动对齐：{changed} 帧的逐帧偏移改了（{how}）；{tail}")

    def _on_frames_updated(self):
        """后台检测完成后刷新动作帧和界面摘要。"""
        for action in self.actions:
            self.image_viewer.crop_frames_for_action(action)
        if self.image_viewer.current_action:
            self.preview.set_action(self.image_viewer.current_action)
        if self.image_viewer._pixmap and not self.image_viewer._pixmap.isNull():
            self.lbl_status.setText(
                f"已完成元素检测：当前共检测到 {len(self.image_viewer._frames)} 个元素"
            )
        self._transform_history_state = self._capture_transform_state()
        # 检测完成后原图元素变了：①「相似帧」的可用性刷一遍；② 开关还按着的话重算一次
        # —— 用户要求「全图识别（有别的新图也要找）」，新加图的元素自动进来。
        try:
            self.preview.adjust_panel._sync_align_buttons()
            if self.preview.adjust_panel.btn_similar_frames.isChecked():
                self._on_similar_toggled(True)
        except Exception as e:  # noqa: BLE001 — 标色出问题不能影响检测后的正常流程
            print(f"[similar] 检测后重算相似帧失败: {e}", file=sys.stderr)
        # 检测完成后帧集合变了：快照基线跟着刷到最新，否则之后第一次动作内改动
        # 会拿"检测前"的旧状态当 before（增量算错，甚至整条记录被吞）
        self._last_snapshot_state = self._capture_snapshot()
        self._mark_project_dirty()
