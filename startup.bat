@echo off
cls
echo ====================================================
echo   AMATS Auto Startup Sequence
echo ====================================================
echo.

:: 스마트플러그/재부팅 시 네트워크 카드 활성화 대기 (25초)
echo [*] Waiting for Network Connection Stability (25s)...
ping 127.0.0.1 -n 26 > nul

:: Auto git pull updates (실패해도 로컬 코드로 계속 진행, 10초 타임아웃)
cd /d "%~dp0"
where git >nul 2>&1
if %errorlevel% equ 0 (
    echo [GIT] Checking latest GitHub repository updates...
    start /B /WAIT cmd /c "git pull origin main --quiet 2>&1 & exit" >nul 2>&1
    echo [GIT] Done.
) else (
    echo [GIT] git not installed. Skipping auto-update...
)
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
