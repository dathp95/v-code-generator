@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"

rem EDIT THESE SETTINGS ONCE. For a literal percent sign, write %%.
set "VC_KEY_PASSWORD=Vinfast@2026"
set "VC_PRIVATE_KEY=C:\Secure\VCodeKeys\vcode-activation-private.pem"
set "VC_CUSTOMER=Test Customer"
set "VC_EDITION=Professional"

if "%VC_KEY_PASSWORD%"=="CHANGE_ME" (
    echo Edit VC_KEY_PASSWORD in this BAT file before running.
    pause
    exit /b 1
)
if not exist "generate_activation.py" (
    echo Place this BAT beside generate_activation.py.
    pause
    exit /b 1
)
if not exist "%VC_PRIVATE_KEY%" (
    echo Private key file not found. Check VC_PRIVATE_KEY.
    pause
    exit /b 1
)

set "VC_DEVICE_ID="
set "VC_DAYS="
set /p "VC_DEVICE_ID=Machine ID: "
set /p "VC_DAYS=Number of days: "

set "VC_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "VC_PYTHON=%~dp0.venv\Scripts\python.exe"

"%VC_PYTHON%" -c "import os,sys,getpass,runpy; getpass.getpass=lambda *a,**k: os.environ['VC_KEY_PASSWORD']; sys.argv=['generate_activation.py','--device-id',os.environ.get('VC_DEVICE_ID',''),'--customer',os.environ['VC_CUSTOMER'],'--edition',os.environ['VC_EDITION'],'--days',os.environ.get('VC_DAYS',''),'--private-key',os.environ['VC_PRIVATE_KEY'],'--output-file','output/last_activation.txt']; runpy.run_path('generate_activation.py',run_name='__main__')"
if errorlevel 1 (
    echo.
    echo FAILED. Check Machine ID, days, password and Python dependencies.
    pause
    exit /b 1
)

echo.
echo SUCCESS. Key saved to output\last_activation.txt
clip < "output\last_activation.txt"
if errorlevel 1 (
    echo Clipboard unavailable. Copy the key from the output file.
) else (
    echo Activation key copied to clipboard.
)
pause
endlocal
