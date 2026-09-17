---
name: dudu-ai-dev-kit
description: "嘟嘟专属的桌面应用开发框架，封装了 PyQt6 桌面应用的全套开发规范、常用工具、检测流程、Git 协作规则、沟通偏好和踩过的坑。触发场景：开发新的 PyQt 桌面应用、改现有项目代码、跑检测提交推送、新项目初始化搭框架、排查开发坑点。不适用：纯聊天闲聊、做 PPT/Excel、写非开发文档、和桌面应用开发无关的任务。"
---

# 嘟嘟的 AI 开发工具包（dudu-ai-dev-kit）

这是嘟嘟专属的桌面应用开发框架。加载这个 skill 后，你就知道所有开发规范、常用工具、坑点、沟通规则，新项目直接套用，不用重新教。

---

## 触发条件

### 什么时候用
- 开发新的 PyQt6 桌面应用
- 改现有项目代码（UI、逻辑、导出等）
- 跑检测、提交、推送代码
- 新项目初始化搭框架
- 排查开发过程中的坑

### 什么时候不用
- 纯聊天、闲聊
- 查资料、写非开发相关文档
- 做 PPT、Excel 等非开发任务
- 和桌面应用开发无关的事

---

## 快速路由

| 场景 | 读哪个 reference |
|---|---|
| 收到新需求，不知道怎么回复 | `references/communication.md` |
| 改完代码要验证、提交 | `references/dev-workflow.md` |
| 做 UI、改界面 | `references/ui-guidelines.md` |
| Git 提交、推送、协作 | `references/git-collab.md` |
| 遇到报错、卡壳、不知道为什么 | `references/pitfalls.md` |
| 新开聊天、说"全面检查"/"体检一下" | `references/project-checkup.md` |

---

## 核心原则（先看这个）

1. **小心小心再小心**：一个一个来，不要影响现有功能
2. **大任务提前说步骤**：开工前就说清楚分几步，每步做完汇报确认
3. **问建议先回复**：用户问"怎么样""行不行"的时候，先回复分析，不要直接操作
4. **涉及国外站点提前说代理**：GitHub、Docker Hub 之类的，先说要开代理
5. **给现成的**：用户要什么直接给能复制粘贴的完整版本

---

## 目录结构

```
dudu-ai-dev-kit/
├── SKILL.md                    # 本文件，主入口
├── references/                 # 详细规范
│   ├── communication.md        # 沟通规范（15条）
│   ├── dev-workflow.md         # 开发工作流
│   ├── ui-guidelines.md        # UI 设计规范
│   ├── git-collab.md           # Git 协作规范
│   └── pitfalls.md             # 踩过的坑（29个）
├── templates/                  # 模板文件，新项目复制就能用
│   ├── smoke_test.py           # 冒烟测试模板
│   ├── verify_script.py        # 验证脚本模板
│   └── gitignore.template      # gitignore 模板
├── scripts/                    # 常用工具脚本
│   ├── check.ps1               # 一键三件套检查
│   ├── git_commit.ps1          # 一键提交推送
│   └── write_utf8.ps1          # 无 BOM 写 UTF-8
└── config.example.json         # 项目配置示例
```

---

## 版本

当前版本：v1.0.0
最后更新：2026-09-17
