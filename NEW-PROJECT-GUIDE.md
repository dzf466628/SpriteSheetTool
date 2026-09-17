# 新项目开发手册（从 SpriteSheetTool 沉淀）

> 本文档是从 SpriteSheetTool（v1.1.34）项目中沉淀下来的开发规范、坑点和偏好。
> **以后开发任何新软件，先读这个文档**，照着做能少踩很多坑。
>
> 本文档由 2 号电脑整理，2026-09-17。

---

## 一、技术栈与环境

### 1.1 固定技术栈

| 组件 | 版本/选择 | 说明 |
|---|---|---|
| 语言 | Python 3.11.x | 不用 3.14+（太新，库可能不兼容） |
| GUI 框架 | PyQt6 6.11.x | 桌面应用首选 |
| 图像处理 | Pillow 12.x | |
| 虚拟环境 | `.venv/` | **绝对不用系统 Python 直接跑** |
| 打包 | PyInstaller + Inno Setup 6 | 安装包用 Inno，入口打包用 PyInstaller |

### 1.2 目录结构（继承现有模式）

```
项目根目录/
├── main.py                  # 启动入口
├── 启动.bat                  # 双击启动（自动建 .venv / 装依赖）
├── requirements.txt         # 运行依赖（PyQt6、Pillow 等）
├── requirements-dev.txt      # 开发/打包依赖（PyInstaller、pyflakes 等）
├── build_windows.ps1        # 一键打包脚本
├── installer.iss            # Inno Setup 安装包配置
├── 项目名.spec               # PyInstaller 配置（必须进 Git）
├── smoke_test.py            # 冒烟回归测试
├── tools/                   # 开发辅助脚本（verify_*.py 回归/静态扫描/诊断）
├── 项目包名/                 # 核心代码
│   ├── __init__.py          # 版本号
│   ├── settings.py          # 软件设置（QSettings）
│   ├── single_instance.py    # 单实例守卫
│   ├── updater.py           # 自动更新
│   └── ui/                  # UI 层（models→widgets→dialogs→...）
├── icons/                   # 图标资源
└── .venv/                   # ⚠️ 本地虚拟环境，不进 Git
```

### 1.3 依赖管理

- 分两个文件：`requirements.txt`（运行时依赖）+ `requirements-dev.txt`（开发/打包用）
- 加新依赖后**必须提交并通知对方电脑**："我加了 XXX 依赖，记得 `pip install -r requirements.txt`"
- 升级依赖后先跑冒烟测试确认没坏，再更新版本范围

---

## 二、代码风格与规范

### 2.1 命名规范

- 函数/变量：`snake_case`
- 类：`PascalCase`
- 常量：`UPPER_SNAKE_CASE`
- 私有成员：前缀 `_`

### 2.2 注释与文档

- **全中文注释**，包括文档字符串
- 关键函数必须有文档字符串（三引号中文）
- 注释要写"为什么这么做"，不是"做了什么"

### 2.3 函数与文件长度

- **短函数原则**：超过 50 行的函数考虑拆分
- **大文件不拆**：现有大文件（>1000 行）不要为了拆而拆，避免引入回归
- **新功能新建文件**：以后加新功能优先新建 `.py`，不要往大文件里继续堆

### 2.4 提交信息规范

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

---

## 三、UI 设计规范

### 3.1 整体风格

- **风格不固定**：根据每个软件的定位和受众来定，不一定都是深色
- **但必须风格统一**：所有窗口、弹窗、按钮风格必须一致，不要有的深色有的浅色
- **单一强调色**：不管什么风格，整个软件只用一个强调色，不要五颜六色

### 3.2 字体

- **非衬线体**：固定用 "Microsoft YaHei UI"
- 字号根据控件层级调整，不要随意用

### 3.3 间距与圆角

- 按钮 padding：5px 14px
- 控件圆角：4px
- 工具栏间距：4px ~ 8px
- 面板间距：根据层级递减

### 3.4 Windows 标题栏

- 所有顶层窗口**强制深色标题栏**（用 DWM API）
- 不要用系统默认的浅色标题栏，和深色软件风格不搭

### 3.5 动效要求

- **所有点击交互都要有动效**：按钮 hover、按下、选中状态都要有视觉反馈
- 不要做"点了没反应"的死按钮

### 3.6 坑点：模态窗口透明底

- ⚠️ **绝对不要把模态窗口的背景设成透明**
- 模态对话框必须有不透明的背景，否则会透到后面的窗口，看起来很乱
- 透明底只适合工具提示、浮层之类的非模态元素

### 3.7 文字排版

- **文字排版要整齐**：对齐、间距、行距都要统一
- 不要有的地方左对齐有的右对齐，不要行距忽大忽小

### 3.8 图标

- **优先用现成图标库**（如 IconPark），风格统一
- 没有合适的再 AI 生成
- 图标风格要多样，不要全是一个风格

---

## 四、测试与验证

### 4.1 冒烟测试

- 每个项目必须有 `smoke_test.py`
- 覆盖关键路径（启动、加载工程、保存、导出、撤销等）
- 用 `offscreen` 模式跑，不弹窗：
  ```powershell
  $env:QT_QPA_PLATFORM="offscreen"
  .venv\Scripts\python.exe smoke_test.py
  ```
- 预期：全部 PASSED，0 FAILED

### 4.2 tools/ 验证脚本

- 关键功能要有对应的 `tools/verify_*.py` 验证脚本
- 比如：字形验证、图标比例验证、标签页切换验证等
- 这些脚本是专门验证视觉/交互细节的，offscreen 模式测不了

### 4.3 静态检查

- 用 `pyflakes` 做静态检测：
  ```powershell
  .venv\Scripts\python.exe -m pyflakes 包名/ tools/
  ```
- 主要查：未使用导入、未使用变量、无意义 f-string

### 4.4 每次改完代码必做

1. 编译检查：`.venv\Scripts\python.exe -m compileall -q 包名/ main.py tools/`
2. 冒烟测试：18/18 全通过
3. pyflakes：无新增警告（排除已知误报）

---

## 五、打包与发布

### 5.1 打包方式（一直沿用这个）

- **PyInstaller** 打包成单文件/目录
- **Inno Setup** 做安装包
- 一键脚本：`build_windows.ps1`
- 产物在 `installer/项目名_v.X.Y.Z.exe`

### 5.2 版本号（两处必须一致）

1. `包名/__init__.py` → `__version__`
2. `installer.iss` → `MyAppVersion`

### 5.3 发版流程

```powershell
# 1. 改版本号（两处）
# 2. 提交
git add 包名/__init__.py installer.iss
git commit -m "chore: 版本升至 X.Y.Z"

# 3. 打包
powershell -ExecutionPolicy Bypass -File build_windows.ps1

# 4. 安装包在 installer/项目名_v.X.Y.Z.exe
# 5. 推送代码
git push
```

> ⚠️ **打包只在固定一台电脑上做**，不要两台电脑同时打包，否则版本会混乱。

### 5.4 其他内置功能

- **单实例守卫**：同一时间只能开一个软件实例
- **自动更新**：检查新版本并提示更新
- 这两个都要，新项目也带上

---

## 六、Git 协作规范

### 6.1 双远程仓库

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

### 6.2 电脑编号 `.machine`

- 每台电脑在项目根目录创建 `.machine` 文件，内容写自己的编号（"1号"、"2号"）
- `.machine` 已加入 `.gitignore`，**不会进 Git**
- AI 工作时会先读 `.machine`，知道自己在哪台电脑上

### 6.3 提交前检查清单

- [ ] `git pull` 过了，代码是最新的
- [ ] 编译通过
- [ ] 冒烟测试通过
- [ ] 只 `git add` 了自己改的文件，没有把 `.venv/`、`build/`、`dist/` 带进去
- [ ] 提交信息写清了"做了什么 + 为什么"
- [ ] 如果改了 `requirements.txt`，已经通知对方电脑
- [ ] `git push` 成功

### 6.4 绝对不要做的事

| 禁止操作 | 原因 |
|---|---|
| `git push --force` | 会覆盖对方电脑的提交历史 |
| 把 `.venv/`、`build/`、`dist/` 提交进 Git | 每台电脑本地生成的，体积大且环境相关 |
| `git add .` 然后提交 | 容易把临时文件、调试产物带进去 |
| 改 `requirements.txt` 不告诉对方 | 对方拉取后虚拟环境里缺包会跑不起来 |

---

## 七、踩过的坑（重要！）

### 7.1 PyQt6 相关坑

**坑 1：`app = QApplication(sys.argv)` 虽然 pyflakes 报未使用，但必须保留**
- PyQt 必须有一个 QApplication 实例才能创建任何 QWidget
- 虽然代码里没直接用 `app` 变量，但删了整个程序就崩了
- pyflakes 会误报，**不要删**

**坑 2：`box.addButton()` 有副作用，不能删调用**
- `btn = box.addButton("保存", ...)` 这个调用会把按钮加到对话框里
- 虽然 `btn` 变量没被用到，但**函数调用必须保留**，只去掉赋值
- 错误做法：整行删掉 → 对话框里就没有这个按钮了
- 正确做法：`box.addButton("保存", ...)` 不赋值给变量

**坑 3：模态窗口背景不要设透明**
- 模态对话框必须有不透明背景，否则会透到后面的窗口
- 透明底只适合工具提示、浮层等非模态元素

### 7.2 Windows 系统坑

**坑 4：Windows 控制台默认 GBK，输出特殊字符会崩**
- 控制台编码是 GBK（代码页 936）
- 输出 ✕、✓ 等 Unicode 字符会 `UnicodeEncodeError` 崩溃
- 解决办法：脚本入口加重配：
  ```python
  import sys
  if sys.platform == "win32":
      sys.stdout.reconfigure(encoding="utf-8")
      sys.stderr.reconfigure(encoding="utf-8")
  ```

**坑 5：QSettings 可能 AccessError**
- 某些环境下（比如权限受限的账户），QSettings 读写会 AccessError
- 所有 QSettings 读写都要 `try/except` 兜底，不要因为设置崩了主功能
- 异常时打印到 stderr，方便排查，但不要弹窗打扰用户

**坑 6：PowerShell 5.1 写 UTF-8 会带 BOM**
- `Set-Content -Encoding UTF8` 在 PowerShell 5.1 里会写入 BOM（字节顺序标记）
- 很多工具/解析器对 BOM 很敏感（比如 .gitignore、Python 源文件）
- 正确写法：
  ```powershell
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($path, $content, $utf8NoBom)
  ```

### 7.3 Git 相关坑

**坑 7：Git + 代理 + schannel SSL 会连接中断**
- Windows 上 Git 默认用 schannel SSL 后端
- 走代理的时候 schannel 会连接被服务器关闭
- 解决办法：切换 SSL 后端为 openssl
  ```powershell
  git config --global http.sslBackend openssl
  ```

**坑 8：Docker Hub 国内拉不动**
- Docker Hub 在国内网络下基本拉不动
- 公共镜像源（中科大、网易、百度云）很多都已失效或 403
- 解决方案：
  1. 开代理，git/docker 都走代理
  2. 或者在能上网的机器上 `docker pull` → `docker save` 导出 tar → 传到目标机器 `docker load`

### 7.4 编码坑

**坑 9：.gitignore 混合编码**
- 不要在 Windows 上用记事本编辑 .gitignore 然后保存
- 容易混入 GBK 字节导致整个文件编码混乱
- 所有源文件统一用 **UTF-8 无 BOM**

### 7.5 AI 开发工具坑

**坑 10：Edit 工具改 UTF-8 文件可能 OLD_STRING_NOT_FOUND**
- 有时候 Edit 工具匹配不到字符串，明明看起来一模一样
- 原因：编码/换行符差异（LF vs CRLF）
- 解决办法：改用 PowerShell 按行号删除/替换，或者用 Python 脚本改

**坑 11：pyflakes 误报**
- `app = QApplication(sys.argv)` 会报未使用，**这是误报，必须保留**
- 除了这个，其他未使用导入/变量基本是真的，可以清理

---

## 八、用户偏好速查

| 偏好 | 具体要求 |
|---|---|
| 回复语言 | 简体中文 |
| 沟通风格 | 不要正式前缀，直接干活；可以适当"冒犯"一点，像真人 |
| 修改原则 | 小心小心再小心，一个一个来，别影响现有功能 |
| 大文件 | 不拆，新功能新建文件 |
| 主题色 | 风格不固定，按软件定位来；但必须全软件统一、单一强调色 |
| 图标 | 优先现成图标库，没有再 AI 生成，风格要多样 |
| 动效 | 所有点击交互都要有动效反馈 |
| 文字排版 | 要整齐，对齐/间距/行距统一 |
| 模态窗口 | 背景不能透明，必须不透明 |
| 打包方式 | 一直用 PyInstaller + Inno Setup，不要换 |
| 双远程 | NAS Gitea 日常推，GitHub 大总结推，推 GitHub 要开代理 |

---

*最后更新：2026-09-17 · 2号电脑整理 · 从 SpriteSheetTool v1.1.34 项目沉淀*
