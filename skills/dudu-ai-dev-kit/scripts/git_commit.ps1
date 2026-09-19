# 一键提交推送：只暂存核心代码路径 → commit → 推送到内网 origin
# 用法（在项目根目录的 PowerShell 中运行）：
#   powershell -ExecutionPolicy Bypass -File skills\dudu-ai-dev-kit\scripts\git_commit.ps1 -Message "类型(模块): 提交信息"
# 注意：
#   - 只暂存 config.json 指定的核心代码路径，不做 git add -u / git add .，避免卷入无关改动
#   - 其余文件（README、构建脚本、requirements 等）如需提交，请手动 git add
#   - 只推内网 origin；GitHub 远程按规范在阶段性完成时手动推送（需开代理）

param(
    [Parameter(Mandatory=$true)]
    [string]$Message
)

# ---- 读取项目配置（PWD 即项目根）----
if (-not (Test-Path "config.json")) {
    Write-Host "❌ 项目根目录未找到 config.json。请先复制 config.example.json 为 config.json 并填写。" -ForegroundColor Red
    exit 1
}
$cfg = [System.IO.File]::ReadAllText((Resolve-Path "config.json").Path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

$pkg = $cfg.package_name
if (-not $pkg) {
    Write-Host "❌ config.json 缺少 package_name。" -ForegroundColor Red
    exit 1
}
$main  = "main.py";        if ($cfg.paths -and $cfg.paths.main)       { $main  = $cfg.paths.main }
$tools = "tools";          if ($cfg.paths -and $cfg.paths.tools_dir)  { $tools = $cfg.paths.tools_dir }

# 只暂存实际存在的核心代码路径（含其中的新增文件）；不碰其他已跟踪文件
$paths = @($pkg, $tools, $main) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
if (-not $paths) {
    Write-Host "❌ 配置中的核心路径都不存在，无法暂存。" -ForegroundColor Red
    exit 1
}

Write-Host "=== 暂存核心代码改动 ===" -ForegroundColor Cyan
git add -- $paths

# git diff --cached --quiet：无暂存差异时 exit 0，有时 exit 1
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "⚠️  核心路径没有需要提交的改动" -ForegroundColor Yellow
    exit 0
}

Write-Host "`n=== 本次提交内容 ===" -ForegroundColor Cyan
git diff --cached --name-status

Write-Host "`n=== 工作区其余改动（左列=已暂存会提交；右列/??=本次不会提交，需要请手动 add）===" -ForegroundColor DarkGray
git status --short | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

Write-Host "`n=== 提交 ===" -ForegroundColor Cyan
git commit -m $Message
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 提交失败（可能是 pre-commit hook 拦截），未推送。" -ForegroundColor Red
    exit 1
}

Write-Host "`n=== 推送到内网 origin ===" -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ 提交并推送成功" -ForegroundColor Green
} else {
    Write-Host "❌ 推送失败，检查网络（GitHub 远程需手动推送并开代理）" -ForegroundColor Red
    exit 1
}
