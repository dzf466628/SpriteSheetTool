# 一键三件套检查：编译 + pyflakes + 冒烟测试
# 用法（在项目根目录的 PowerShell 中运行）：
#   powershell -ExecutionPolicy Bypass -File skills\dudu-ai-dev-kit\scripts\check.ps1
# 依赖项目根目录的 config.json（从 config.example.json 复制并填写 package_name / paths）。

# ---- 读取项目配置（脚本假定在项目根目录运行，PWD 即项目根）----
if (-not (Test-Path "config.json")) {
    Write-Host "❌ 项目根目录未找到 config.json。请先复制 config.example.json 为 config.json 并填写包名等信息。" -ForegroundColor Red
    exit 1
}
$cfg = [System.IO.File]::ReadAllText((Resolve-Path "config.json").Path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

$pkg = $cfg.package_name
if (-not $pkg) {
    Write-Host "❌ config.json 缺少 package_name（核心代码包名）。" -ForegroundColor Red
    exit 1
}
# paths 各项给默认值，配置缺失时也能跑
$venv  = ".venv";          if ($cfg.paths -and $cfg.paths.venv)       { $venv  = $cfg.paths.venv }
$main  = "main.py";        if ($cfg.paths -and $cfg.paths.main)       { $main  = $cfg.paths.main }
$tools = "tools";          if ($cfg.paths -and $cfg.paths.tools_dir)  { $tools = $cfg.paths.tools_dir }
$smoke = "smoke_test.py";  if ($cfg.paths -and $cfg.paths.smoke_test) { $smoke = $cfg.paths.smoke_test }

$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "❌ 找不到解释器 $py，请检查 config.json 的 paths.venv 或先创建虚拟环境。" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $pkg)) {
    Write-Host "❌ 找不到核心包目录 '$pkg'，请检查 config.json 的 package_name。" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $smoke)) {
    Write-Host "❌ 找不到冒烟测试脚本 '$smoke'，请检查 config.json 的 paths.smoke_test。" -ForegroundColor Red
    exit 1
}

# 只把实际存在的路径传给检查工具（tools / main 在个别项目里可能没有）
$compileTargets = @($pkg, $main, $tools) | Where-Object { $_ -and (Test-Path $_) }
$lintTargets    = @($pkg, $tools)        | Where-Object { $_ -and (Test-Path $_) }

Write-Host "=== 1/3 编译检查 ===" -ForegroundColor Cyan
& $py -m compileall -q $compileTargets
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 编译失败" -ForegroundColor Red
    exit 1
}
Write-Host "✅ 编译通过" -ForegroundColor Green

Write-Host "`n=== 2/3 pyflakes 静态检查 ===" -ForegroundColor Cyan
$result = & $py -m pyflakes $lintTargets 2>&1
# 排除已知误报：函数内 app = QApplication(sys.argv) 未被直接使用（坑1/坑29）；更彻底可在该行加 # noqa: F841
$filtered = $result | Where-Object { $_ -notmatch "local variable 'app'" }
if ($filtered) {
    Write-Host "⚠️  发现问题：" -ForegroundColor Yellow
    $filtered
} else {
    Write-Host "✅ pyflakes 无问题（已排除 app 误报）" -ForegroundColor Green
}

Write-Host "`n=== 3/3 冒烟测试 ===" -ForegroundColor Cyan
$env:QT_QPA_PLATFORM = "offscreen"
& $py $smoke 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 冒烟测试失败" -ForegroundColor Red
    exit 1
}
Write-Host "`n✅ 全部检查通过" -ForegroundColor Green
