# SpriteSheetTool

精灵图（Sprite Sheet）打包工具：自动检测原图元素、按动作逐帧编排动画，支持
自由变换（旋转/缩放/斜切/透视/扭曲）、帧复用，一键导出图集 PNG / GIF / 多种 JSON。

- 技术栈：Python 3.10+（开发/打包用 **3.12.10**）· PyQt6 6.11 · Pillow 12.3（GIF 导出）
- 主题：深色界面 + 单一玫红强调色
- 当前版本：**v1.1.34**　|　许可证：**GNU GPL v3**（开源免费，可自由使用/修改/再分发）

**目录**：[功能特性](#功能特性) · [环境要求](#环境要求) · [新电脑部署](#新电脑部署快速开始) ·
[日常使用](#日常使用) · [打包发布](#打包发布生成安装包) · [自动更新](#自动更新) ·
[项目结构](#项目结构) · [开发与验证](#开发与验证) · [版本历史](#版本历史近期) ·
[迁移清单](#换电脑--迁移清单) · [许可证](#许可证)

> 想直接动手：装好 Python → 双击 `启动.bat` → 拖入图片 → `+ 添加动作` → 选元素 → 导出。
> 出问题或要改代码，先看 [开发与验证](#开发与验证) 里的三步自检。

---

## 功能特性

- **自动元素检测**：载入图片后自动分析 alpha 通道，用 4 连通 flood fill 识别每个独立元素
- **动作编排**：每个动作独立保存选中元素、帧序、颜色与全部动画参数，切换动作不丢失；
  一个元素可同时属于多个动作
- **元素选中**：点击 / 框选（Ctrl 追加），帧序即选中顺序，右侧预览实时同步
- **合成帧**：Ctrl+点击（或按住 Ctrl 框选）把多个元素合成为一张帧
- **帧复用**：同一元素可出现在多帧 —— 点击「Alt+复用」按钮（或按住 Alt 点击已选元素）
  追加一帧；再按住 Alt 点击已复用元素取消最近一次复用；元素左上角按帧号依次显示色块
- **自由变换**（每帧独立，所见即所得）：旋转 / 缩放 / 斜切 / 透视 / 扭曲，
  预览画布直接拖拽手柄编辑，点击空白或 Esc 退出；「清空变换」一键还原该帧的
  旋转/镜像/透视/旋转中心（8 个变换按钮等宽排布）
- **动画预览**：居中叠放播放、fps 调节（1–60）、往返/单次/循环模式、洋葱皮参照帧、
  地面线、逐帧微调偏移、锚点准星
- **导出**：原图坐标图集或重构打包图集（去重复用元素图块）、GIF、多格式 JSON、
  .sstool 工程文件；导出带实时进度条

## 环境要求

- Windows 10/11（64 位）
- Python 3.10+ —— 开发与打包环境是 **3.12.10**（`启动.bat` 会拒绝低于 3.10 的解释器）；
  只在**未打包**运行时才需要本机 Python，装好的用户直接跑 exe 即可

## 新电脑部署（快速开始）

> 本工具设计为"拷贝即用"：只需要源码 + Python，不需要手工装任何东西。

1. **安装 Python**：从 <https://www.python.org/downloads/> 下载 **3.12**（3.10 及以上都可以），
   安装时务必勾选 **Add python.exe to PATH**。
2. **拷贝项目文件夹**到新电脑任意位置（无需 .venv、build、dist 等目录，见下方迁移清单）。
3. **双击 `启动.bat`**：脚本会自动
   - 在项目下创建 `.venv` 并安装 `requirements.txt` 依赖（PyQt6 + Pillow），
   - 若 `.venv` 缺失或损坏则自动改用系统 Python 并补装依赖，
   - 然后启动程序。

也可以手动安装并运行：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

或直接 `pip install -r requirements.txt && python main.py`。

## 日常使用

1. **添加图片**：主界面点「+ 添加图片」或把图片拖入原图区，元素自动检测并画出辅助框。
2. **添加动作**：点「+ 添加动作」创建动作卡牌（双击卡牌可重命名，点色圆可换颜色）。
3. **选择元素**：在原图区点击/框选元素，成为当前动作的帧；Ctrl 点击合并为合成帧。
4. **变换 / 复用**：在右侧预览画布拖动手柄做自由变换；点「Alt+复用」或按住 Alt 点击元素复用帧。
5. **预览调节**：fps、循环模式、地面线、锚点、逐帧偏移等在预览区下方调节面板调整。
   拖动「当前帧微调」的偏移滑块/按住方向按钮时，**帧会跟着真的移动**（画布区域先冻结），
   松手后再按新偏移重算画布大小 —— 不然帧会看起来钉在原地、只有灰框在动。
6. **导出**：点「导出」→ 选择动作与格式（图集 PNG、GIF、JSON、.sstool 工程）→ 选文件夹导出。

> 还没添加动作时点原图画布或变换/合成等控件，画布底部会弹出提示「请先添加动作」——
> 这些操作的数据（选中元素、逐帧偏移、透视四边形）都挂在动作上，没有动作就没有落点。

### 工程标签页（多工程）

窗口里从上到下是：菜单栏 → 软件名 → **工程标签栏**（26px 矮条）→ 内容区 → 状态栏。
标签栏像 PS 的文档标签：

| 操作 | 结果 |
| --- | --- |
| 单击标签 | 切换工程（各自的动作/帧/原图/预览/撤销栈都独立） |
| 双击标签 | 就地重命名工程（回车提交 / Esc 取消） |
| 双击标签栏空白处 | 新建一个空白工程标签页 |
| 标签上的 × / 中键点击 | 关闭该标签（有改动会先问保存） |
| 末尾的 + | 新建标签页 |
| 文件 → 新建工程（Ctrl+N） | 新建标签页 |
| 文件 → 打开工程… | 当前标签是"没动过的空白页"就复用它，否则**新开一个标签**（不会顶掉正在编辑的工程） |

标签栏尺寸细节：高 26px，文件名整体下移 2px（`TEXT_DROP`），左右留白 14px，
名字与关闭叉号之间留 26px（`NAME_GAP`，原来只有 14，叉号贴着名字太挤），最小宽度 96px。

> ⚠️ **切换工程这件事只有主窗口能做**：标签栏只发 `tab_selected` 信号，**不自己改
> `current_index`**。这里踩过一次坑：标签栏先把 current 改掉再发信号，主窗口的
> `if index == current: return` 就必然命中 —— 表现为"点标签只换高亮、工程根本不切"。
> 回归也必须走真实点击（`verify_tabs.py` 早先直接调 `_switch_tab()`，绕开入口，所以没抓到）。

标签名默认取工程文件名；**双击改名后不会再被文件名覆盖**（`_name_custom`），
改名只影响软件里的显示名，**不动磁盘上的文件名**。
标签上**不显示**未保存标记（曾经有个小圆点，已按要求删掉）；未保存状态看**窗口标题末尾的 `*`**，
关闭标签 / 退出时也会先问要不要保存。

> **实现要点**：切标签不是"存一次工程再读回来"，而是把文档状态**按引用**换过去
> （`_capture_document` / `_restore_document`）。实测 `_project_manifest()` 对一张
> 3.8MB 素材要 **2.1 秒**（原图要重新编码成 PNG），按"存读"方式切标签会卡两秒没法用；
> 换引用再刷新几个控件是毫秒级。
> 撤销栈是**每个文档一份**，切标签时会把 编辑 → 撤销 重新接到当前文档那份上。
> 退出时若有多个未保存的工程，会一次性列出来问"全部保存 / 不保存退出 / 取消"。

### 单实例（同时只跑一个窗口）

整个系统只允许一个 SpriteSheetTool 进程。**第二次启动（含双击 `.sstool` 文件关联打开）
不会开新窗口**，而是把文件转给已经在跑的实例，由它**开一个新标签页**，自己随即退出。

- 机制：`QLocalServer`/`QLocalSocket`（Windows 上是命名管道，不需要管理员权限），
  服务名固定为 `SpriteSheetTool.single-instance` —— 不带版本号，所以装了新版本后
  从旧版本入口打开也能落到同一个窗口。
- 文件转过去之后会把窗口提到前面。
- 自己核对过：第二个进程 **0.2 秒**退出、退出码 0，主实例收到完整路径
  （`tools/verify_single_instance.py`，含真的再起一个进程的端到端用例）。
- 两个坑记在 `spritesheet/single_instance.py` 注释里：客户端写完立刻断开会把数据丢掉
  （要等 ACK 才能走）、`listen()` 之前必须无条件 `removeServer()`（否则被强杀后会留下
  连不上的僵尸服务名）。

### 顶部名称栏

只有软件名，没有图标（原来左侧那个 54×54 白边图标已去掉）。
软件名首字母 `S` 用 30px、其余 22px，两段**垂直中心对齐**（`AppNameLabel`）；
版本号跟在名字后面，与名字**共用同一条基线**（也就是底对齐）。
不用 QLabel 富文本的原因：Qt 富文本按基线对齐，放大首字母会整块往上冒，
看起来是"吊在上面"而不是上下居中 —— 所以这里按墨迹盒自己算基线。
名称控件高 54、名称栏高 64，下边缘留 5px，不会越界去盖住下面的标签栏。

对齐基准是**大写带**（大写顶 → 基线），不是整块墨迹盒：正文 "SpriteSheet" 里的 `p`
有降部，把它算进去首字母会被"居中"到偏低的位置（真实字体下实测偏低 3px）。
大写高度用 `tightBoundingRect("H")` 量，不用 `capHeight()`（offscreen 下后者退化到
和字号一样大）。真实字体实测：首字母墨迹 24px、正文到大写高度 18px，两者中心差 **1.0px**；
版本号下缘与名字基线差 **0.6px**。

> ⚠️ **offscreen 平台没有真实字体**：`QT_QPA_PLATFORM=offscreen` 下任何字符
> （连 ASCII `x`）都被画成同一个方框。所以回归里"文字画出来了 / 墨迹多少像素"这类断言
> 只能证明**位置与尺寸**，证明不了字形本身 —— 字形由 `tools/verify_glyphs.py`
> 在真实 windows 平台下验证（它只往 QPixmap 上画，不显示窗口，不会闪屏）。

### 导出格式

产物**以当前标签页的工程名开头**（下表以工程名 `主角工程` 为例）：

| 格式 | 文件 | 说明 |
| --- | --- | --- |
| 图集 PNG | `主角工程.png` | 原图坐标图集或重构打包图集（勾选"分别导出"时每个动作一个文件夹） |
| GIF | `主角工程_<动作名>.gif` | 预览动画，保留 fps / 循环模式 / 帧顺序 |
| 本工具 JSON | `主角工程.json` | 完整保留锚点/地面线/移动参数/烘焙偏移等编辑语义 |
| TexturePacker JSON | `主角工程.tp_hash.json` / `主角工程.tp_array.json` | 适配 Phaser 2 / PixiJS / Cocos2d-x |
| Aseprite JSON | `主角工程.aseprite.json` | 带帧标签，适配 Godot / Phaser / Unity 导入器 |
| 工程文件 | `主角工程.sstool` | 完整工程（双击可打开恢复）；文件本身是一张 **PNG**，见下节 |

**落点规则**：本次导出会写出的**文件数 > 2** 时，全部放进
`<所选目录>/<工程名>/`；不超过 2 个就直接放在所选目录下。默认（图集 PNG + JSON + 工程
= 3 个）走文件夹，这样反复导出不会互相覆盖、也不会和别的工程混在一起。
"分别导出"时每个动作一个文件夹，名字同样以工程名开头（`主角工程_跑步/`）。

命名与落点规则只有一份实现（`base_name()` / `_file_count()` / `target_root()`），
同名检测与真正写盘都调它们 —— 以前两边各自拼字符串，是最容易改漏的地方。

导出时若目标目录里已有同名文件，会先询问：**覆盖** / **加序号另存**（如 `_2`）/ **取消**。
选"加序号"时新文件带序号、原有文件不受影响（由 `tools/verify_export_names.py` 回归）。

文件对话框（打开/保存/添加图片/导入 JSON/导出文件夹）默认从**上次用过的目录**开始；
没记录过、或那个目录已经被删/改名，就回退到**桌面**。打开工程时优先当前工程所在目录。

导出选项的默认值与顺序（顺序即优先级，最完整的产物放最上面）：

| 顺序 | 选项 | 默认 |
| --- | --- | --- |
| 1 | 导出 SSTOOL 工程 | **勾选** |
| 2 | 导出 JSON | **勾选** |
| 3 | 导出 GIF（预览） | 不勾选 |
| 4 | 分别导出 | 不勾选 |

### 软件设置

**编辑 → 软件设置…**（`spritesheet/ui/settings_dialog.py`）：

- **工程文件显示缩略图**（默认勾选）：勾选时保存的 `.sstool` 内嵌缩略图，资源管理器
  直接显示画面；不勾选则整个缩略图区段都不写，资源管理器回退到文件类型图标
  `SSTool.ico`。开关**即时生效**（立刻写 QSettings，对下次保存的工程起作用），
  不设"确定 / 应用"——只有一个开关时多一步确认只会让人怀疑到底存没存。
- **检查更新**：原来挂在 文件 菜单，现已移到这里。更新属于软件级操作，
  和打开/保存工程不是一类事。

### 工程文件的缩略图

`.sstool` 的文件布局是「**一张合法 PNG + 容器数据**」：

```
[ 缩略图 PNG（当前画面 + 黑白粉描边 + 灰棋盘底 + 右下角 工程图标.png 角标）… IEND ] [ 工程数据 ]
```

系统图片解码器读到 `IEND` 即停、忽略尾部数据，而本程序从 PNG 结束处开始解析工程数据。
配合安装包注册的缩略图处理器，**资源管理器会直接显示工程内容缩略图**（右下角带角标），
而不是一个看不出内容的通用图标。要点：

- 缩略图是**固定的 256×256 正方形**：资源管理器用方形槽位显示缩略图，若按画面比例
  输出，宽条素材（如 8:1 的横排精灵）会被显示成细细一条。
- **灰色棋盘格铺底**：内容装不满或本身透明时，由棋盘格兜底，缩略图永远是不透明的整块方形
  （棋盘配色与软件预览区一致）。
- **黑白粉三层描边**（由外到内：黑 4px → 白 4px → 粉 `#FB5582` 6px）把缩略图罩起来。
  粉用的是**角标图的品牌粉**（`工程图标.png` 主色），和右下角标同色；界面强调色
  `theme.ACCENT #E91E63` 是另一个红，要换回来改 `_THUMB_STROKE` 一处即可。
  早先粉只有 3px，在资源管理器常用的小尺寸下不足 1px，会被白边和棋盘格吃掉。
- 内容按比例居中装进描边内缘，放大用最近邻（精灵图多为像素画，平滑会糊）、缩小才用平滑。
- 右下角叠加 `工程图标.png` 角标，占短边 **1/3**（原为 1/4）。
- 旧版工程（容器在最前）仍可正常打开，只是那种文件没有缩略图。
- 缩略图处理器注册在 **当前用户** 范围（`HKCU\Software\Classes\.sstool\ShellEx\…`），
  不需要管理员权限，随卸载清理。
- 若旧文件仍显示旧图标，是资源管理器的缩略图缓存在起作用：**重装后新存的工程**会立即
  有缩略图；老文件可改个名或清理缩略图缓存（`ie4uinit.exe -show`）后刷新。
- 内容损坏/不是 PNG 的 `.sstool` 会**优雅回退**到 `SSTool.ico`，不会出现破图。
- 不想让工程文件带缩略图时，用 **编辑 → 软件设置…** 关掉开关即可（见上节）。

#### 工程文件图标（SSTool.ico）

`SSTool.ico` 是**方形多尺寸**（16/24/32/48/64/128/256），源图（185×256 竖幅）按长边适配
居中 —— 方形画布两侧是透明的。

**必须这样，不能改成"原比例帧"（非方形尺寸）。** 曾经试过一次，结果是图标被压扁：
资源管理器图标视图走的是**系统图标列表**，列表项是方形位图，**非方形帧会被拉伸填满
方形槽位**（185×256 拉成方形 = 横向拉宽 1.38 倍）。实测（`tools/verify_icon_imagelist.py`，
走真实 shell 路径 `SHGetFileInfo`）：

| 图标文件 | 16px 档粉色框 | 32px 档粉色框 |
| --- | --- | --- |
| **方形帧（现行）** | 12×16（0.750） | 22×32（0.688） |
| 非方形帧（错误做法） | 16×16（**1.000**） | 32×32（**1.000**） |

方形画布不代表"图更小"：**图按高度铺满整档**（256 档里就是 256 高），宽度按原比例
（72%），这是不拉伸前提下的最大尺寸。

> ⚠️ 验证图标**必须走 `SHGetFileInfo`**。`IShellItemImageFactory`（取缩略图用的接口）
> **会**保持长宽比，拿它验证只会得出"没变化"的错误结论 —— 上一轮就是这么漏掉的。

不想让"图只占 72% 宽"的观感，唯一不拉伸的办法是**给方形画布铺品牌色圆角底**
（`--tile "#FB5582"`），让图标整体铺满槽位。

重做图标的流程（原图已落库为 `SSTool_源图.png`）：

```powershell
# 生成 SSTool.ico（方形帧）
.venv\Scripts\python.exe tools\make_icons.py SSTool_源图.png SSTool.ico
# 把原图从现有 .ico 里取出来（裁掉透明边），换素材时用
.venv\Scripts\python.exe tools\make_icons.py SSTool.ico SSTool_源图.png --extract
.venv\Scripts\python.exe tools\inspect_icon.py SSTool.ico          # 校验尺寸表
.venv\Scripts\python.exe tools\verify_icon_ratio.py                # 文件层：方形帧 + 比例 + 铺满
.venv\Scripts\python.exe tools\verify_icon_imagelist.py            # shell 层：真实显示不变形
```

### 导入 / 往返保真度

拖入 JSON（连同配套 PNG）即可还原为工程，格式自动识别。往返保真度已用
`tools/verify_roundtrip.py` 逐字段验证（含逐帧像素与帧间相对位移）：

| 格式 | 往返结果 |
| --- | --- |
| 本工具 JSON | **无损**：动作名/配色/帧数与顺序/fps/loop/锚点/padding/地面线/动作类型与移动参数/逐帧偏移/帧内容像素/帧间相对位移 全部一致 |
| TexturePacker | 帧内容与帧间相对位移一致；fps、loop、锚点、padding、地面线、动作类型与移动参数、配色**格式本身不含**，取默认值（导入时会提示） |
| Aseprite | 帧内容、帧间相对位移、fps、loop（pingpong）一致；锚点、padding、地面线、移动参数、配色格式不含；`once` 无法表达（Aseprite 只有 forward/reverse/pingpong），会退化为 `loop` |

## 打包发布（生成安装包）

> 需要：Python 环境 + [Inno Setup 6](https://jrsoftware.org/isinfo.php)（含 ISCC.exe）。

```powershell
# 1. 升版本号（两处必须一致）：
#    spritesheet/__init__.py  的 __version__
#    installer.iss            的 MyAppVersion
# 2. 打包：
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```

脚本自动完成：PyInstaller 构建 `dist\SpriteSheetTool\`（onedir）→ Inno Setup 编译
`installer\SpriteSheetTool_v.X.Y.Z.exe`。安装包可分发、可自动更新。

## 自动更新

客户端启动时请求**源站**目录列表
`https://dudua.synology.me:8443/download/SpriteSheetTool/.index.php`（**必须带端口号**；
直接访问目录会 403，只能走 `.index.php`），自动挑选版本号最高的
`SpriteSheetTool_v.X.Y.Z.exe` 提示更新。每次发版只需上传新安装包到该目录。

服务器端零配置，详见 `update_server/README.txt`。两点注意：

- **证书**：源站需持有 `dudua.synology.me` 的有效证书（现已部署 Let's Encrypt）。
  客户端**严格校验证书、不做回落** —— 证书一旦失效，"检查更新"会明确报错，而不是
  静默降级（自动更新链路上绝不能接受来路不明的安装包）。
- 为什么不再走 `duadu.cc`：那是 CDN，会消耗流量；源站直连更省。索引里的安装包链接是
  **相对路径**，所以下载也只打源站，不会绕回 CDN。
- 入口在 **编辑 → 软件设置… → 检查更新**（启动 2.5 秒后仍会自动静默检查一次）。
- 发版后可用 `tools/verify_updater.py` 一次性核对：地址可达、证书严格校验通过、
  目录里最高版本是否高于本地版本。

## 项目结构

```
SpriteSheetTool/
├── main.py                     # 启动入口（python main.py）
├── 启动.bat                    # 双击启动：自动建 .venv / 装依赖（推荐）
├── run.bat                     # 调 启动.bat
├── requirements.txt            # 运行依赖：PyQt6、Pillow
├── requirements-dev.txt        # 开发/打包依赖（含 PyInstaller）
├── build_windows.ps1           # Windows 一键打包脚本
├── installer.iss               # Inno Setup 安装包配置
├── ChineseSimplified.isl       # 安装向导中文语言文件
├── SpriteSheetTool.spec        # PyInstaller 打包配置
├── SpriteSheetTool.ico         # 应用图标（main.py 使用）
├── SSTool.ico / 工程图标.png   # .sstool 文件关联图标 / 缩略图角标素材
├── SSTool_源图.png             # .sstool 图标原图（185×256，生成 ico 的输入）
├── icons/                      # 工具栏 SVG 图标
├── update_server/              # 自动更新服务器部署说明
├── installer/                  # 产出的安装包（SpriteSheetTool_v.X.Y.Z.exe，不进版本库）
├── smoke_test.py               # offscreen 冒烟回归（18 项，跨全部主流程）
├── bench.py                    # 性能基准（关键热路径 A/B）
├── tools/                      # 开发辅助脚本（回归 / 静态扫描 / 搬移与等价性取证）
└── spritesheet/
    ├── __init__.py             # 版本号（__version__）
    ├── importer.py             # JSON 工程导入（TP/Aseprite/本工具格式）
    ├── project_file.py         # .sstool 工程读写 + QPixmap/JSON 片段辅助
    ├── settings.py             # 软件设置键值（QSettings，工程缩略图开关）
    ├── single_instance.py      # 单实例守卫（QLocalServer/Socket 转发打开请求）
    ├── updater.py              # 自动更新（版本检查/下载）
    └── ui/
        ├── main_window.py      # 主窗口装配 + 更新下载弹窗 + 撤销命令
        ├── widgets.py          # 通用控件：动作卡牌/标尺/紧凑输入/缩略播放器/棋盘格
        ├── image_viewer.py     # 原图区：元素检测 + 标尺画布 + 浮动工具条 + 帧复用
        ├── preview.py          # 预览区：预览画布 + 自由变换手柄 + 动画调节组
        ├── export_dialog.py    # 导出弹窗：动作选择 + 预览 + 图集打包/GIF/JSON
        ├── settings_dialog.py  # 软件设置弹窗：缩略图开关 + 检查更新
        ├── models.py           # 数据模型：Action 动作 + 主题色常量
        ├── transform.py        # 帧变换/预览布局纯函数
        ├── exporters.py        # 第三方 JSON 导出器
        └── theme.py            # 深色玫红主题 QSS
```

## 开发与验证

### 版本控制

- 仓库就在**本工作区**（`git init` 已完成，分支 `main`）。
- **提交约定：每完成一件事就提交一次**（一个主题一个提交，消息写清"做了什么 + 为什么"），
  不要攒着一起提交 —— 出问题时才能精确回滚。
- 历史结构（重构那段是按主题重建的，每个提交都是可编译、可验证的状态）：

  | 提交 | 内容 |
  | --- | --- |
  | `chore:` | 导入重构前的项目状态（v1.1.16，便于对照） |
  | `refactor(ui):` | 清理死代码与未使用导入 |
  | `refactor(ui):` | 按 SPLIT_GUIDE 步骤 3-7 拆分 main_window.py |
  | `perf:` | 变换 / 绘制 / 元素检测热路径优化 |
  | `fix(ui):` | 帧号色块互相遮盖 |
  | `fix:` | 导出/导入往返缺陷 |
  | `feat(ui):` | 菜单调整 |
  | `test:` | 冒烟回归、性能基准与校验脚本 |

- 重建前的旧历史保留在 `pre-rebuild-backup` 分支，可随时对照或回退。
- ⚠️ **重建的诚实说明**：重构那段历史是在**没有 git** 的情况下完成的，我没留中间快照，
  因此历史是"按主题重建"的 —— **代码**用当时记录的精确改动文本 + 备份文件还原，且
  每个提交都通过编译、最后一个提交的树与重建前**逐字节一致**；但第一个提交里的
  `README.md` / `SPLIT_GUIDE.md` / `.gitignore` 用的是**整理后的版本**（这三个文件
  没有留下原始快照），所以看早期提交时会发现"代码是当时的、文档是最新的"。
  想看真实起点请看 `backup\main_window_v6222lines_*.py.bak`。
- **备份不在仓库里**：源码快照（`.bak`）与仓库历史打包（`.bundle`）统一放在
  `E:\projects\SpriteSheetTool\`，该目录下另有 `README.md` 说明恢复方法。
- `.gitignore` 已排除 `.venv/`、`build/`、`dist/`、`__pycache__/`、`dsh-usage/`、
  `backup/`、`*.bak` 与导出产物。
- ⚠️ **不要往 `.gitignore` 里加 `*.spec`** —— 那会把 `SpriteSheetTool.spec` 一起忽略，
  新克隆将缺少 PyInstaller 打包配置。
- 大改动前备一份完整历史：
  ```powershell
  git bundle create "E:\projects\SpriteSheetTool\repo-backup-$(Get-Date -Format yyyyMMdd).bundle" --all
  git bundle verify "E:\projects\SpriteSheetTool\repo-backup-$(Get-Date -Format yyyyMMdd).bundle"
  ```

### 模块分层

拆分后 `spritesheet/ui/` 按依赖方向单向分层，禁止反向 import：

```
models ← transform ← widgets ← image_viewer / export_dialog ← preview ← main_window
settings_dialog / settings   是叶子（只被 main_window 引用），不反向依赖任何 UI 模块
```

每次改动后跑这三步即可覆盖回归：

```powershell
# 1. 编译 + 导入（确认无循环 import / 无 NameError）
.venv\Scripts\python.exe -m compileall -q spritesheet main.py

# 2. offscreen 冒烟回归：载图→检测元素→建动作→变换→导出 PNG/JSON/GIF/.sstool→工程往返
.venv\Scripts\python.exe smoke_test.py

# 3. 性能基准（可选）
.venv\Scripts\python.exe bench.py
```

要一次跑完**整套**专项回归（下面表格里 21 个 `verify_*.py`，实测约 **20 秒**，
全部通过时没有输出；有失败会打印 `FAILED: 脚本名`）：

```powershell
Get-ChildItem tools\verify_*.py | Where-Object { $_.Name -notin @('verify_move.py','verify_class_members.py') } |
  ForEach-Object { .venv\Scripts\python.exe $_.FullName; if ($LASTEXITCODE -ne 0) { "FAILED: $($_.Name)" } }
```

### 专项回归脚本

每个 `verify_*.py` 都是**可独立运行**的断言集合（退出码 0 = 全过），末尾打印 `XXX OK` / `XXX FAILED`。
界面的断言尽量用**真入口 + 真像素**（QTest 真点击/真拖动、渲染成图后数像素），
而不是只读内部状态 —— 上一版"点标签不切工程"就是因为回归绕开真实入口而漏掉的。

| 脚本 | 用途 |
| --- | --- |
| `smoke_test.py` | offscreen 冒烟回归（18 项，跨全部主流程：载图/检测/动作/变换/复用/导出/工程往返） |
| `verify_tabs.py` | 工程标签页（位置 / 新建 / 切换不串味 / 改名 / 关闭 / 标签栏逐像素 / 标签上不画未保存点） |
| `verify_offset_drag.py` | 帧偏移拖动（拖动时**帧真的动**、画布区域冻结、松手才重算并集；真滑块 + 真像素） |
| `verify_no_action_hint.py` | 无动作提示（点图片/变换按钮/合成按钮都要提示「先添加动作」，且不留下脏数据） |
| `verify_frame_offset.py` | 逐帧偏移（一个帧一个偏移量、互不影响、切帧同步控件） |
| `verify_adjust_panel.py` | 变换按钮布局（每行两个 / 摘要独占一行 / 窄了换行 / **8 个按钮等宽** / 清空变换悬停图标转白） |
| `verify_transform_modes.py` | 自由变换各模式的**角点数值**回归（旋转/缩放/斜切/透视/扭曲各自该怎么联动） |
| `verify_glyphs.py` | **字形**回归（必须跑真实 windows 平台：叉号不是缺字方框、三段文字对齐、标签不因标脏改变外观） |
| `verify_header.py` | 顶部名称栏（无图标 / 首字母更大 / 两段按大写带垂直对齐） |
| `verify_menu.py` | 菜单结构与顺序（含「检查更新」已移出文件菜单） |
| `verify_frame_badges.py` | 帧号色块排版（多动作复用同一元素时不得互相遮盖） |
| `verify_composite_badges.py` | 合成帧色块排版（多动作合成同一组元素时不得互相遮盖） |
| `verify_settings.py` | 软件设置（默认值 / 关掉后不写缩略图 / 描边分层 / 弹窗接线） |
| `verify_project_format.py` | 工程文件格式（新布局 / 旧布局兼容 / 缩略图退回 / 损坏文件） |
| `verify_export_names.py` | 导出选项默认值与顺序 + 同名文件处理 |
| `verify_roundtrip.py` | 导出→导入逐字段往返审计（四种 JSON 格式，含逐帧像素与帧间位移） |
| `verify_single_instance.py` | 单实例（协议 / 探测 / 真起第二个进程的端到端转发） |
| `verify_updater.py` | 更新检查端到端核对（地址可达 / 证书严格校验 / 版本列表解析） |
| `verify_checker.py` | 棋盘格平铺改造的逐像素等价验证 |
| `verify_perf_equiv.py` | 性能优化等价性验证（对照原算法，含真实素材） |
| `verify_icon_ratio.py` | 图标文件层（方形帧 / 图比例不变 / 按高度铺满 / 逐档独立缩放） |
| `verify_icon_imagelist.py` | 图标**显示**层（走 `SHGetFileInfo` 真实路径，确认没被拉伸压扁；含自检） |

### 诊断与一次性工具

这些不参与上面的整套回归：平时排查用的诊断工具，加上重构期间做**取证**用的一次性脚本
（它们不是"没写好的回归"，是不需要每次都跑）。其中需要传文件路径的几个，不带参数运行时
会报用法错误或 `IndexError`，这是预期的 —— 用法见各脚本文件头。

| 脚本 | 用途 |
| --- | --- |
| `inspect_theme.py` | 界面配色**诊断**（不是断言）：整窗渲染找"成条的浅色区域"并打印各部件颜色 |
| `profile_hotpaths.py` | 热路径采样（先测再改：重绘 / 打包 / 导出 / 检测） |
| `inspect_project_file.py` | 实存一个工程并检查其文件布局与缩略图 |
| `bench.py` | 性能基准（关键热路径 A/B，仓库根目录） |
| `make_icons.py` | 从源 PNG 生成多尺寸 `.ico`（方形帧；`--extract` 取原图；`--frames` 导出等比缩放序列） |
| `inspect_icon.py` | 打印 `.ico` 内嵌尺寸表并校验是否全是标准方形尺寸 |
| `static_scan.py` | 扫描未使用导入 / 疑似死代码（AST） |
| `check_spacing.py` | 检查顶层与类内定义的空行规范 |
| `block_tool.py` | 按限定名定位 / 剪切 / 抽取代码块（含 AST 一致性比对） |
| `free_names.py` | 列出某个代码块引用的自由名字（据此确定新模块需要哪些 import，不靠猜） |
| `assemble.py` | 用"手写文件头 + 抽取的代码块"拼出新模块 |
| `verify_move.py` | 证明代码块在文件间搬移前后 AST 完全一致（纯搬移证据） |
| `verify_class_members.py` | 逐个类成员比对两个文件，点名差异项 |
| `diff_vs_baseline.py` | 与清理前的基线逐块比对，让每处差异都能归因到某个已知改动 |
| `patch_checker.py` | 一次性：把三处棋盘格绘制换成 `paint_checkerboard`（找不到预期片段会明确报错） |

> `verify_move.py` / `verify_class_members.py` / `diff_vs_baseline.py` / `assemble.py` /
> `free_names.py` 需要传入文件路径（见各自文件头的 Usage），是重构期间的取证工具。

> 界面类脚本（`verify_menu.py` / `verify_settings.py` / `verify_export_names.py` 等）结尾
> 必须 `win.close()`：只有关窗口才会走 `closeEvent → ImageViewer.shutdown()`，
> 否则元素检测线程会与窗口析构撞车（`0xC0000005`，连续跑整套回归时必现）。

## 版本历史（近期）

每个版本都在 `installer/` 下留了安装包（目前保留 v1.1.20 起共 15 个）；**缺陷的成因、
修复方式与回归证据**记在 `SPLIT_GUIDE.md §9`（下面"详见"列就是对应的条目）。

| 版本 | 主要变化 | 详见 |
| --- | --- | --- |
| v1.1.34 | 删掉工程标签上的未保存圆点（那个点本来就不是有意设计的） | §9.14 ④ |
| v1.1.33 | 拖偏移时帧真的移动（松手才重算画布区域）；无动作时点图片/控件给提示；变换按钮等宽 + 清空变换悬停图标转白 | §9.14 ①②③ |
| v1.1.32 | 「清空变换」改红色；关闭叉号下移 3px 与工程名对齐；修当前帧微调的显示不同步 | — |
| v1.1.31 | 斜切恢复（"加清空变换"不是"替换斜切"）；清空变换挪进按钮网格 | — |
| v1.1.30 | 新增「清空变换」；Shift 三种吸附（锁轴 / 等比 / 15°） | — |
| v1.1.29 | 透视联动修正（邻角反向收放才是梯形）；关闭叉号悬停转 30° 变红 | — |
| v1.1.28 | 叉号改用真符号 ✕；首字母按**大写带**对齐；新增字形回归 | §9.13 |
| v1.1.27 | 标签改直角；关闭叉号换真符号 + 红色悬停动画；变换按钮两个一行 | §9.12 |
| v1.1.26 | 标签栏自己铺底色；文件对话框记住上次目录；导出以工程名命名/成夹；修「点标签不切工程」 | — |
| v1.1.25 | 标签栏移到软件名下方、版本号与名字底对齐；单实例（只跑一个进程） | §9.11 |
| v1.1.24 | 工程标签页（多工程：切换不串味、双击改名）；顶部名称栏去掉图标 | §9.10 |
| v1.1.23 | `SSTool.ico` 回退到方形帧（非方形会被资源管理器拉伸压扁）；无工程数据时不勾"导出 SSTOOL 工程" | §9.7 |
| v1.1.22 | 新增「软件设置」（缩略图开关 + 检查更新移入）；默认导出 SSTOOL 工程并排在 JSON 之前 | §9.7 9.8 9.9 |
| v1.1.17 | 工程文件改成「PNG + 容器」，资源管理器直接显示缩略图 | §9.6 |

更早的版本（v1.1.16 及以前）见 `pre-rebuild-backup` 分支的历史。

## 换电脑 / 迁移清单

- **必须拷贝**：`main.py`、`启动.bat`、`run.bat`、`requirements*.txt`、
  `spritesheet/`（整个包）、`icons/`、`SpriteSheetTool.ico`、`SSTool.ico`、
  `SSTool_源图.png`、`工程图标.png`、`build_windows.ps1`、`installer.iss`、
  `ChineseSimplified.isl`、`SpriteSheetTool.spec`、`update_server/`
- **无需拷贝**（自动重建/可选）：
  - `.venv/` —— 新电脑双击 `启动.bat` 自动创建并装依赖
  - `build/`、`dist/`、`installer/*.exe`、`release/` —— 打包产物，需要时重打
  - `__pycache__/`、`.pytest_cache/`、`dsh-usage/` —— 缓存，可随时删除
  - 根目录示例图（`003-妙蛙花.png` 等）—— 仅调试素材，可删
- 新电脑首次运行：装好 Python（勾选 PATH）→ 双击 `启动.bat` 即可。

## 许可证

本软件以 **GNU General Public License v3.0**（GPL v3）开源。

你可以自由使用、修改和再分发本软件，包括收费销售二进制版本——但必须同时向接收者提供完整源代码，且接收者有权在 GPL v3 条款下自由再分发。程序按"原样"提供，不附带任何担保。

完整许可证文本见项目根目录的 LICENSE 文件。

### 第三方开源组件

| 组件 | 版本 | 许可证 | 说明 |
| --- | --- | --- | --- |
| PyQt6 | 6.11 | GPL v3 / Commercial | GUI 框架（Riverbank Computing） |
| Pillow | 12.3 | HPND License | 图像处理 / GIF 导出 |
| PyInstaller | 6.22 | GPL v2+ with Bootloader Exception | 打包为 Windows 可执行文件 |
| Inno Setup 6 | — | Inno Setup License | 安装包制作（Jordan Russell） |
| ChineseSimplified.isl | — | Inno Setup 社区翻译 | 安装向导简体中文（kira-96） |

PyInstaller 的 GPL 例外条款明确允许用其构建和分发非自由（包括商业）程序，因此 PyInstaller 本身不会使本软件受到 GPL 传染；本软件选择 GPL v3 是因为使用了 PyQt6 的 GPL 版本。
