@echo off
cls
echo ====================================================
echo   AMATS Auto Startup Sequence
echo ====================================================
echo.

if not exist "%~dp0venv32\Scripts\python.exe" (
    echo [ERROR] venv32 not found! Please run setup_env.bat first.
    echo.
    pause
    exit /b 1
)
set "PY=%~dp0venv32\Scripts\python.exe"

echo [1/2] Launching TCA Telegram Controller...
start "AMATS | TCA" "%PY%" "%~dp0tca\tca_controller.py"

timeout /t 5 /nobreak > nul

echo [2/2] Launching ERA Trading Engine...
start "AMATS | ERA" "%PY%" "%~dp0era\era_order_manager.py"

echo.
echo ====================================================
echo   AMATS System successfully launched.
echo   Check Telegram using !status command.
echo   This window will close automatically.
echo ====================================================
echo.

timeout /t 3 /nobreak > nul
exit
