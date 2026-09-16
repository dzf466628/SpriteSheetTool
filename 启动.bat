@echo off
setlocal
cd /d "%~dp0"

rem ================================================================
rem  SpriteSheetTool launcher - auto-fix, supports BOTH environments
rem  Env 1: project .venv - used first if it can run; missing deps
rem         are installed into the venv automatically
rem  Env 2: system Python - fallback when .venv is missing or broken;
rem         prefers one that already has PyQt6 and Pillow,
rem         otherwise installs the deps automatically
rem  A broken .venv pointing to a missing interpreter is skipped.
rem ================================================================

set "PYTHON="

rem ---- Env 1: project .venv (prefer if it can run) ----
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" --version >nul 2>nul
    if not errorlevel 1 set "PYTHON=%~dp0.venv\Scripts\python.exe"
)

rem ---- Env 2: system Python, prefer one with deps installed ----
if not defined PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 call :try_ready "python"
)
if not defined PYTHON (
    where py >nul 2>nul
    if not errorlevel 1 call :try_ready "py"
)
for /d %%D in ("C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python3*") do call :try_dir_ready "%%D"

rem ---- Env 2 fallback: runnable but deps missing (auto-install later) ----
if not defined PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 call :try_any "python"
)
if not defined PYTHON (
    where py >nul 2>nul
    if not errorlevel 1 call :try_any "py"
)
for /d %%D in ("C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python3*") do call :try_dir_any "%%D"

if not defined PYTHON (
    echo [ERROR] No usable Python found - need Python 3.10 or newer.
    echo Install Python from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [INFO] Using Python: %PYTHON%

rem ---- Ensure deps, install if missing ----
"%PYTHON%" -c "import PyQt6, PIL" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Runtime deps missing, installing from requirements.txt ...
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. Run manually:
        echo   "%PYTHON%" -m pip install -r "%~dp0requirements.txt"
        pause
        exit /b 1
    )
)

rem ---- Launch ----
"%PYTHON%" "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Program exited with an error. See traceback above.
    pause
)
endlocal
exit /b 0

rem ---- Sub: select candidate if it can import PyQt6+PIL ----
:try_ready
if defined PYTHON exit /b 0
%1 -c "import PyQt6, PIL" >nul 2>nul
if not errorlevel 1 set "PYTHON=%~1"
exit /b 0

rem ---- Sub: select candidate if it can run (--version) ----
:try_any
if defined PYTHON exit /b 0
%1 --version >nul 2>nul
if not errorlevel 1 set "PYTHON=%~1"
exit /b 0

rem ---- Sub: user-dir Python3x, select if it can import PyQt6+PIL ----
:try_dir_ready
if defined PYTHON exit /b 0
if exist "%~1\python.exe" (
    "%~1\python.exe" -c "import PyQt6, PIL" >nul 2>nul
    if not errorlevel 1 set "PYTHON=%~1\python.exe"
)
exit /b 0

rem ---- Sub: user-dir Python3x, select if it can run (--version) ----
:try_dir_any
if defined PYTHON exit /b 0
if exist "%~1\python.exe" (
    "%~1\python.exe" --version >nul 2>nul
    if not errorlevel 1 set "PYTHON=%~1\python.exe"
)
exit /b 0
