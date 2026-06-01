@echo off
title AMATS TCA Controller
cls
echo ==========================================================
echo   AMATS [ TCA ] Telegram Controller
echo ==========================================================
echo   Telegram Bot Interface - System Monitoring & Control
echo ----------------------------------------------------------
echo.
echo   Available Telegram Commands:
echo   !status        - Current system status & balance
echo   !reconnect     - Force Kiwoom reconnect
echo   !restart       - Restart whole AMATS system
echo   !shutdown      - Gracefully stop AMATS system
echo.
echo ----------------------------------------------------------
echo.

:: Set working directory to the directory of this batch file
cd /d "%~dp0"

:: Auto git pull updates
where git >nul 2>&1
if %errorlevel% equ 0 (
    echo [GIT] Checking latest GitHub repository updates...
    git pull origin main --quiet 2>&1
    if %errorlevel% equ 0 (
        echo [GIT] GitHub code is up to date.
        for /f "usebackq tokens=*" %%v in (`git log --oneline -1`) do echo Latest Commit: %%v
    ) else (
        echo [GIT] git pull failed. Starting with local codebase...
    )
) else (
    echo [GIT] git not installed. Skipping auto-update...
)
echo.

if exist "venv32\Scripts\python.exe" (
    echo [OK] venv32 python verified.
    echo [OK] Starting TCA Controller...
    echo.
    "venv32\Scripts\python.exe" "tca\tca_controller.py"
) else (
    echo [WARNING] venv32 not found. Falling back to global python...
    echo [OK] Starting TCA Controller...
    echo.
    python "tca\tca_controller.py"
)

echo.
echo ----------------------------------------------------------
echo [OK] TCA Controller has terminated.
echo.
pause
