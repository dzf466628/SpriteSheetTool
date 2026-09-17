# 一键三件套检查：编译 + pyflakes + 冒烟测试
# 用法：.venv\Scripts\python.exe scripts\check.ps1

Write-Host "=== 1/3 编译检查 ===" -ForegroundColor Cyan
.venv\Scripts\python.exe -m compileall -q spritesheet main.py tools
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 编译失败" -ForegroundColor Red
    exit 1
}
Write-Host "✅ 编译通过" -ForegroundColor Green

Write-Host "`n=== 2/3 pyflakes 静态检查 ===" -ForegroundColor Cyan
$result = .venv\Scripts\python.exe -m pyflakes spritesheet tools 2>&1
# 排除已知误报（app = QApplication(sys.argv)）
$filtered = $result | Where-Object { $_ -notmatch "local variable 'app'" }
if ($filtered) {
    Write-Host "⚠️  发现问题：" -ForegroundColor Yellow
    $filtered
} else {
    Write-Host "✅ pyflakes 无问题（排除已知误报）" -ForegroundColor Green
}

Write-Host "`n=== 3/3 冒烟测试 ===" -ForegroundColor Cyan
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe smoke_test.py 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 冒烟测试失败" -ForegroundColor Red
    exit 1
}
Write-Host "`n✅ 全部检查通过" -ForegroundColor Green
