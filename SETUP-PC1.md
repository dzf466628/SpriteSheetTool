# SpriteSheetTool 1号电脑部署与协作指南

> 本文档给 **1号电脑**（家里那台）使用：怎么从 NAS 拉取代码、搭建运行环境、日常协作。
>
> 代码托管在 NAS 的 Gitea 上：`http://dudua.synology.me:3003/dzf466628/SpriteSheetTool.git`

---

## 一、快速部署（5 分钟）

### 1. 克隆代码

选一个存放位置（比如桌面），打开 PowerShell 执行：

```powershell
cd C:\Users\你的用户名\Desktop
git clone http://dudua.synology.me:3003/dzf466628/SpriteSheetTool.git
```

会提示输入用户名和密码：
- 用户名：`dzf466628`
- 密码：`angel466628`

> 第一次输入后，Windows 会自动保存凭据，以后不用再输。

### 2. 创建虚拟环境并装依赖

```powershell
cd SpriteSheetTool
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> ⚠️ 必须用项目里的 `.venv`，不要用系统 Python，避免污染全局环境。

### 3. 验证

```powershell
# 编译检查
.venv\Scripts\python.exe -m compileall -q spritesheet main.py

# 冒烟测试（offscreen，不弹窗）
$env:QT_QPA_PLATFORM="offscreen"
.venv\Scripts\python.exe smoke_test.py
# 预期：PASSED 18 | FAILED 0
```

### 4. 运行

双击 `启动.bat`，或：

```powershell
.venv\Scripts\python.exe main.py
```

---

## 二、日常协作流程

两台电脑（1号和2号）通过 NAS 上的 Gitea 同步代码，同时配置了 GitHub 作为异地备份。

### 双远程仓库规则（重要！）

| 远程名 | 地址 | 推送频率 | 网络要求 |
|---|---|---|---|
| `origin` | NAS Gitea | **每次改完都要 push** | 局域网直连，不需要代理 |
| `github` | GitHub | **只在大总结/阶段性完成时 push** | **必须开代理** |

### 标准工作流

```powershell
# 1. 每次开始改代码前，先拉取对方的最新代码
git pull origin main

# 2. 改代码...

# 3. 提交
git add .
git commit -m "类型: 做了什么 + 为什么"

# 4. 推送到 NAS（每次改完都要推）
git push origin main

# 5. 大总结/阶段性完成时，额外推送到 GitHub
# ⚠️ 推送前必须确认代理已打开！
git push github main
```

> ⚠️ **推 GitHub 前务必打开代理**，否则会连接失败。代理地址是 `127.0.0.1:11304`，git 已配置为只对 GitHub 走代理，NAS Gitea 直连。

### 提交信息规范

```
feat(ui): 新增帧偏移拖动时画布冻结
fix(export): 合成帧导出时偏移量写错
refactor(preview): 拆分自由变换手柄到独立模块
perf: 元素检测改用行程编码，2048x2048 从 1.4s 降到 50ms
docs: 更新 README 部署说明
chore: 升级 PyQt6 到 6.11
test: 新增标签页切换回归用例
```

**一个提交只做一件事。**

### 遇到冲突怎么办

如果 `git pull` 或 `git push` 报冲突：

1. **不要强制推送（`--force`）**。
2. 打开冲突文件，找到 `<<<<<<<` / `=======` / `>>>>>>>` 标记。
3. 和 2号电脑确认该保留哪一边，手动合并。
4. 合并完后：
   ```powershell
   git add <冲突文件>
   git commit -m "merge: 解决 XXX 冲突"
   git push
   ```

---

## 三、电脑编号标识

每台电脑在项目根目录创建一个 `.machine` 文件，写自己的编号，AI 会读取这个文件知道当前在哪台电脑上。

**1号电脑执行**：

```powershell
"1号" | Out-File -Encoding utf8 .machine
```

`.machine` 已加入 `.gitignore`，不会同步到 Git，每台电脑各自维护。

---

## 四、备份说明

### Git 备份范围

NAS 上的 Gitea **只备份源代码和提交历史**，以下内容**不进 Git**（已在 `.gitignore` 里）：

| 不备份的内容 | 原因 |
|---|---|
| `.venv/` | 虚拟环境，每台电脑自己建，可重建 |
| `build/`、`dist/` | 打包临时产物，可重新生成 |
| `installer/` | 打包出来的安装包（30MB+），需要时重新打包 |
| `.machine` | 本机编号标识，每台电脑不同 |
| `__pycache__/` | Python 缓存，自动生成 |
| `*.bak` | 备份文件 |

### 全量备份建议

如果需要全量备份（包括虚拟环境、构建产物等），在每台电脑上：

1. **代码** → 推送到 NAS Gitea（已做）
2. **虚拟环境** → 不需要备份，`pip install -r requirements.txt` 可重建
3. **构建产物/安装包** → 复制到 NAS 共享文件夹或外接硬盘
4. **用户工程文件**（.sstool 等）→ 自行备份到 NAS 或云盘

NAS 上 Gitea 的数据目录在 Docker 卷里，建议定期备份 NAS 本身（群晖 Hyper Backup 等）。

---

## 五、常见问题

### Q: 拉取代码后跑不起来，报 `ModuleNotFoundError`

A: 2号电脑加了新依赖但你没装。执行：
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

### Q: 推送时提示认证失败

A: 检查用户名密码是否正确，或在 Windows 凭据管理器里删除旧凭据后重新输入。

### Q: NAS 连不上怎么办

A: 确认 NAS 开机且 Gitea 容器在运行。NAS 地址是 `dudua.synology.me`，Gitea 端口 3003。

### Q: 想在自己电脑上试实验性代码，不想影响主分支

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

## 六、快速检查清单（每次提交前）

- [ ] `git pull` 过了，代码是最新的
- [ ] 编译通过：`.venv\Scripts\python.exe -m compileall -q spritesheet main.py`
- [ ] 冒烟测试通过：18/18
- [ ] 只提交了自己改的文件，没有把 `.venv/`、`build/`、`dist/` 带进去
- [ ] 提交信息写清了"做了什么 + 为什么"
- [ ] 如果改了 `requirements.txt`，已经通知 2号电脑
- [ ] `git push` 成功

---

*最后更新：2026-09-17 · 2号电脑编写 · 适用于 v1.1.34+*
