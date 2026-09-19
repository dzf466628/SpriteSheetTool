# 踩过的坑（30个）

## PyQt6 / UI 相关坑（21个）

### 坑 1：`app = QApplication(sys.argv)` 虽然 pyflakes 报未使用，但必须保留
- PyQt 必须有一个 QApplication 实例才能创建任何 QWidget
- 虽然代码里没直接用 `app` 变量，但删了整个程序就崩了
- pyflakes 会误报，**不要删**

### 坑 2：`box.addButton()` 有副作用，不能删调用
- `btn = box.addButton("保存", ...)` 这个调用会把按钮加到对话框里
- 虽然 `btn` 变量没被用到，但**函数调用必须保留**，只去掉赋值
- 错误做法：整行删掉 → 对话框里就没有这个按钮了
- 正确做法：`box.addButton("保存", ...)` 不赋值给变量

### 坑 3：模态窗口背景不要设透明
- 模态对话框必须有不透明背景，否则会透到后面的窗口
- 透明底只适合工具提示、浮层等非模态元素

### 坑 4：窗口最小尺寸没限制
- 用户把窗口拖到极小，控件全重叠了
- 必须设置 `setMinimumSize()`，保证窗口缩小到一定程度就不能再小了

### 坑 5：点击交互没有动效
- 按钮按下去没反应，用户以为没点中，反复点
- 所有可点击元素必须有 hover / pressed / checked 状态的视觉反馈

### 坑 6：hover 效果不统一
- 有的按钮 hover 变灰，有的没变，有的闪烁
- 全局 QSS 里统一定义 hover 样式，不要每个控件单独写

### 坑 7：长任务界面卡死
- 导出/加载/解析大文件时，界面转圈，用户以为崩了就强退
- 耗时操作必须放到子线程里，主线程保持响应，或者加进度条

### 坑 8：右键菜单样式不统一
- 有的右键菜单是深色（自己画的），有的是系统默认浅色，突兀
- 全局统一右键菜单样式，和整体风格保持一致

### 坑 9：对话框关闭后资源没释放
- 对话框关了，但里面的对象没销毁，内存越用越大
- 关闭对话框时确保正确释放资源

### 坑 10：新窗口打开后找不到
- 新窗口被主窗口挡住了，或者跑到屏幕外面去了
- 新窗口打开后要 `raise_()` + `activateWindow()`，确保在最前面

### 坑 11：拖拽过程中崩溃
- 拖文件到窗口，拖到一半程序退了
- dragEnterEvent / dropEvent 里的所有操作都要 try/except 兜底

### 坑 12：paintEvent 卡顿
- 每次重绘都重新计算所有内容，滚动画布卡
- 重绘前先做局部刷新，不要整窗重绘；缓存计算结果

### 坑 13：滚动条不跟手
- 滚动画布有延迟，不流畅
- 滚动条用极窄圆角样式，宽 6px 左右；能不出现滚动条就不要出现

### 坑 14：全局 QSS 覆盖了局部样式
- 改了全局 QSS 后，某个控件的样式就不对了
- 局部样式要用 `#objectName` 选择器，不要直接用类名覆盖全局

### 坑 15：主题切换后某些控件还是旧色
- 深色主题下漏改了某个控件，还是浅色，突兀
- 全局改主题后，逐个检查所有控件，不要漏

### 坑 16：字体加载失败
- 自定义字体没加载成功，默默回退到默认字体，看起来不对
- 字体加载后要检查是否成功，失败了要有兜底

### 坑 17：Python 对象被 GC 回收
- Qt 的 C++ 对象销毁了，Python 端还拿着引用，一调用就崩
- 持有 Qt 对象时，确保 Python 端有强引用；用 `sip.isdeleted()` 检查对象是否还活着

### 坑 18：事件过滤器被 GC 回收
- `installEventFilter` 不持有 Python 引用，过滤器对象悄悄被回收，静默失效
- 必须把过滤器对象存到一个长期存在的对象上（比如 `app._filter = obj`）

### 坑 19：子线程更新 UI
- 在子线程里改控件属性，界面卡死或者随机崩溃
- 子线程不能直接碰 UI，必须通过信号槽发到主线程里更新

### 坑 20：信号槽参数不匹配
- 连接信号槽的时候不报错，运行到才报错
- 连接时要仔细检查参数数量和类型，尽量用类型注解

### 坑 21：控件透明空白区域导致对齐错乱
- 很多控件自带透明的内边距/外边距，肉眼看不到，但实际占着位置
- 看起来文字左对齐了，实际文字前面还有一块透明 padding
- 两个控件"对齐"了，实际差了几像素
- AI 调布局的时候经常被这个坑
- 解决：显式 `setContentsMargins(0,0,0,0)` 去掉默认内边距；对齐靠数值不靠感觉；调试时给控件加临时背景色看实际边界

---

## Windows 系统坑（3个）

### 坑 22：Windows 控制台默认 GBK，输出特殊字符会崩
- 控制台编码是 GBK（代码页 936）
- 输出 ✕、✓ 等 Unicode 字符会 `UnicodeEncodeError` 崩溃
- 解决办法：脚本入口加重配：
  ```python
  import sys
  if sys.platform == "win32":
      sys.stdout.reconfigure(encoding="utf-8")
      sys.stderr.reconfigure(encoding="utf-8")
  ```

### 坑 23：QSettings 可能 AccessError
- 某些环境下（比如权限受限的账户），QSettings 读写会 AccessError
- 所有 QSettings 读写都要 `try/except` 兜底
- 异常时打印到 stderr，方便排查，但不要弹窗打扰用户

### 坑 24：PowerShell 5.1 写 UTF-8 会带 BOM
- `Set-Content -Encoding UTF8` 在 PowerShell 5.1 里会写入 BOM
- 很多工具/解析器对 BOM 很敏感
- 正确写法：
  ```powershell
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($path, $content, $utf8NoBom)
  ```

---

## Git / 网络坑（2个）

### 坑 25：Git + 代理 + schannel SSL 会连接中断
- Windows 上 Git 默认用 schannel SSL 后端
- 走代理的时候 schannel 会连接被服务器关闭
- 解决办法：切换 SSL 后端为 openssl
  ```powershell
  git config --global http.sslBackend openssl
  ```

### 坑 26：Docker Hub 国内拉不动
- Docker Hub 在国内网络下基本拉不动
- 公共镜像源很多都已失效或 403
- 解决方案：
  1. 开代理，git/docker 都走代理
  2. 或者在能上网的机器上 `docker pull` → `docker save` 导出 tar → 传到目标机器 `docker load`

---

## 编码坑（1个）

### 坑 27：.gitignore 混合编码
- 不要在 Windows 上用记事本编辑 .gitignore 然后保存
- 容易混入 GBK 字节导致整个文件编码混乱
- 所有源文件统一用 **UTF-8 无 BOM**

---

## AI 开发工具坑（2个）

### 坑 28：Edit 工具改 UTF-8 文件可能 OLD_STRING_NOT_FOUND
- 有时候 Edit 工具匹配不到字符串，明明看起来一模一样
- 原因：编码/换行符差异（LF vs CRLF）
- 解决办法：改用 PowerShell 按行号删除/替换，或者用 Python 脚本改

### 坑 29：pyflakes 误报
- **函数内** `app = QApplication(sys.argv)` 且后续没用到时，pyflakes 报 `local variable 'app' is assigned to but never used`——**这是误报，app 必须保留**（模块顶层的 app 不会报）
- 纯 pyflakes **不认** `# noqa` 注释（那是 flake8 / IDE 的约定）：check.ps1 靠字符串过滤兜底，代码里可同时加 `# noqa: F841` 给 flake8 / IDE 看
- 除了这个，其他未使用导入/变量基本是真的，可以清理

---

## PowerShell 脚本坑（1个）

### 坑 30：含中文/emoji 的 .ps1 必须存成 UTF-8 带 BOM，否则 PS5.1 直接语法错误
- Windows PowerShell 5.1 用 `-File` 执行**无 BOM** 的 .ps1 时，按系统 ANSI 代码页解析（中文系统是 GBK/936），**和控制台 `chcp` 是不是 65001 无关**
- 脚本里只要有中文或 ✅❌⚠️ 等 emoji，就会乱码，甚至把字符串截断报 `The string is missing the terminator`，整个脚本跑不起来
- **注意和坑 24 / 坑 27 区分，结论正好相反**：
  - `.py` / `.json` / `.gitignore` 等被其他工具解析的源码或数据文件 → **UTF-8 无 BOM**
  - `.ps1` 脚本自身（要交给 powershell.exe 5.1 执行）→ **UTF-8 带 BOM**
- 批量把 .ps1 转成带 BOM：
  ```powershell
  Get-ChildItem *.ps1 | ForEach-Object {
      $t = [IO.File]::ReadAllText($_.FullName, (New-Object Text.UTF8Encoding($false)))
      [IO.File]::WriteAllText($_.FullName, $t, (New-Object Text.UTF8Encoding($true)))
  }
  ```
- PowerShell 7（pwsh）默认按 UTF-8 读脚本、无此问题，但中文机器常只装 5.1，脚本统一存带 BOM 最保险
