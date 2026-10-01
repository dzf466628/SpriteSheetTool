$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Run 启动.bat once or create .venv first."
    }
    $python = $pythonCommand.Source
}

$version = (& $python -c "from spritesheet import __version__; print(__version__)").Trim()
$distApp = Join-Path $projectRoot "dist\SpriteSheetTool"
$installerRoot = Join-Path $projectRoot "installer"
$installerPath = Join-Path $installerRoot "SpriteSheetTool_v.$version.exe"
$isccCandidates = @(
    (Join-Path ${env:ProgramFiles} "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 6\ISCC.exe")
)
$isccCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($isccCommand) { $isccCandidates += $isccCommand.Source }
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 ISCC.exe was not found. Install Inno Setup 6 first."
}

Write-Host "[1/3] Build PyInstaller onedir directory ..."
& $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $projectRoot "dist") --workpath (Join-Path $projectRoot "build") SpriteSheetTool.spec
if (-not (Test-Path (Join-Path $distApp "SpriteSheetTool.exe"))) {
    throw "PyInstaller did not create dist\SpriteSheetTool\SpriteSheetTool.exe"
}
# jsonschema_specifications discovers its metaschemas from a real package
# directory at runtime. Keep the directory beside the frozen Python modules;
# PyInstaller's collect hook can otherwise omit the extensionless vocabulary files.
$schemaSource = Join-Path $projectRoot ".venv\Lib\site-packages\jsonschema_specifications\schemas"
$schemaTarget = Join-Path $distApp "_internal\jsonschema_specifications\schemas"
if (-not (Test-Path $schemaSource)) {
    throw "Missing jsonschema_specifications schemas: $schemaSource"
}
New-Item -ItemType Directory -Force -Path $schemaTarget | Out-Null
& robocopy.exe $schemaSource $schemaTarget /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "Failed to copy jsonschema_specifications schemas (robocopy exit $LASTEXITCODE)"
}

Write-Host "[2/3] Compile Inno Setup installer ..."
New-Item -ItemType Directory -Force -Path $installerRoot | Out-Null
if (Test-Path $installerPath) {
    Remove-Item -LiteralPath $installerPath -Force
}
& $iscc "/DMyAppVersion=$version" (Join-Path $projectRoot "installer.iss")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $installerPath)) {
    throw "Inno Setup compilation failed or did not create $installerPath"
}

Write-Host "[3/3] Build complete"
Write-Host "Installer: $installerPath"
