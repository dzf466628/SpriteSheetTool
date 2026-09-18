#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据模型：动作 Action 与主题色常量。

（从 main_window.py 拆出，纯搬移，逻辑未改动）
动作 = 一组帧图片 + 主题色 + 选中的元素序号（有序）+ 动画调节参数。
"""
from __future__ import annotations

from PyQt6.QtGui import QPixmap


class Action:
    """动作：一组帧图片 + 主题色 + 选中的元素序号（有序）+ 动画调节参数。"""
    # 循环模式
    LOOP = "loop"
    ONCE = "once"
    PINGPONG = "pingpong"

    def __init__(self, name: str = "未命名动作", color: str = "#E91E63") -> None:
        self.name: str = name
        self.color: str = color
        self.frames: list[QPixmap] = []
        self.selected_indices: list[int] = []  # 选中的元素索引，有序（点击/框选顺序）
        # 合成帧：每个内层列表是要合并为一帧的源元素索引。
        self.composite_groups: list[list[int]] = []
        # 显式帧布局（仅从 JSON 导入的工程使用）：
        # 每帧 = [(元素索引, 帧内 x, 帧内 y), ...]。
        # 不为 None 时 crop_frames_for_action 直接按它逐帧生成，
        # 从而支持「同一元素参与多个不同合成帧」——这是 composite_groups
        # （一个元素只能属于一个组）无法表达的情况，也是导入不丢帧的关键。
        # 用户重新选择元素后该布局会被清空，回到常规生成方式。
        self.frame_layouts: list[list[tuple[int, int, int]]] | None = None
        # 布局中「基线帧」的数量（索引 < base 的是常规/导入生成的原始帧，
        # 索引 >= base 的是用户复用的帧，Alt+点击可取消）。
        self.frame_layouts_base: int = 0
        # 每帧在原图中的包围盒 (x,y,w,h)，与 frames 同序
        self.frame_rects: list[tuple[int, int, int, int]] = []
        # 每帧的组成部件：[[(源图裁剪矩形(x,y,w,h), 帧内相对 x, y, 元素逻辑坐标 x, y), ...], ...]
        # 与 frames 同序。裁剪矩形决定"从图里取哪块像素"，
        # 逻辑坐标决定"元素之间的相对位置"（合成帧的拼接间距）——
        # 二者在正常情况下相同，但从 JSON 导入的工程里会不同（图块被重新打包过），
        # 因此必须分开记录，导出时才能把元素层的真实关系写回 JSON。
        self.frame_parts: list[list[tuple[tuple[int, int, int, int], int, int, int, int]]] = []
        # ---- 动画调节参数（每个动作独立保存）----
        self.fps: int = 10
        self.loop_mode: str = Action.LOOP
        self.padding: int = 0  # 统一画布四周额外扩展像素
        self.ground_enabled: bool = False
        self.ground_offset: int = 0  # 地面线相对自动位置（主体最底部）的偏移
        self.reuse_frame_settings: bool = True  # 与其他勾选动作复用帧设置
        self.show_anchor: bool = False
        # 每个动作独立的逻辑画布锚点；归一化坐标，左上角为 (0, 0)。
        self.anchor: tuple[float, float] = (0.5, 0.5)
        self.frame_offsets: dict[int, tuple[int, int]] = {}  # 单帧偏移 {idx: (dx,dy)}
        # 单帧显示时长（毫秒），只存被改过的帧；未记录的帧按 fps 推导。
        self.frame_durations: dict[int, int] = {}
        # 每帧旋转与镜像（帧设置里编辑；勾选"数据复用"时整组帧一起改）
        self.frame_rotations: dict[int, int] = {}  # {帧索引: 旋转角度(度, 0-359)}
        self.frame_mirror_h: set[int] = set()      # 水平镜像（左右翻转）的帧索引
        self.frame_mirror_v: set[int] = set()      # 垂直镜像（上下翻转）的帧索引
        # 每帧旋转中心，归一化到“镜像后、旋转前”的帧尺寸。
        # 默认中心不写入字典；它只影响旋转，不会改变动作的逻辑锚点。
        self.frame_rotation_pivots: dict[int, tuple[float, float]] = {}
        # 每帧自由变换四边形（透视/斜切/扭曲/缩放共用），归一化 0-1 相对帧宽高：
        # (x0,y0, x1,y1, x2,y2, x3,y3)，角序 = 矩形四角 (0,0)(1,0)(1,1)(0,1)。
        # 缺省 = (0,0, 1,0, 1,1, 0,1)，即无四边形变换。
        self.frame_quads: dict[int, tuple[float, float, float, float, float, float, float, float]] = {}
        # 仅用于界面摘要，记录用户在该帧使用过的四边形变换模式。
        self.frame_transform_modes: dict[int, set[str]] = {}
        self._transform_cache: dict = {}           # (idx,rot,mh,mv,pivot,quad) -> 变换后的 QPixmap
        # (idx,rot,mh,mv,pivot) -> 未加 quad 的帧尺寸缓存（交互路径高频调用）
        self._base_size_cache: dict = {}
        # (idx,rot,mh,mv,pivot) -> 旋转中心在基础图像里的坐标（交互路径高频调用）
        self._pivot_out_cache: dict = {}
        self.ref_frame: int = -1  # 参照帧索引（洋葱皮）
        self.ref_opacity: float = 1.0  # 参照帧透明度（固定，不透明）
        self.frame_opacity: float = 1.0  # 当前帧透明度
        self.bg_mode: str = "checker"  # checker/black/white 预览背景
        # ---- 动作类型（静止/移动）----
        self.action_type: str = "static"  # static / move
        self.move_direction: str = "right"  # up/down/left/right
        self.move_speed: int = 10  # 1-100
        # 每个动作独立的 undo 栈（懒创建，由 MainWindow 赋值 QUndoStack）
        self.undo_stack = None

    def frame_duration_ms(self, idx: int) -> int:
        """该帧显示时长（毫秒）：逐帧覆盖优先，缺省由 fps 推导。"""
        ms = self.frame_durations.get(idx)
        if ms is not None:
            return max(1, int(ms))
        return max(1, round(1000 / max(1, self.fps)))

    def invalidate_transforms(self) -> None:
        """旋转中心/旋转/镜像或帧内容变化后清空变换缓存。"""
        self._transform_cache.clear()
        self._base_size_cache.clear()
        self._pivot_out_cache.clear()


# 24 色深色系：色相均匀覆盖 360°（每 15° 一个），S=0.78 / L=0.50，
# 成对最小 RGB 距离 ≈1250，保证任意两色一眼可分。
MACARON_COLORS = [
    "#E3351C",
    "#E3671C",
    "#E3981C",
    "#E3CA1C",
    "#CAE31C",
    "#98E31C",
    "#67E31C",
    "#35E31C",
    "#1CE335",
    "#1CE367",
    "#1CE398",
    "#1CE3CA",
    "#1CCAE3",
    "#1C98E3",
    "#1C67E3",
    "#1C35E3",
    "#351CE3",
    "#671CE3",
    "#981CE3",
    "#CA1CE3",
    "#E31CCA",
    "#E31C98",
    "#E31C67",
    "#E31C35",
]
