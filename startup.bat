@echo off
cls
echo ====================================================
echo   AMATS Auto Startup Sequence
echo ====================================================
echo.

:: Verify venv32 python environment
if not exist "%~dp0venv32\Scripts\python.exe" (
    echo [ERROR] venv32 not found! Please run setup_env.bat first.
    echo.
    pause
    exit /b 1
)
set "PY=%~dp0venv32\Scripts\python.exe"

echo [1/2] Launching TCA Telegram Controller...
start "AMATS | TCA" "%PY%" "%~dp0tca\tca_controller.py"

:: Delay for 5 seconds using ping (safe for non-interactive shells)
ping 127.0.0.1 -n 6 > nul

echo [2/2] Launching ERA Trading Engine...
start "AMATS | ERA" "%PY%" "%~dp0era\era_order_manager.py"

echo.
echo ====================================================
echo   AMATS System successfully launched.
echo   Check Telegram using !status command.
echo   This window will close automatically.
echo ====================================================
echo.

ping 127.0.0.1 -n 4 > nul
exit
