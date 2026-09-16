# SpriteSheetTool 代码拆分指南（SPLIT_GUIDE）

> **状态：拆分已完成（步骤 1-7 全部执行并验证）。** 本文档保留为拆分记录与
> 后续维护依据；新功能请直接改对应模块，不要再让 `main_window.py` 膨胀。

---

## 1. 现状（拆分完成后）

| 文件 | 行数 | 大小 | 备注 |
| --- | --- | --- | --- |
| `spritesheet/ui/main_window.py` | **1192** | 56 KB | 拆分前 6222 行 → 只留 MainWindow + 更新弹窗 + 撤销命令 |
| `spritesheet/ui/preview.py` | 1948 | 89 KB | 预览画布 / 自由变换手柄 / 动画调节组 / 预览窗口 |
| `spritesheet/ui/image_viewer.py` | 1336 | 64 KB | 元素检测 / 标尺画布 / 浮动工具条 / 帧复用 |
| `spritesheet/ui/export_dialog.py` | 1160 | 56 KB | 导出弹窗 / 预览区 / 图集打包 / GIF / JSON |
| `spritesheet/ui/widgets.py` | 694 | 28 KB | 通用控件 / 图标工具 / 棋盘格背景 |
| `spritesheet/ui/theme.py` | 377 | 10 KB | 深色玫红主题 QSS |
| `spritesheet/ui/transform.py` | 266 | 11.5 KB | 帧变换/预览布局纯函数 |
| `spritesheet/importer.py` | 259 | 10 KB | JSON 工程导入 |
| `spritesheet/ui/exporters.py` | 165 | 7.6 KB | 第三方 JSON 导出器 |
| `spritesheet/updater.py` | 131 | 5.8 KB | 自动更新 |
| `spritesheet/ui/models.py` | 114 | 5.8 KB | Action 数据模型 + 主题色 |
| `spritesheet/project_file.py` | 110 | 4.3 KB | .sstool 读写 + QPixmap 片段辅助 |

## 2. 最终结构

```
spritesheet/ui/
├── __init__.py          # 空
├── main_window.py       # MainWindow + UpdateDownloadDialog + _TransformHistoryCommand
├── models.py            # Action 数据模型 + MACARON_COLORS + 常量
├── transform.py         # 变换/布局纯函数（无 UI 依赖）
├── widgets.py           # 通用小部件 + 图标/像素工具 + 棋盘格背景
├── image_viewer.py      # ImageViewer + _Canvas + FloatingToolBar + _AltNativeFilter + 检测
├── export_dialog.py     # ExportDialog + PreviewArea + _safe_export_name + _pixmap_to_pil
├── preview.py           # PreviewFloatingBar/PreviewCanvas/AnimAdjustPanel/ActionPreviewWidget
├── exporters.py         # 不变
└── theme.py             # 不变
```

依赖方向（**运行时单向，禁止反向 import**）：

```
models ← transform ← widgets ← image_viewer / export_dialog ← preview ← main_window
```

`spritesheet/project_file.py` 另收 `_rect_from_dict` / `_pixmap_bytes` / `_pixmap_from_bytes`
（工程序列化语义，按 §5.3 归属）。


## 3. 依赖侦察结果（本次实测，拆分依据）

### 3.1 外部依赖（只有这些入口引用 main_window）
- `main.py` → `from spritesheet.ui.main_window import MainWindow`（唯一外部引用）
- `theme.py`、`exporters.py`：**不依赖** main_window（exporters 设计约束"不 import 主窗口"）
- 因此：拆分期间 main_window.py 必须始终保留 `MainWindow` 名字。

### 3.2 模块内交叉引用（拆分前侦察记录：行号属于拆分前的旧文件，仅作历史参考）

| 被引用者 | 引用处（行号） | 归属建议 |
| --- | --- | --- |
| `Action`（类，183-251） | 全文件几乎所有类 | **models.py**（第一步，零依赖） |
| `MACARON_COLORS`（常量） | 6043, 6179（MainWindow 配色） | **models.py** |
| `DEFAULT_QUAD` / `DEFAULT_ROTATION_PIVOT` | 变换函数 + PreviewCanvas 多处 | **transform.py** |
| 变换函数（`transform_frame_pixmap` / `action_frame_pixmap` / `compute_preview_layout` 等，975-1193） | PreviewCanvas / ExportDialog / ActionThumbPlayer / ActionPreviewWidget / ImageViewer | **transform.py** |
| `make_tool_icon`（68） | 15 处：工具栏/按钮/卡片等 | **widgets.py** |
| `make_logo_pixmap`（44） | 5688（MainWindow） | **widgets.py** |
| `detect_frames` / `_detect_frames_image`（104-161） | ImageViewer.set_pixmap | **image_viewer.py** |
| `_tick_interval`（94） | 455（_Ruler） | **widgets.py**（随 _Ruler） |
| `_pixmap_bytes` / `_pixmap_from_bytes`（1176-1193） | MainWindow（5375-5616） | **project_file.py** 或 models.py |
| `_rect_from_dict`（1170） | 6024（MainWindow） | project_file.py |
| `_safe_export_name` / `_pixmap_to_pil`（3114-3139） | ExportDialog 内部 | **export_dialog.py** |
| `ColorPickerDialog`（254） | 389（ActionCardWidget） | **widgets.py** |
| `ActionCardWidget`（285） | MainWindow（5985-6194） | **widgets.py** |
| `ActionItemDelegate`（408） | 5735（MainWindow） | **widgets.py** |
| `_Ruler`（431） | 4575,4578（ImageViewer） | **widgets.py** |
| `FloatingToolBar`（860） | 4588,4589（ImageViewer） | **image_viewer.py** |
| `PreviewFloatingBar`（1194） | 4159（ActionPreviewWidget） | **preview.py** |
| `PreviewCanvas`（1299） | 4158（ActionPreviewWidget） | **preview.py** |
| `CompactComboBox/EditableNumberLabel/CompactSpinBox/SwitchWidget` | AnimAdjustPanel / ExportDialog | **widgets.py** |
| `ActionThumbPlayer`（2923） | 3161,3207（ExportDialog） | **widgets.py** |
| `AnimAdjustPanel`（2216） | 4246（ActionPreviewWidget） | **preview.py** |
| `PreviewArea`（3008） | 3250（ExportDialog 左侧卡片区） | **export_dialog.py** |
| `ActionPreviewWidget`（4134） | 5759（MainWindow） | **preview.py** |
| `ImageViewer`（4535） | 845（_Canvas 转发）、5750（MainWindow） | **image_viewer.py** |
| `_TransformHistoryCommand`（5234） | MainWindow 内部 | 留 main_window.py |

### 3.3 拆分后各定义所在模块（查代码用这张表）

| 定义 | 所在模块 |
| --- | --- |
| `Action` / `MACARON_COLORS` | `ui/models.py` |
| `transform_frame_pixmap` / `action_frame_pixmap` / `action_frame_size` / `frame_base_size` / `compute_preview_layout` / `action_anchor_pixels` / `_alpha_bounds` / `_rotation_transform` / `_normalized_rotation_pivot` / `DEFAULT_QUAD` / `DEFAULT_ROTATION_PIVOT` | `ui/transform.py` |
| `make_tool_icon` / `make_logo_pixmap` / `_tick_interval` / `_APP_ROOT` / `_ICONS_DIR` / `RULER_SIZE` | `ui/widgets.py` |
| `ColorPickerDialog` / `ActionCardWidget` / `ActionItemDelegate` / `_Ruler` / `SwitchWidget` / `CompactComboBox` / `CompactSpinBox` / `EditableNumberLabel` / `ActionThumbPlayer` / `_SLIDER_QSS` / `_GROUP_QSS` / `paint_checkerboard` / `checker_tile` | `ui/widgets.py` |
| `SUPPORTED_IMAGE_EXT` / `MIN_ZOOM` / `MAX_ZOOM` / `_detect_frames_image` / `_FrameDetectThread` / `_Canvas` / `FloatingToolBar` / `_AltNativeFilter` / `ImageViewer` | `ui/image_viewer.py` |
| `PreviewArea` / `_safe_export_name` / `_pixmap_to_pil` / `ExportDialog` | `ui/export_dialog.py` |
| `PreviewFloatingBar` / `PreviewCanvas` / `AnimAdjustPanel` / `ActionPreviewWidget` | `ui/preview.py` |
| `_rect_from_dict` / `_pixmap_bytes` / `_pixmap_from_bytes` / `read_project` / `write_project` | `spritesheet/project_file.py` |
| `UpdateDownloadDialog` / `_TransformHistoryCommand` / `MainWindow` | `ui/main_window.py` |

## 4. 建议拆分顺序（低风险 → 高风险，每步独立可验证）

> ✅ 已完成的步骤（2026-09-16，源码快照已备份到 `E:\projects\SpriteSheetTool\backup\`，
> 并已用 AST 逐块比对确认零行为差异）

| 步骤 | 产出模块 | 内容 | 风险 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | `models.py` | Action + MACARON_COLORS + 类型常量 | 极低（零依赖纯搬移） | ✅ 完成 |
| 2 | `transform.py` | 变换/布局纯函数 + DEFAULT_QUAD/PIVOT | 低（只依赖 models，纯函数可加单测） | ✅ 完成 |
| 3 | `widgets.py` | 图标/像素工具 + ColorPickerDialog/ActionCardWidget/ActionItemDelegate/_Ruler/SwitchWidget/Compact*/ActionThumbPlayer | 低（依赖 models/transform） | ✅ 完成（17 块） |
| 4 | `export_dialog.py` | ExportDialog + PreviewArea + `_safe_export_name`/`_pixmap_to_pil` | 中（依赖 widgets/transform/exporters） | ✅ 完成（4 块） |
| 5 | `image_viewer.py` | ImageViewer + FloatingToolBar + _FrameDetectThread + detect_frames | 中（依赖 widgets/transform） | ✅ 完成（9 块） |
| 6 | `preview.py` | PreviewFloatingBar/PreviewCanvas/AnimAdjustPanel/ActionPreviewWidget | 中高（依赖 transform/widgets） | ✅ 完成（4 块） |
| 7 | `main_window.py` 瘦身 | 只留 MainWindow + 更新弹窗 + _TransformHistoryCommand | 收尾 | ✅ 完成（6222 → 1192 行） |

### 拆分验证方式（本次实际执行，可复用）
- 备份：`E:\projects\SpriteSheetTool\backup\main_window_v6222lines_*.py.bak`（拆分前完整文件；
  备份目录已移出仓库，见该目录下 README.md 的说明）
- **纯搬移证明**：`tools/verify_move.py` 用 AST 逐块比对（忽略行号/列号），
  证明被搬移的 37 个顶层定义在新模块与拆分前**结构完全一致**；
  `tools/verify_class_members.py` 逐方法比对留下的类（MainWindow 46/47 一致，
  唯一差异是本次有意修正的错别字 docstring）。
- 每步回归：`compileall` → 导入检查 → `smoke_test.py`（17/17）
- 附加检查：`tools/check_spacing.py`（空行规范）、`tools/static_scan.py`（未使用导入）
- 额外清理：删除死代码 `_dedupe_frames`（43 行）/ `detect_frames`（10 行）/
  `_action_prefix`（3 行）与 4 个未使用 Qt 导入。


## 5. 注意要点（★ 核心）

### 5.1 循环 import（最大的坑）
- **Action 必须第 1 步抽走**（全文件最大公共依赖），否则任何新模块 import 它都会和 main_window 循环。
- transform/widgets/preview/export_dialog/image_viewer 之间的类型注解引用，一律：
  ```python
  from __future__ import annotations          # 文件已用，保持
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from spritesheet.ui.models import Action
  ```
- 运行时 import 只保留**无循环**方向：models ← transform ← widgets ← export_dialog/image_viewer ← preview ← main_window。

### 5.2 不要改行为，只搬移
- 拆分 = 原样剪切粘贴 + 补 import。**禁止**顺手重构逻辑、改名、改注释语义。
- 公共名字（`Action`、`MainWindow`、`transform_frame_pixmap`、`make_tool_icon` 等）保持原名，只改 `import` 来源。
- 如果给旧文件留转发别名（`from .widgets import make_tool_icon`），**必须**在最终步骤删除，避免两个定义并存。

### 5.3 常量/工具函数归属（避免散落）
- `DEFAULT_QUAD`、`DEFAULT_ROTATION_PIVOT` → transform.py
- `MACARON_COLORS` → models.py
- `_pixmap_bytes`/`_pixmap_from_bytes`/`_rect_from_dict` → **project_file.py**（工程序列化语义，最合适）
- `make_tool_icon`/`make_logo_pixmap`/`_tick_interval` → widgets.py
- `_safe_export_name`/`_pixmap_to_pil` → export_dialog.py

### 5.4 拆分期间不可动的逻辑细节（曾踩过坑）
- `_Canvas.paintEvent` 里 `_act_info[act] = (_fn, _cr, _fns)` 是 **3 元组**，所有解包处必须是 `({}, {}, {})` 默认。
- `toggle_reuse_at` / `reuse_frame_at` 依赖 `Action.frame_layouts` + `frame_layouts_base` 语义（基线帧不可取消）。
- `_AltNativeFilter`（原生 Alt 键监听）是顶层类，跟着 main_window 或单列文件都行，别和 ImageViewer 混在一起。
- `FloatingToolBar` 固定宽 300（`setFixedSize(300, 32)`）、帧号色块步进 `sq+2` 等是交互约定，搬移时原样保留。

### 5.5 每步回归清单（拆一个验一个）
1. 编译：`python -m compileall -q spritesheet`
2. 导入：`python -c "import spritesheet.ui.main_window"`（确认无循环 import / 无 NameError）
3. 冒烟脚本（offscreen）必须全部通过：
   - 启动 MainWindow（image_viewer / preview / action_list 非空）
   - 加载图 → 添加动作 → 选择元素 → 变换（rotation/quad）→ 复用（reuse_frame_at / toggle_reuse_at）
   - 导出：ExportDialog 构造 + `_export_to_directory` 生成 PNG/GIF/JSON/`project.sstool`
4. 若有测试：`python -m pytest`（当前已删 tests，重建单测时优先给 transform.py 写）

### 5.6 过程管理
- ✅ 已 `git init`（分支 `main`，仓库在桌面工作区）。后续改动**每步一个 commit**，
  方便回滚；重命名/搬移代码时尤其要走提交，否则无法用 `git diff` 复核。
- 每次只拆 1 个模块；一次拆多个出错难定位。
- 拆完更新 `README.md` 的「项目结构」章节。

## 6. 拆分后的收益
- 单文件从 5693 行降到 <1200 行，定位/审查/维护成本大幅下降。
- `transform.py`（纯函数）可独立单测，回归成本最低。
- 未来加功能只改对应模块，不再让 main_window.py 继续膨胀。

## 7. 素材
- 大图（非代码）：`mage_sprite_keyed.png`（3.6 MB）、`sprite_demo_keyed.png`（1.36 MB）—— 测试素材，与拆分无关。

## 8. 性能优化记录（2026-09-16，与拆分为同一轮）

拆分完成后对热路径做了三项优化，**每项都用"对照原算法"的等价性测试证明行为不变**
（`tools/verify_perf_equiv.py` / `tools/verify_checker.py`）。

| 热点 | 问题 | 优化 | 实测（优化前 → 后） |
| --- | --- | --- | --- |
| `transform._alpha_bounds` | 逐像素 Python 循环，变换每次都要调用 | alpha 通道一次切出，逐行用 C 层 `max`/`lstrip`/`rstrip` 求边界 | 1024² 244ms → **13.4ms（18.2×）** |
| `transform.frame_base_size` | 无缓存，交互路径（命中测试/手柄绘制/拖动）每次 mouseMove 都重算 | 按 `(帧号,旋转,镜像,旋转中心)` 缓存，随 `invalidate_transforms` 失效 | 一次拖动 20 次调用 1701ms → **0.01ms** |
| 三处棋盘格背景绘制（`_Canvas`/`PreviewCanvas`/`PreviewArea`） | 逐格 `fillRect`，cell=4 时每帧约 5 万次调用 | `widgets.paint_checkerboard`：缓存 2×2 图块 + 一次 `drawTiledPixmap` | cell=4 270ms → **0.82ms（449×）** |
| `image_viewer._detect_frames_image` | 纯 Python flood fill，双层全图循环 | alpha 预取 + 整行跳过 + 行内透明段 `lstrip` 跳过（等价优化） | 2048² 1692ms → **1527ms**；稀疏小图最高 **6.1×** |

### 8.1 关键正确性细节（改这里务必注意）
- `_alpha_bounds` 必须保持 **alpha > 0** 语义（不是 >=128）；已有 16 个用例覆盖
  （全透明/全不透明/alpha=1/单像素/随机尺寸/真实素材）。
- `frame_base_size` 的缓存键**不含 quad**（quad 不参与基准尺寸）；`Action.__init__`
  必须保留 `_base_size_cache` 字段，`invalidate_transforms` 必须同时清两个缓存。
- 元素检测的等价性依赖一个事实：**透明像素永远不会被入栈**（邻域判定含阈值），
  所以跳过它们不改变连通域；`divmod(pos, w)` 返回 `(商, 余数) = (行, 列)`，别写反。
- `paint_checkerboard` 要求 `rect.topLeft()` 就是图案原点（当前三处都是 `self.rect()`）。
  `ActionThumbPlayer` 的滚动棋盘格带 translate 相位，**故意未改**。

### 8.2 第二轮优化（先测再改，避免凭感觉）

上一轮之后重新采样，**原来的四个热点全部不在榜单上了**，新的绝对瓶颈只有一个：

| 位置 | 测前 | 测后 | 做法 |
| --- | --- | --- | --- |
| `_detect_frames_image` 2048² | 1413 ms | **196 ms（7.2×）** | 逐像素 flood fill → 行程切分 + 并查集（见 §9.4） |
| 同上 800×400 | 226 ms | **19.9 ms（11.4×）** | 同上 |

实测确认「不是」瓶颈的（省下无谓改动）：
- `_Canvas.paintEvent` 里为所有动作 × 所有帧重算帧号信息：**仅 0.06 ms**
  （该重绘总耗时 6.7 ms，主体是绘制本身）；
- 图集打包 2.5 ms、`PreviewCanvas.paintEvent` 0.58 ms、完整导出（含 GIF）100 ms —— 均无需优化。

### 8.3 仍未解决的项（留待后续）
- `_detect_frames_image` 剩余 196 ms 主要是**内存搬运**（QImage 缓冲 16 MB 拷贝 +
  步长切片取 alpha + 掩码 translate，合计约 28 MB 拷贝），已不是算法问题；
  要再降需要 numpy / C 扩展（引入新依赖，需产品决策）。
- `_Canvas.paintEvent` 6.7 ms 尚可接受，但元素/动作数量再翻几倍会线性上升；
  可加**视口裁剪**（跳过完全不可见的元素）——属渲染等价优化，必须做像素级验证。
- `PreviewCanvas` 在移动类型动作播放时每 8ms 触发一次背景重绘（现已很便宜）。

## 9. 缺陷修复记录

### 9.1 多动作共用元素时帧号色块互相遮盖（2026-09-16 修复）

**现象**：元素被两个动作同时选中，且第一个动作复用过该元素时，第一个动作的
「第 2 个帧号色块」会被第二个动作的色块盖住——看起来像一格内挤了两个序号，
而第二个动作的色块本该排到第 3 格。

**根因**（`image_viewer._Canvas.paintEvent`）：色块横向位置原先按**动作序号**
预留固定格位 `base_x = rect.left() + idx * (sq + 2)`，即假设"每个动作只占 1 格"；
但同一动作内 `for no in nos` 会为**每个帧号**各画一格，复用一次就多占一格，
于是第 2 个动作的起点正好落在第 1 个动作的第 2 格上。

**修复**：改为**连续游标** `cursor_x`，每画一格前进 `sq + 2`，下一个动作从上一个
动作画完后的游标位置继续（`sq`/`sy`/`setPen`/`setFont` 顺带提到循环外）。

**回归测试**：`tools/verify_frame_badges.py` —— 用「动作A 复用一次（帧号 1、2）
+ 动作B（帧号 1）」共用一个元素，离屏渲染后按 16×16 格取主色，断言三格依次为
A、A、B 且第 2、3 格之间有 2px 间隙。修复前该测试失败（第 2 格是 B 的颜色、
第 3 格空白），修复后通过。

**同类场景（已一并修复）**：两个动作对**同一组元素**做合成帧时，二者的合成帧粗框
位置相同（非当前动作仅偏移 2px），框左上角的帧号色块同样会互相遮盖 —— 见 9.2。

### 9.2 多动作合成同一组元素时帧号色块互相遮盖（2026-09-16 修复）

**现象**：两个（或更多）动作把**同一组元素**合成为一帧时，合成帧粗框落在同一位置，
框左上角的帧号色块彼此遮盖；顺延排布才是期望行为。

**根因**（`image_viewer._Canvas.paintEvent`）：合成帧色块一律画在各自粗框的左上角
`(group_rect.left(), group_rect.top())`，而多个动作选中同一组元素时 `group_rect`
完全相同（非当前动作只有 2px 的错位），于是后画的色块盖住先画的。

**修复**：绘制前先做**一遍前置遍历**，按「未加 2px 偏移的框几何」把合成框分组
（整数量化键，避免浮点误差拆散同框），组内按动作顺序分配档位 `slot`；
色块横坐标改为 `draw_rect.left() + slot * (sq + 2)`。单动作独占一框时 `slot == 0`，
位置与修复前**逐像素一致**。

**回归测试**：`tools/verify_composite_badges.py` —— 分别用 2 个和 3 个动作合成同一组
元素，离屏渲染后断言色块依次落在第 1/2/3 格且两两不重叠。修复前 2 动作场景即失败
（第 1 格被第 2 个动作的颜色盖住、第 2 格空白），修复后 2/3 动作两场景均通过。

### 9.3 导出 / 导入规则的往返缺陷（2026-09-16 修复）

用 `tools/verify_roundtrip.py` 对四种 JSON 格式做「导出 → 导入 → 逐字段比对」，
首次运行 96 项检查中 31 项不一致，定位出 5 个真实缺陷：

| # | 缺陷 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 本工具格式丢失 `padding` / 地面线 / 动作类型 / 移动参数 | JSON 里**写了**这些字段，但导入端从未读取 | `ImportedAction` 增加字段，`_parse_native` 解析，`_apply_imported_project` 应用 |
| 2 | 本工具格式丢失逐帧偏移 | `frames[].offset` 被忽略，帧被重新居中 | 新增 `_restore_imported_canvas` 反解 `frame_offsets` |
| 3 | 动作配色丢失（导入后按调色板轮换） | JSON 不含 `color` | 导出补 `color` 字段并回填 |
| 4 | **TP/Aseprite 帧尺寸被改坏 → 帧间相对位置错位（动画抖动）** | 导入未使用 `sourceSize`，且重建出的帧尺寸不一，`compute_preview_layout` 按各自尺寸居中 | 用 `sourceSize` 反推 padding + 用 `spriteSourceSize` 反解逐帧偏移 |
| 5 | TP/Aseprite 的 `trimmed` 恒为 false | 与非零 `spriteSourceSize` / 更大的 `sourceSize` 自相矛盾，只认 `trimmed` 的引擎会把帧贴到 (0,0) | 按「内容是否为源图子矩形」如实标注 |

**几何还原公式**（缺陷 2/4 共用，`MainWindow._restore_imported_canvas`）：

```
内容落点 = padding + 居中补偿 (max尺寸 - 本帧尺寸)//2 + frame_offset
⇒ frame_offset = 目标落点 - padding - (max尺寸 - 本帧尺寸)//2
```

其中「目标落点」本工具格式取 `frames[].offset`，TP/Aseprite 取帧原点加部件 `pos`；
`padding` 优先用文件写明的值，否则由 `sourceSize` 与帧尺寸反推。

**结果**：96 项检查 **缺陷 0 项**。本工具格式无损；TP/Aseprite 仅剩格式本身无法
表达的字段（fps/loop/锚点/padding/地面线/移动参数/配色，导入时提示）。

**注意**：`frame_offsets` / `padding` 是「帧在画布中落点」的唯一载体，改导入逻辑时
不要绕过 `_restore_imported_canvas`，否则动画会抖动（`verify_roundtrip.py` 的
「帧间相对位移」一项会立刻发现）。

### 9.4 关闭窗口 / 退出程序时随机闪退（2026-09-16 修复）

**现象**：同一进程内先关一个主窗口、再新建一个（或加载大图后很快关程序）时随机
崩溃，退出码 `0xC0000005`（原生 access violation）。修复前实测 **2/3 ~ 5/6 概率复现**，
是「偶发闪退、重启就好」这类最难查的问题。

**两个成因**（都在 `image_viewer.py`）：
1. `ImageViewer._build_ui` 把自己装成了 **app 级事件过滤器**
   （`app.installEventFilter(self)` + `installNativeEventFilter(原生 Alt 过滤器)`），
   却**从不卸载** —— 控件析构后过滤器仍指向已释放内存，下一次事件投递即崩溃；
2. **元素检测线程**（`_FrameDetectThread`）的 parent 是 ImageViewer，窗口先被析构
   而线程还在跑：继续访问 `self._image`，并向已释放的接收者 emit 信号。

**修复**：
- 新增 `ImageViewer.shutdown()`（幂等）：置取消标志 → `wait()` 等检测线程结束 →
  卸下 app 级 / 原生事件过滤器；
- 检测支持**协作式取消**：`_detect_frames_image(..., cancel=None)` 逐行检查取消回调，
  `_FrameDetectThread.cancel()` 触发；取消后不再 emit。默认 `None`，行为与之前完全一致；
- `MainWindow.closeEvent` 调用 `shutdown()`，并接 `app.aboutToQuit` 覆盖未走
  `close()` 的退出路径。

**验证**：往返测试（同一进程连开 8 个主窗口）连跑 6 次全部 `exit 0`；修复前 5/6 崩溃。

**给后续的提醒**：任何 `app.installEventFilter(x)` / `installNativeEventFilter(x)`
都必须有对应的卸载时机，否则就是一颗定时炸弹（`theme.py` 的 `DarkTitleBarFilter`
已在 `aboutToQuit` 卸载，可作参考写法）。

### 9.5 元素检测：逐像素 flood fill → 行程 + 并查集（2026-09-16）

**背景**：检测是上一轮之后剩下的唯一大热点（2048² 约 1.4s，占绝对主导），
原实现是逐像素 Python 4 连通 flood fill。

**新实现**（`_detect_frames_image`）：
1. alpha 通道一次切出 → `bytes.translate` 压成 0/1 掩码（全图一次 C 层调用）；
2. 逐行 `split(b"\x00")` 切出连续不透明**行程**，Python 只遍历行程；
3. 相邻行行程列区间有重叠（共享至少一列 = 上下相邻 = 4 连通）即合并，并查集维护；
4. 包围盒与**发现顺序**（自上行下行、同行自左向右）与原实现一致。

复杂度从 O(w·h) 次 Python 迭代降到 O(行程数)；2048² 实测 1413 → 196 ms。

**等价性证据**：`tools/verify_perf_equiv.py` 对照「原逐像素实现」逐项比对 19 个用例
全部一致，含对抗用例：对角接触**不得连通**（3 个）、单像素散点（4 个）、
棋盘格（13 个）、阈值边界（alpha 恰好等于阈值算达标）、楼梯形阶梯（1 个）、
随机细碎图（71 / 192 个连通域逐一吻合）。

**踩过的坑**：① 测试台原来用 `QImage(str(src))` 处理非 QPixmap 用例，把 QImage 当成了
路径，导致对抗用例**空转**（全返回 0）；② 用例里用 `.` 表示「恰好等于阈值」，
而那是**不透明**的，几何用例因此退化成一整块 —— 两处都是「测试没在测该测的东西」，
现已修正并写明期望数量。

### 9.6 工程文件改为「PNG + 容器」，资源管理器显示工程缩略图（v1.1.17）

**需求**：资源管理器里 `.sstool` 要能显示工程内容缩略图（工程里存的那张画面），
右下角带 `工程图标.png` 角标 —— 而不是一个看不出内容的通用图标。

**先验证再动手**（这一步很关键，否则容易白干）。用一个对照实验判定
`IShellItemImageFactory`（资源管理器取缩略图的实际接口）到底给了什么：

| 文件 | 取回尺寸 | 与源图平均通道差 | 结论 |
| --- | --- | --- | --- |
| `x.png`（已注册图片扩展名） | 70×96 | **6.15** | 真缩略图（基准） |
| `x.sstool`（**未**注册，内容是 PNG） | 96×96 | 118.83 | **图标回退** |
| 垃圾内容的 `.sstool` | 96×96 | 118.83 | 图标回退（数值完全相同） |

**第一次读数是错的**：只看「拿到了位图」会误判成功 —— 通用图标也会返回位图。
必须做内容比对才能区分。结论：光把文件做成 PNG 不够，**必须注册缩略图处理器**。
注册 `HKCU\Software\Classes\.sstool\ShellEx\{e357fccd-…}` =
`{C7657C4A-9F68-40fa-A4DF-96BC08EB3551}`（系统图片缩略图处理器）后复测：
`x.sstool` 取回 70×96、通道差 **6.15**，与 `.png` 基准完全一致 ✓

**实现**：

1. `project_file.py`：文件布局改为 `[PNG … IEND] + [MAGIC/header/manifest/sections]`。
   - 写入时把 `thumbnail.png` 提到最前，并从容器 sections 里移除（避免重复存储）；
   - 读取时兼容两种布局，读到前置 PNG 就补回 `sections["thumbnail.png"]`；
   - `png_image_end()` 按 PNG 块结构（长度+类型+数据+CRC）定位 IEND，**不搜索
     `IEND` 字面量**（它可能出现在块数据里）；容器损坏一律 `ValueError`，不崩溃。
2. `main_window._project_manifest`：缩略图长边归一 192px，**放大用最近邻**
   （精灵图多为像素画，平滑会糊）、缩小用平滑；角标按短边 1/4 取尺寸
   （原来固定 `max(24, …)`，小帧上角标会盖住主体）。
3. `installer.iss`：注册上述 ShellEx + `PerceivedType=image` + `Content Type=image/png`
   （仅 HKCU，不需管理员，随卸载清理）。Inno 里字面大括号写作 `{{ }}`。

**回归**：`tools/verify_project_format.py`（20 项：新布局 / 旧布局兼容 / 无缩略图退回 /
坏缩略图退回 / 空文件·截断·魔数损坏报 ValueError）；`tools/inspect_project_file.py`
实存一个工程检查布局。实测真实工程：PNG 开头、缩略图 157×192、其后是容器，
资源管理器取回 79×96 等比缩略图，内嵌图中角标特征色 (251,85,130) 只在右下角出现
（左上 0、右下 251 像素）。

**注意**：`PerceivedType` / `Content Type` 只是让「按类型分组」和预览窗格把 `.sstool`
当图片看待；真正决定缩略图的是那个 ShellEx CLSID。改注册项时别删错。


### 9.7 缩略图描边与「工程文件图标」比例（v1.1.21 → v1.1.22）

**需求**：① 缩略图描边太细，红色要用软件自己的红；② `SSTool.ico` 不要做成正方形，
按正常比例、且随尺寸等比缩放。

**① 描边**。原参数 黑 2 / 白 2 / 粉 `#E91E63` 3px（合计 7px）。3px 的粉在资源管理器
常用档位（48–96px）下不足 1px，会被白边和棋盘格吃掉 —— 这就是「描边太细」的成因。
改为 黑 4 / 白 4 / 粉 6px（合计 14px），红色换成 `#FB5582`：那是角标图
`工程图标.png` 的主色（占该图不透明像素 31.6%），和右下角标同色。
同一张缩略图上两处红不是一个红，视觉上很跳。界面强调色仍是 `theme.ACCENT #E91E63`，
要换回来只改 `_THUMB_STROKE` 一处。

**踩坑**：写「描边有几层几像素」的断言时，不能用「有几行偏黑」——
棋盘格的深格 `#1E1E1E` 也会被判成黑色，实测把 4px 数成 10px。
必须从 y=0 起**逐层量连续行程**（黑→白→粉依次各占多少行）。

**② 图标比例**。原做法是把竖幅原图（185×256）按长边适配进 256×256 方形画布，
于是每档实心内容只占画布宽 **72%**，四周一圈透明边。改为按原图比例分档：
`12x16 / 17x24 / 23x32 / 35x48 / 46x64 / 92x128 / 185x256`，每档都是原图等比缩放
（Pillow 存的时候直接用给好的帧，不再二次缩放，实测与原图缩放的平均通道差 0.000）。
`tools/make_icons.py` 现有 `--natural`（原比例分档）与 `--extract`（把原图从 .ico 里
取出来、裁掉透明边存成 PNG）两种模式；原图落库为 `SSTool_源图.png`。

**这条必须写清楚：它不改变显示大小**。用一个 A/B 实验确认（`spike_icon_ab.ps1`：
把 `HKCU\…\SpriteSheetTool.Project\DefaultIcon` 分别指向旧方形版与新原比例版，
各取一次资源管理器渲染结果）：

| 档位 | 旧（方形画布） | 新（原比例） |
| --- | --- | --- |
| 48px | 亮像素 1015，范围 x[4,43] y[0,47] | 亮像素 1015，范围 x[4,43] y[0,47] |
| 96px | 亮像素 3363，范围 x[13,82] y[0,95] | 亮像素 3363，范围 x[13,82] y[0,95] |
| 256px | 亮像素 22961，范围 x[35,219] y[0,255] | 亮像素 22961，范围 x[35,219] y[0,255] |

逐像素完全相同 —— 资源管理器按**高度**把图标适配进方形槽位，所以两种做法的渲染一致
（内容都占满高 100%、占宽 72%）。要让图标在槽位里「看起来更大」只能铺满槽位，
而 185×256 铺满 256×256 需要横向拉伸 38%，会变形，因此没有这么做。
这次改的是**图标文件的构成**（不再存正方形帧、不再有透明边），不是显示大小。

**回归**：`tools/verify_icon_ratio.py`（每档长宽比等于原图 ±1px、每档实心占满整帧、
每档等于原图按该尺寸重缩放、本体图标仍是方形）；`tools/inspect_icon.py` 打印尺寸表。

**顺带发现**：把 `.sstool` 的 `DefaultIcon` 临时改到别处做实验时，必须
`ie4uinit.exe -show` 刷新图标缓存并**恢复原值**（脚本里用 try/finally 语义处理）。


### 9.8 关闭窗口 / 退出程序时随机闪退（复现于回归脚本，v1.1.22 补）

9.4 修的是程序内的路径（`closeEvent` + `aboutToQuit`）。这一轮发现回归脚本里
同样会中招：`tools/verify_menu.py`、`tools/verify_settings.py` 从 `MainWindow()`
建窗口后直接退出，元素检测线程与窗口析构撞车，报 `0xC0000005`，
**单独跑通常碰不到，连跑整套必现**。两个脚本结尾都补 `win.close()` 后才稳定
（连跑 5 次全 0）。凡是「建 MainWindow 但不 close」的脚本都有同一个坑。


### 9.9 设置项放哪、怎么存（v1.1.22）

新增 `spritesheet/settings.py` 只管键名与读写（`QSettings`），不放 UI：
读的地方在保存工程（`_project_manifest`），写的地方在设置弹窗，两边必须同一份键名，
而且必须能在无界面环境里单独测。UI 在 `spritesheet/ui/settings_dialog.py`，
是叶子模块（只被 `main_window` 引用）。

开关设计成**即时生效**（勾选就写 QSettings），不设「确定 / 应用」——
只有一个开关时，多一步确认只会让人怀疑到底存没存。


