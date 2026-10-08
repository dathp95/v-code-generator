@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "PYTHON_CMD="
set "PYTHON_ARGS="

if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=%PYTHON_EXE%"
    )
)

if not defined PYTHON_CMD (
    py -3 --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_ARGS=-3"
    )
)

if not defined PYTHON_CMD (
    python --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    echo Python was not found.
    echo Please install Python or recreate .venv before running this script.
    pause
    exit /b 1
)

echo V-CODE now uses Activation Keys instead of license.lic files.
echo.
echo Usage:
echo   %PYTHON_CMD% %PYTHON_ARGS% "%~dp0generate_activation.py" --device-id VC-1234-5678-ABCD-EF12-3456 --customer "Customer A" --edition Professional --days 30 --private-key C:\Secure\VCodeKeys\vcode-activation-private.pem
echo.
echo See README.md for the full admin workflow.

pause
endlocal
