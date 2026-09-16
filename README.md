# SpriteSheetTool

精灵图（Sprite Sheet）打包工具：自动检测原图元素、按动作逐帧编排动画，支持
自由变换（旋转/缩放/斜切/透视/扭曲）、帧复用，一键导出图集 PNG / GIF / 多种 JSON。

- 技术栈：Python 3.10+ · PyQt6 · Pillow（GIF 导出）
- 主题：深色界面 + 单一玫红强调色
- 当前版本：**v1.1.16**

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
  预览画布直接拖拽手柄编辑，点击空白或 Esc 退出
- **动画预览**：居中叠放播放、fps 调节（1–60）、往返/单次/循环模式、洋葱皮参照帧、
  地面线、逐帧微调偏移、锚点准星
- **导出**：原图坐标图集或重构打包图集（去重复用元素图块）、GIF、多格式 JSON、
  .sstool 工程文件；导出带实时进度条

## 环境要求

- Windows 10/11（64 位）
- Python 3.10+（开发环境为 3.11，未打包运行时需要）

## 新电脑部署（快速开始）

> 本工具设计为"拷贝即用"：只需要源码 + Python，不需要手工装任何东西。

1. **安装 Python**：从 <https://www.python.org/downloads/> 下载 3.11 或更新版本，
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
6. **导出**：点「导出」→ 选择动作与格式（图集 PNG、GIF、JSON、.sstool 工程）→ 选文件夹导出。

### 导出格式

| 格式 | 文件 | 说明 |
| --- | --- | --- |
| 图集 PNG | `spritesheet.png` | 原图坐标图集或重构打包图集（勾选"分别导出"时每个动作一个文件夹） |
| GIF | `*.gif` | 预览动画，保留 fps / 循环模式 / 帧顺序 |
| 本工具 JSON | `spritesheet.json` | 完整保留锚点/地面线/移动参数/烘焙偏移等编辑语义 |
| TexturePacker JSON | `spritesheet.tp_hash.json` / `spritesheet.tp_array.json` | 适配 Phaser 2 / PixiJS / Cocos2d-x |
| Aseprite JSON | `spritesheet.aseprite.json` | 带帧标签，适配 Godot / Phaser / Unity 导入器 |
| 工程文件 | `project.sstool` | 完整工程（双击可打开恢复）；文件本身是一张 **PNG**，见下节 |

### 工程文件的缩略图

`.sstool` 的文件布局是「**一张合法 PNG + 容器数据**」：

```
[ 缩略图 PNG（工程当前画面 + 右下角 工程图标.png 角标）… IEND ] [ 工程数据 ]
```

系统图片解码器读到 `IEND` 即停、忽略尾部数据，而本程序从 PNG 结束处开始解析工程数据。
配合安装包注册的缩略图处理器，**资源管理器会直接显示工程内容缩略图**（右下角带角标），
而不是一个看不出内容的通用图标。要点：

- 缩略图长边统一 192px；放大用最近邻（精灵图多为像素画，平滑会糊），缩小才用平滑。
- 旧版工程（容器在最前）仍可正常打开，只是那种文件没有缩略图。
- 缩略图处理器注册在 **当前用户** 范围（`HKCU\Software\Classes\.sstool\ShellEx\…`），
  不需要管理员权限，随卸载清理。
- 若旧文件仍显示旧图标，是资源管理器的缩略图缓存在起作用：**重装后新存的工程**会立即
  有缩略图；老文件可改个名或清理缩略图缓存（`ie4uinit.exe -show`）后刷新。
- 内容损坏/不是 PNG 的 `.sstool` 会**优雅回退**到 `SSTool.ico`，不会出现破图。


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

客户端启动时请求 `http://duadu.cc/download/SpriteSheetTool/` 目录列表，自动挑选
版本号最高的 `SpriteSheetTool_v.X.Y.Z.exe` 提示更新。服务器端零配置（纯目录索引），
详见 `update_server/README.txt`；每次发版只需上传新安装包到该目录。

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
├── SSTool.ico / 工程图标.png   # .sstool 文件关联图标 / 工程图标素材
├── icons/                      # 工具栏 SVG 图标
├── update_server/              # 自动更新服务器部署说明
├── smoke_test.py               # offscreen 冒烟回归（17 项，跨全部主流程）
├── bench.py                    # 性能基准（关键热路径 A/B）
├── tools/                      # 开发辅助脚本（静态扫描/搬移验证/等价性验证）
└── spritesheet/
    ├── __init__.py             # 版本号（__version__）
    ├── importer.py             # JSON 工程导入（TP/Aseprite/本工具格式）
    ├── project_file.py         # .sstool 工程读写 + QPixmap/JSON 片段辅助
    ├── updater.py              # 自动更新（版本检查/下载）
    └── ui/
        ├── main_window.py      # 主窗口装配 + 更新下载弹窗 + 撤销命令
        ├── widgets.py          # 通用控件：动作卡牌/标尺/紧凑输入/缩略播放器/棋盘格
        ├── image_viewer.py     # 原图区：元素检测 + 标尺画布 + 浮动工具条 + 帧复用
        ├── preview.py          # 预览区：预览画布 + 自由变换手柄 + 动画调节组
        ├── export_dialog.py    # 导出弹窗：动作选择 + 预览 + 图集打包/GIF/JSON
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

`tools/` 下的辅助脚本（纯开发用，不参与打包）：

| 脚本 | 用途 |
| --- | --- |
| `static_scan.py` | 扫描未使用导入 / 疑似死代码（AST） |
| `check_spacing.py` | 检查顶层与类内定义的空行规范 |
| `block_tool.py` | 按限定名定位 / 剪切 / 抽取代码块（含 AST 一致性比对） |
| `verify_move.py` | 证明代码块在文件间搬移前后 AST 完全一致（纯搬移证据） |
| `verify_perf_equiv.py` | 性能优化等价性验证（对照原算法，含真实素材） |
| `verify_checker.py` | 棋盘格平铺改造的逐像素等价验证 |
| `verify_frame_badges.py` | 帧号色块排版回归（多动作复用同一元素时不得互相遮盖） |
| `verify_composite_badges.py` | 合成帧色块排版回归（多动作合成同一组元素时不得互相遮盖） |
| `verify_menu.py` | 菜单结构与顺序回归 |
| `verify_roundtrip.py` | 导出→导入逐字段往返审计（四种 JSON 格式，含逐帧像素与帧间位移） |
| `profile_hotpaths.py` | 热路径采样（先测再改：重绘 / 打包 / 导出 / 检测） |
| `verify_project_format.py` | 工程文件格式回归（新布局 / 旧布局兼容 / 缩略图退回 / 损坏文件） |
| `inspect_project_file.py` | 实存一个工程并检查其文件布局与缩略图 |


## 换电脑 / 迁移清单

- **必须拷贝**：`main.py`、`启动.bat`、`run.bat`、`requirements*.txt`、
  `spritesheet/`（整个包）、`icons/`、`SpriteSheetTool.ico`、`SSTool.ico`、
  `工程图标.png`、`build_windows.ps1`、`installer.iss`、`ChineseSimplified.isl`、
  `SpriteSheetTool.spec`、`update_server/`
- **无需拷贝**（自动重建/可选）：
  - `.venv/` —— 新电脑双击 `启动.bat` 自动创建并装依赖
  - `build/`、`dist/`、`installer/*.exe`、`release/` —— 打包产物，需要时重打
  - `__pycache__/`、`.pytest_cache/`、`dsh-usage/` —— 缓存，可随时删除
  - 根目录示例图（`003-妙蛙花.png` 等）—— 仅调试素材，可删
- 新电脑首次运行：装好 Python（勾选 PATH）→ 双击 `启动.bat` 即可。
