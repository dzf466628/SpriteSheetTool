# 无 BOM 写 UTF-8 文件（替代 Set-Content -Encoding UTF8，那个会带 BOM）
# 用法：.venv\Scripts\python.exe scripts\write_utf8.ps1 文件路径 "内容"

param(
    [Parameter(Mandatory=$true)]
    [string]$Path,
    [Parameter(Mandatory=$true)]
    [string]$Content
)

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Resolve-Path $Path).Path, $Content, $utf8NoBom)

# 验证
$bytes = [System.IO.File]::ReadAllBytes($Path)
if ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    Write-Host "❌ 仍有 BOM" -ForegroundColor Red
    exit 1
} else {
    Write-Host "✅ 已写入无 BOM 的 UTF-8 文件: $Path" -ForegroundColor Green
}
