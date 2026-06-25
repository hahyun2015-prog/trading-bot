@echo off
cls

fltmc >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] 관리자 권한을 요청 중입니다... (UAC 승인 필요)
    goto UACPrompt
) else ( goto gotAdmin )


:UACPrompt
    if "%~1"=="--elevated" (
        echo [ERROR] Failed to obtain Administrator privileges after elevation attempt.
        echo Please run this script manually as Administrator.
        pause
        exit /B 1
    )
    if "%~1"=="" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs"
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated %*' -Verb RunAs"
    )
    exit /B

:gotAdmin
    if "%~1"=="--elevated" shift
    pushd "%CD%"
    CD /D "%~dp0"

title AMATS Market Regime Monitor
echo ==========================================================
echo   AMATS Market Regime & Auto-Switching Monitor
echo ==========================================================
echo   Calculates daily ADX and switches futures strategy
echo   between Parabolic SAR and Bollinger Band exits.
echo ----------------------------------------------------------
echo.

if not exist "venv32\Scripts\python.exe" (
    echo [ERROR] venv32 environment not found!
    echo Please run setup_env.bat first.
    echo.
    pause
    exit /b 1
)

echo [OK] venv32 python verified. Starting monitor...
echo      To terminate, press Ctrl+C or close this window.
echo ----------------------------------------------------------
echo.

"venv32\Scripts\python.exe" "market_regime_monitor.py"

echo.
echo ----------------------------------------------------------
echo [OK] Market Regime Monitor has terminated.
echo.
pause
