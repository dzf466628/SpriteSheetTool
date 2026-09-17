# SpriteSheetTool 两台电脑协作指南

> 本文档给 **1号电脑** 和 **2号电脑** 的协作者共同使用：怎么把工程跑起来、
> 怎么改代码、怎么同步，以及**绝对不要碰对方的环境**。
>
> **电脑编号**：
> - **1号电脑** = 家里那台（原始开发机）
> - **2号电脑** = 当前这台（新电脑，本文档由 2 号电脑编写）
>
> 两台电脑各自有独立的虚拟环境，代码通过 Git 同步，互不影响。

### 怎么让 AI 知道自己是几号电脑

每台电脑在**项目根目录**创建一个 `.machine` 文件，内容写自己的编号：

```powershell
# 1号电脑上执行：
"1号" | Out-File -Encoding utf8 .machine

# 2号电脑上执行：
"2号" | Out-File -Encoding utf8 .machine
```

- `.machine` 已加入 `.gitignore`，**不会进 Git**，每台电脑各自维护。
- AI 在这个项目里工作时会先读 `.machine`，就知道自己当前在哪台电脑上，
  从而正确区分"我这台"和"对方那台"，不会搞混环境。
- **1号电脑的协作者**：拉取代码后，请先创建自己的 `.machine` 文件写 `1号`。

---

## 1. 核心原则

1. **每台电脑有自己独立的虚拟环境**，互不共享、互不影响。
   - `.venv/` 目录**不进 Git**，每台电脑自己建。
   - 你在自己电脑上装/升级依赖，不会影响对方电脑。
2. **代码通过 Git 同步**，不要用 U 盘/微信直接拷文件。
3. **改代码前先 `git pull`**，改完后**立即提交并推送**，减少冲突。
4. **大改动前先沟通**，避免两台电脑同时改同一个文件的同一区域。

---

## 2. 在自己电脑上部署（从零开始）

> 以下步骤 1号电脑和 2号电脑都适用，在**自己的电脑**上执行。

### 2.1 环境要求

- Windows 10/11（64 位）
- Python 3.10 ~ 3.13（开发与打包推荐 **3.11** 或 **3.12**）
- Git

### 2.2 拉取代码

```bash
git clone <仓库地址> SpriteSheetTool
cd SpriteSheetTool
```

### 2.3 创建虚拟环境并装依赖

```powershell
# 在项目根目录下执行
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> ⚠️ **不要用系统 Python 直接跑**，必须用项目里的 `.venv`。
> 否则你装的包会污染系统环境，也可能和对方电脑的版本不一致。

### 2.4 验证

```powershell
# 1. 编译检查
.venv\Scripts\python.exe -m compileall -q spritesheet main.py

# 2. 冒烟测试（offscreen，不弹窗）
$env:QT_QPA_PLATFORM="offscreen"
.venv\Scripts\python.exe smoke_test.py
# 预期：PASSED 18 | FAILED 0
```

### 2.5 运行

双击 `启动.bat`，或：

```powershell
.venv\Scripts\python.exe main.py
```

---

## 3. Git 协作流程

### 3.1 日常工作流

```
每次开始改代码前：
  git pull origin main

改完代码后：
  git status                    # 看看改了哪些文件
  git add <具体文件>            # 只加你改的文件，不要 git add .
  git commit -m "类型: 做了什么 + 为什么"
  git push origin main          # 每次改完都要推送到 NAS Gitea
```

### 3.2 双远程仓库规则（重要！）

项目配置了两个远程仓库，分工不同：

| 远程名 | 地址 | 推送频率 | 网络要求 |
|---|---|---|---|
| `origin` | NAS Gitea | **每次改完都要 push** | 局域网直连，不需要代理 |
| `github` | GitHub | **只在大总结/阶段性完成时 push** | **必须开代理** |

**日常操作**：
```powershell
# 每次改完代码，推送到 NAS（日常主用）
git push origin main

# 大总结/阶段性完成时，额外推送到 GitHub（异地备份）
# ⚠️ 推送前必须确认代理已打开！
git push github main
```

> ⚠️ **推 GitHub 前务必打开代理**，否则会连接失败。代理地址是 `127.0.0.1:11304`，git 已配置为只对 GitHub 走代理，NAS Gitea 直连。

### 3.3 提交信息规范

```
feat(ui): 新增帧偏移拖动时画布冻结
fix(export): 合成帧导出时偏移量写错
refactor(preview): 拆分自由变换手柄到独立模块
perf: 元素检测改用行程编码，2048x2048 从 1.4s 降到 50ms
docs: 更新 README 部署说明
chore: 升级 PyQt6 到 6.11
test: 新增标签页切换回归用例
```

**一个提交只做一件事**，不要把"修 bug + 加功能 + 改格式"塞进同一个提交。

### 3.4 冲突处理

如果 `git pull` 或 `git push` 报冲突：

1. **不要慌，不要强制推送（`--force`）**。
2. 打开冲突文件，找到 `<<<<<<<` / `=======` / `>>>>>>>` 标记。
3. 和对方电脑的协作者确认该保留哪一边，手动合并。
4. 合并完后：
   ```powershell
   git add <冲突文件>
   git commit -m "merge: 解决 XXX 冲突"
   git push
   ```

### 3.5 绝对不要做的事

| 禁止操作 | 原因 |
|---|---|
| `git push --force` | 会覆盖对方电脑的提交历史 |
| 把 `.venv/`、`build/`、`dist/` 提交进 Git | 这些是每台电脑本地生成的，体积大且环境相关 |
| 直接修改对方电脑上的文件 | 每台电脑的工作区是独立的，通过 Git 同步 |
| `git add .` 然后提交 | 容易把临时文件、调试产物带进去 |
| 改 `requirements.txt` 不告诉对方 | 对方拉取后虚拟环境里缺包会跑不起来 |

---

## 4. 依赖管理

### 4.1 加新依赖

1. 在自己的虚拟环境里装：
   ```powershell
   .venv\Scripts\python.exe -m pip install <包名>
   ```
2. 把包名和版本范围写进 `requirements.txt`（运行时依赖）或 `requirements-dev.txt`（仅打包/开发用）。
3. 提交并推送，**通知对方电脑的协作者**："我加了 XXX 依赖，记得 `pip install -r requirements.txt`"。

### 4.2 升级依赖

```powershell
.venv\Scripts\python.exe -m pip install --upgrade <包名>
```

升级后跑一遍冒烟测试确认没坏，再更新 `requirements.txt` 里的版本范围并提交。

---

## 5. 版本号与发布

### 5.1 版本号位置（两处必须一致）

1. `spritesheet/__init__.py` → `__version__`
2. `installer.iss` → `MyAppVersion`

### 5.2 发版流程

```powershell
# 1. 改版本号（两处）
# 2. 提交
git add spritesheet/__init__.py installer.iss
git commit -m "chore: 版本升至 X.Y.Z"

# 3. 打包（需要 Inno Setup 6）
powershell -ExecutionPolicy Bypass -File build_windows.ps1

# 4. 安装包在 installer/SpriteSheetTool_v.X.Y.Z.exe
# 5. 上传到更新服务器，推送代码
git push
```

> ⚠️ **打包只在一台电脑上做**（固定发版机，建议 1号电脑），不要两台电脑同时打包，
> 否则安装包版本会混乱。

---

## 5.5 代码组织原则（重要！）

> 以下规则两台电脑都必须遵守，是 2026-09-17 代码清理后定下的。

### 现有大文件不拆分

以下 5 个文件目前较大，但**不要为了拆而拆**：

| 文件 | 行数 | 说明 |
|---|---|---|
| `spritesheet/ui/preview.py` | ~2026 行 | 画布渲染核心，拆分风险高 |
| `spritesheet/ui/main_window.py` | ~1600 行 | 主窗口逻辑 |
| `spritesheet/ui/image_viewer.py` | ~1339 行 | 帧预览控件 |
| `spritesheet/ui/widgets.py` | ~1228 行 | 自定义控件集合 |
| `spritesheet/ui/export_dialog.py` | ~1184 行 | 导出对话框 |

**原因**：软件目前功能正常，没有大问题。强行拆分大文件容易引入回归 bug，风险大于收益。

### 新功能新建文件

以后**新增功能时，优先新建文件**，不要往上面这 5 个大文件里继续堆代码：

- 新的 UI 控件 → 在 `spritesheet/ui/` 下新建 `.py` 文件
- 新的业务逻辑 → 在 `spritesheet/` 下新建 `.py` 文件
- 新的验证脚本 → 在 `tools/` 下新建 `.py` 文件

**例外**：如果新功能必须修改大文件（比如改 main_window 的菜单结构），可以改，但**只改必要部分**，不要顺手重构无关代码。

---

## 6. 目录结构速查

```
SpriteSheetTool/
├── main.py                  # 启动入口
├── 启动.bat                  # 双击启动（自动建 .venv / 装依赖）
├── requirements.txt         # 运行依赖（PyQt6、Pillow）
├── requirements-dev.txt     # 开发/打包依赖（PyInstaller）
├── build_windows.ps1        # 一键打包脚本
├── installer.iss            # Inno Setup 安装包配置
├── SpriteSheetTool.spec     # PyInstaller 配置（必须进 Git）
├── smoke_test.py            # 冒烟回归（18项）
├── bench.py                 # 性能基准
├── tools/                   # 开发辅助脚本（回归/静态扫描/诊断）
├── spritesheet/             # 核心代码
│   ├── __init__.py          # 版本号
│   ├── importer.py          # JSON 工程导入
│   ├── project_file.py      # .sstool 工程读写
│   ├── settings.py          # 软件设置
│   ├── single_instance.py   # 单实例守卫
│   ├── updater.py           # 自动更新
│   └── ui/                  # UI 层（分层：models→transform→widgets→...）
├── icons/                   # 工具栏 SVG 图标
├── .venv/                   # ⚠️ 本地虚拟环境，不进 Git，每台电脑自己建
├── build/                   # ⚠️ 打包临时目录，不进 Git
├── dist/                    # ⚠️ 打包产物，不进 Git
└── installer/               # ⚠️ 产出的安装包 .exe，不进 Git
```

---

## 7. 备份

每台电脑在自己的 **E 盘**维护备份，互不干扰：

| 电脑 | 备份目录 |
|---|---|
| 1号电脑 | `E:\projects\SpriteSheetTool\` |
| 2号电脑 | `E:\projects\SpriteSheetTool\` |

备份内容：源码快照（`.bak`）、Git 历史打包（`.bundle`）、其他不进 Git 的长期保留文件。

**大改动前备份一次 Git 历史**：

```powershell
cd C:\Users\Administrator\Desktop\SpriteSheetTool
git bundle create "E:\projects\SpriteSheetTool\repo-backup-$(Get-Date -Format yyyyMMdd).bundle" --all
git bundle verify "E:\projects\SpriteSheetTool\repo-backup-$(Get-Date -Format yyyyMMdd).bundle"
```

> ⚠️ 两台电脑的备份目录各自独立，不要把对方的备份文件拷到自己电脑上。

---

## 8. 常见问题

### Q: 拉取代码后跑不起来，报 `ModuleNotFoundError`

A: 对方电脑加了新依赖但你没装。执行：
```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Q: `.venv` 坏了 / 指向旧电脑路径

A: 删除重建即可，不影响代码：
```powershell
Remove-Item -Recurse -Force .venv
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Q: 冒烟测试里 `verify_glyphs.py` 报编码错误

A: Windows 控制台默认 GBK，输出 ✕ 会崩。在跑之前设：
```powershell
$env:PYTHONIOENCODING="utf-8"
```

### Q: 两台电脑同时改了同一个文件怎么办

A: 后推送的一方会遇到冲突，按第 3.3 节处理，**不要强制推送**。

### Q: 我想在自己电脑上试一些实验性代码，不想影响主分支

A: 建个分支：
```powershell
git checkout -b experiment/xxx
# 随便改，改完如果要合并回 main：
git checkout main
git pull
git merge experiment/xxx
git push
```

---

## 9. 快速检查清单（每次改完代码提交前）

- [ ] `git pull` 过了，代码是最新的
- [ ] 编译通过：`.venv\Scripts\python.exe -m compileall -q spritesheet main.py`
- [ ] 冒烟测试通过：18/18
- [ ] 只 `git add` 了自己改的文件，没有把 `.venv/`、`build/`、`dist/` 带进去
- [ ] 提交信息写清了"做了什么 + 为什么"
- [ ] 如果改了 `requirements.txt`，已经通知对方电脑的协作者
- [ ] `git push` 成功

---

*最后更新：2026-09-17 · 2号电脑编写 · 适用于 v1.1.34+*
