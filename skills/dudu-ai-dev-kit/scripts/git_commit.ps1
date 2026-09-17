# 一键提交推送：add → commit → push origin main
# 用法：.venv\Scripts\python.exe scripts\git_commit.ps1 "类型(模块): 提交信息"

param(
    [Parameter(Mandatory=$true)]
    [string]$Message
)

# 只加当前改动的文件，不要 git add .
Write-Host "=== 暂存改动 ===" -ForegroundColor Cyan
git add -u
git add spritesheet/ tools/ main.py

Write-Host "`n=== 提交 ===" -ForegroundColor Cyan
git commit -m $Message

if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️  没有改动需要提交" -ForegroundColor Yellow
    exit 0
}

Write-Host "`n=== 推送到内网 Gitea ===" -ForegroundColor Cyan
git push origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ 提交并推送成功" -ForegroundColor Green
} else {
    Write-Host "❌ 推送失败，检查网络" -ForegroundColor Red
    exit 1
}
