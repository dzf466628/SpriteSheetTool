# 开发工作流

## 标准流程

改完代码后，必须按这个顺序验证和提交：

### 1. 编译检查
```powershell
.venv\Scripts\python.exe -m compileall -q 包名/ main.py tools/
```
编译通过才能继续。

### 2. 静态检查（pyflakes）
```powershell
.venv\Scripts\python.exe -m pyflakes 包名/ tools/
```
- 排除误报：`app = QApplication(sys.argv)` 必须保留，pyflakes 会报但不用管
- 其他未使用导入/变量基本是真的，可以清理

### 3. 冒烟测试
```powershell
$env:QT_QPA_PLATFORM="offscreen"
.venv\Scripts\python.exe smoke_test.py
```
- 预期：全部 PASSED，0 FAILED
- offscreen 模式，不弹窗

### 4. Git 提交推送
```powershell
git add <具体文件>
git commit -m "类型(模块): 做了什么 + 为什么"
git push origin main
```

---

## 提交信息规范

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

## 改代码原则

1. **小心小心再小心**：一个一个来，不要一下子改一大堆
2. **每改一步就验证一步**，不要攒到最后
3. **大文件不拆**：现有大文件（>1000 行）不要为了拆而拆
4. **新功能新建文件**：以后加新功能优先新建 .py，不要往大文件里继续堆

---

## Windows 控制台编码问题

Windows 控制台默认 GBK，输出特殊字符（✕、✓）会崩溃。脚本入口要加重配：

```python
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
```
