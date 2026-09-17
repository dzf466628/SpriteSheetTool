# Git 协作规范

## 双远程仓库

| 远程名 | 地址 | 推送频率 | 网络要求 |
|---|---|---|---|
| `origin` | 内网 Gitea | **每次改完都要 push** | 局域网直连，不需要代理 |
| `github` | GitHub | **只在大总结/阶段性完成时 push** | **必须开代理** |

**日常操作**：
```powershell
# 每次改完代码，推送到内网（日常主用）
git push origin main

# 大总结/阶段性完成时，额外推送到 GitHub（异地备份）
# ⚠️ 推送前必须确认代理已打开！
git push github main
```

> ⚠️ **推 GitHub 前务必打开代理**，否则会连接失败。

## 电脑编号 `.machine`
- 每台电脑在项目根目录创建 `.machine` 文件，内容写自己的编号（"1号"、"2号"）
- `.machine` 已加入 `.gitignore`，**不会进 Git**
- AI 工作时会先读 `.machine`，知道自己在哪台电脑上

## 提交前检查清单
- [ ] `git pull` 过了，代码是最新的
- [ ] 编译通过
- [ ] 冒烟测试通过
- [ ] 只 `git add` 了自己改的文件，没有把 `.venv/`、`build/`、`dist/` 带进去
- [ ] 提交信息写清了"做了什么 + 为什么"
- [ ] 如果改了 `requirements.txt`，已经通知对方电脑
- [ ] `git push` 成功

## 绝对不要做的事

| 禁止操作 | 原因 |
|---|---|
| `git push --force` | 会覆盖对方电脑的提交历史 |
| 把 `.venv/`、`build/`、`dist/` 提交进 Git | 每台电脑本地生成的，体积大且环境相关 |
| `git add .` 然后提交 | 容易把临时文件、调试产物带进去 |
| 改 `requirements.txt` 不告诉对方 | 对方拉取后虚拟环境里缺包会跑不起来 |

## Git + 代理 + SSL 坑
Windows 上 Git 默认用 schannel SSL 后端，走代理的时候会连接被关闭。
解决办法：切换 SSL 后端为 openssl
```powershell
git config --global http.sslBackend openssl
```

## 依赖管理
- 分两个文件：`requirements.txt`（运行时依赖）+ `requirements-dev.txt`（开发/打包用）
- 加新依赖后**必须提交并通知对方电脑**
- 升级依赖后先跑冒烟测试确认没坏，再更新版本范围
