# 交接：抽帧窗口（SpriteSheetTool）—— 未解决项与下一步

> 用途：把当前会话的上下文交给下一个会话。请连同「用户口径」原文一起读，别只看结论。

## 0. 项目与环境

- 工程根：`E:\开发源文件\SpriteSheetTool\SpriteSheetTool`（PyQt6 桌面应用 SpriteSheetTool）。
- 解释器与命令（必须用这个 venv，`-X utf8 -u` 不可省）：
  - `& ".venv\Scripts\python.exe" -m compileall -q spritesheet`
  - `& ".venv\Scripts\python.exe" -m pyflakes spritesheet`
  - `$env:QT_QPA_PLATFORM="offscreen"` 后再跑脚本（无声环境必须）
  - `& ".venv\Scripts\python.exe" -X utf8 -u smoke_test.py`
  - 回归：`tools\verify_similar.py`（21/21）、`tools\verify_auto_align.py`（45/45）、`tools\verify_decimate.py`（35/35）
  - 探针：`tools\_check_extract.py`（抽帧窗口端到端，暂时是探针，待转正）
- 用户设置（别动）：`detect_min_area = 30`、`grid_zoom = 400`、guide `#00ffff`、guide_selected `#e91e63`、grid `#808080`、相似帧容差 0。
- **本批待发 v1.1.38**：版本号两处已改（`spritesheet/__init__.py` 的 `__version__` + `installer.iss` 的 `MyAppVersion`）；**还没打包**（用户交代：先不打包，先优化）。

## 1. 抽帧位移 —— **已解决**（两条真因都修了）

- 真因①：老代码把「第 k 帧 = `composite_groups[k]`」当映射，**只要元素不在任何合成组就错位** → 删错元素、偏移挂错帧（用户看到的「后面的元素会位移」）。已改成复刻 `crop_frames_for_action` 的真实规则（`frame_source_table` / `plan_decimate`）。
- 真因②：相似 ≠ 同尺寸 ⇒ 抽完 `max` 变小 ⇒ 落点整体平移。已由 `compensate_offsets` 逐帧补偿。
- 附带真因③：**换档位**时只还原派生源、没重建 `action.frames` ⇒ 老拿"抽过的结果"当基准 ⇒ 越抽越少。已补重建（1/4→1/2→强力→3/5 得 2→3→2→4，撤掉修复立刻红）。
- 现在的回归：`tools\verify_decimate.py` **42/42**、`tools\_diag_decimate_shift.py`（5 种形态落点逐帧一致 + 切换档位序列 ✅）。

### 【已作废】当初排除过的假设（留档，别再引用）

用户原话：「抽帧以后 后面的元素会位移 你好好检查一下 还有 抽帧按钮按下 就要按照这个按钮抽
改变相似度以后 也要记得执行啊 不要乱套」，后续追问：「抽帧 还是会位移 …… 你直接修好啊！再好好检查一下」。

### 已经排除的假设（别再走这条路）

- **不是"最大帧尺寸变小导致落点整体挪"**：落点是 `padding + (max−size)//2 + frame_offsets[i]`（`transform.compute_preview_layout`）。
  但每串至少保留 1 帧、且保留的是"最标准那帧"（与同串帧同尺寸），所以**抽帧永远不会让 `max` 变小** ⇒ `max_old == max_new` 恒成立。
  代码里目前那段"按 max 补偿 frame_offsets"是**空操作**（不会弄坏数据，但不是解药）。要么删掉，要么留着当保险并写清它是 no-op。

> ⚠️ **上面这句是错的，我后来自己推翻了**：相似 ≠ 同尺寸时，抽掉一串里较大的帧确实会让 `max` 变小、落点整体平移 ⇒ 这段补偿**不是 no-op，它就是解药的一半**（`tools\_diag_decimate_shift.py` 里 5 种形态实测：保留帧的内容/落点/偏移逐帧一致）。留在这里当"别相信当时那个结论"的标记。
- 不是删错了元素：`plan_decimate()` 只在"某元素所有引用帧都被抽掉"时才删它，`verify_decimate.py` 有用例守着。

### 当前最可疑的一条链（下一步就查这个）

`image_viewer.crop_frames_for_action()`（约 3410 行起）把帧拆成三条路：

1. `action.frame_layouts`（JSON 导入 / 复用帧）：逐帧按记录的**部件与帧内位置**生成，注释原话
   「合成帧内各元素的相对位置使用元素的**逻辑坐标**计算」；
2. `action.composite_groups`（一轮一键合成的帧）；
3. 都没有：一帧一元素，帧序 = `selected_indices` 顺序。

这条路上还有 `_drop_from_layouts()`（约 3439 行，显式取消元素选择时"只把元素从各帧里摘掉、帧保留"）
和 `_merge_added_into_layouts()`。**抽帧目前只重排了 `selected_indices` / `composite_groups` /
`frame_layouts` / `frame_layouts_base` 和一堆"每帧字典"（`frame_offsets`、`frame_durations`、
`frame_rotations`、`frame_mirror_h/v`、`frame_rotation_pivots`、`frame_quads`、`frame_transform_modes`），
很可能漏了帧内相对位置那一层（元素逻辑坐标 / `frame_parts` 的帧内位移）。**

### 下一步精确步骤（按顺序，不许跳步）

1. 读 `_drop_from_layouts()` / `_merge_added_into_layouts()` / `crop_frames_for_action()` 后半段
   （约 3458-3560 行），确认"帧内元素相对位置"是从哪里来的、抽帧后会不会变。
2. 写**能量化区分**的用例（这次别再犯"空验证"的错）：
   - 造一个动作，其中一帧由**两个元素**合成（元素间距已知，比如 A 在 (0,0)、B 在 (20,0)）；
   - 抽掉**另一串**的帧；断言"这一帧里 A 与 B 的间距仍是 20、A 的帧内位置仍是 (0,0)"。
   - 内容度量沿用老办法：alpha 阈值 + 行列求和 / 包围盒，不靠肉眼。
3. 若确认是帧内相对位置被改：修 `plan_decimate()` / 落地方，再补断言进 `verify_decimate.py`：
   **抽帧前后，每个保留帧的落点与帧内相对位置逐帧不变**。
4. 顺带钉住"已选中档位再改相似度是否全部重算"（见下）。

## 2. 抽帧功能现在的行为（用户口径为准）

- 一串 = **同一个相似组 + 帧序上相邻**；中间夹了别的帧或没配对的帧就断开；长度 < 2 不算串。
  判的是**组号**不是颜色值（颜色由组号映射，撞色也不并串）。
- 档位（行内左→右，按抽掉多少由轻到重）：`[相似度框] … [抽帧3/5] [抽帧1/2] [抽帧1/4] [抽帧强力去重]`（按钮右对齐）。
  一串 N 帧保留 `ceil(N*num/den)`，**每串最少留 1 帧**；不相邻/没配对的帧原样保留；
  每串保留的帧里**必定包含"最标准那帧"**（与同串其他帧平均最像的一帧，并列取靠前）。
- 按钮互斥 + 选中高亮（`#E91E63` 底白字）；点下去**立刻**抽（实时显示，≥80 帧先弹转圈提示）。
- **基准永远是"该动作的所有帧"**（打开窗口时记下的原始帧表）：换档位、改完相似度重抽，
  都先还原再算 —— 用户原话「每次切换按钮 都是从这个动作所有帧选择而不是当前的帧数」。
- 改相似度 → 300ms 攒批 → 用新容差对**所有帧**重跑 `group_elements()` → 按当前选中档位再抽一次。
  薄弱点（需断言钉住）：攒批期间关窗口则不重抽（有意）；重抽前判 `isVisible()`。
- 撤销：第一次抽帧推一份 `_push_snapshot("抽帧", current_only=True)`；取消/Esc = 整轮撤回；
  确定 = 收工（下次打开重记原始状态）。取消时必须**停掉重算定时器并清 `_pending_reapply`**
  （用户实测过"取消之后又闪'正在抽帧'并改了原图选择"，就是这个没清）。
- 相似度框、循环档、帧率只活在这个窗口里：**不写设置、不碰动作、不动右侧帧条**。
- 窗口：默认 390×600、可缩放、左下角缩放抓柄、模态（开了先处理它）、预览只画当前帧 + 棋盘格
  （共用 `widgets.paint_checkerboard`）、悬浮工具条在棋盘格左上（循环三档 `LOOP_ICONS`：loop/once/pingpong，
  帧率 1~100）、缩略图 64×64（共用 `preview.build_frame_thumb`，帧号右下 + 相似彩块右上）、
  标题「预览 · 动作名（N 帧 · M 块）」在缩略图**下面**，M = 强力去重后的帧数（`split_runs` 直接数）。
- 空格 = 按住拖动：**应用级** `space_pan.SpacePanFilter`（只喂 `_panning` 状态，**绝不吞键** ——
  原图页的空格拖动/临时让位靠这个键，吞掉就是回归）。鼠标平时箭头，按空格才变手。

## 3. 待还的账

1. 位移（上面 P0）。
2. `tools\_check_extract.py` 收尾会崩（0xC0000005，断言全过、崩在退出阶段）——
   同类老坑：带定时器/模态对象被回收。修完把探针那 6 条端到端断言转进 `verify_decimate.py` 或新建
   `verify_extract.py`，让探针退休。
3. `NEXT.md` 补「相似帧 / 抽帧」两批的章节；`README.md` 的功能特性与回归表要加上抽帧窗口与
   `verify_decimate.py`。
4. 新度量（64×64 特征）的**变异验证**还没做（项目惯例：改产品代码 → 确认预期断言变红 → 按字节还原）。
5. 用户上一条消息里有个空的「2.」没发过来，等它。

## 4. 本项目的老坑（务必照做）

- 中文串里混 ASCII 双引号 = SyntaxError（用「」）；绝不用 PowerShell `Set-Content` 改中文文件。
- `main_window.py` / `preview.py` 是 **CRLF**：做文本替换前先按文件实际换行归一化。
- **Qt 槽里抛异常会直接打死进程**（0xC0000409，看不到 traceback）：槽里调用的东西一律包 try 打日志。
  同理 **`paintEvent` 里抛异常直接崩** —— 曾经因为把落点元组当二元组拆（`for i,(px,py) in enumerate(positions)`）
  导致"抽帧窗口不显示 + QBackingStore::endPaint 刷屏"，改成 `spot[0], spot[1]` 下标取。
- 别对带着已武装定时器的窗口 `deleteLater()`；长生命周期的模态/QProgressDialog 要留引用。
- 小步验证：每步都跑 compileall + pyflakes + 冒烟 + 三本回归；用例红了先怀疑自己的用例
  （这轮 8 条新断言里 3 次红都是用例写错，算法侧没改过一行）。
- 用户偏好：口语中文、要数字不要形容词、不要过度设计、大任务先说步骤、不留死按钮（禁用+写明原因）。