@echo off
cls

:: 관리자 권한 자동 승격 (UAC) 원천 확보 (net session을 이용한 안전하고 신뢰할 수 있는 방식)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] 관리자 권한을 요청 중입니다... (UAC 승인 필요)
    goto UACPrompt
) else ( goto gotAdmin )


:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "%1", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    if exist "%temp%\getadmin.vbs" ( del "%temp%\getadmin.vbs" )
    pushd "%CD%"
    CD /D "%~dp0"

title AMATS ERA Order Manager
echo ==========================================================
echo   AMATS [ ERA ] Order & Risk Management Engine (Admin)
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
if "%1"=="auto" (
    echo [AUTO] Skipping pause in auto mode.
    exit
)
pause
