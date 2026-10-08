@echo off
setlocal

set "DURATION_DAYS=%~1"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "PYTHON_CMD="
set "PYTHON_ARGS="

if "%DURATION_DAYS%"=="" (
    set /p "DURATION_DAYS=Enter license duration in days: "
)

if "%DURATION_DAYS%"=="" (
    echo License duration is required.
    pause
    exit /b 1
)

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

"%PYTHON_CMD%" %PYTHON_ARGS% "%~dp0generate_license.py" %DURATION_DAYS%

if errorlevel 1 (
    pause
    exit /b %errorlevel%
)

pause
endlocal
