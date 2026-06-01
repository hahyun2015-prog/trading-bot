@echo off
title AMATS ERA Order Manager
cls
echo ==========================================================
echo   AMATS [ ERA ] Order & Risk Management Engine
echo ==========================================================
echo   Kiwoom OpenAPI - 32bit Python Virtual Environment
echo ----------------------------------------------------------
echo.

:: Set working directory to the directory of this batch file
cd /d "%~dp0"

if not exist "venv32\Scripts\python.exe" (
    echo [ERROR] venv32 environment not found!
    echo Please run setup_env.bat first.
    echo.
    pause
    exit /b 1
)

echo [OK] venv32 python verified.
echo.

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

echo [OK] Starting ERA Order Manager...
echo      To terminate, press Ctrl+C or close this window.
echo ----------------------------------------------------------
echo.

"venv32\Scripts\python.exe" "era\era_order_manager.py"

echo.
echo ----------------------------------------------------------
echo [OK] ERA Order Manager has terminated.
echo.
pause
