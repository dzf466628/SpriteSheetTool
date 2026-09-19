# 无 BOM 写 UTF-8 文件（替代 Set-Content -Encoding UTF8，那个在 PS5.1 会带 BOM）
# 用法（在项目根目录的 PowerShell 中运行）：
#   powershell -ExecutionPolicy Bypass -File skills\dudu-ai-dev-kit\scripts\write_utf8.ps1 -Path "文件路径" -Content "内容"
# 文件不存在会自动创建（含父目录）；已存在则整体覆盖。

param(
    [Parameter(Mandatory=$true)]
    [string]$Path,
    [Parameter(Mandatory=$true)]
    [string]$Content
)

# 用“未解析路径”转绝对路径：文件尚不存在时 Resolve-Path 会直接报错，这里不能用它
$abs = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)

# 父目录不存在则先创建，否则 WriteAllText 会抛 DirectoryNotFoundException
$parent = Split-Path $abs -Parent
if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($abs, $Content, $utf8NoBom)

# 验证：检查头三个字节是否为 BOM（EF BB BF），空文件要先判断长度避免越界
$bytes = [System.IO.File]::ReadAllBytes($abs)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    Write-Host "❌ 仍有 BOM" -ForegroundColor Red
    exit 1
}
Write-Host "✅ 已写入无 BOM 的 UTF-8 文件: $abs" -ForegroundColor Green
