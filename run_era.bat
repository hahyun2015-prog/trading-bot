@echo off
cls

:: 관리자 권한 자동 승격 (UAC) 원천 확보 (net session을 이용한 안전하고 신뢰할 수 있는 방식)
:: auto_reconnect_era.bat(RunLevel=Highest 스케줄 작업, 항상 이미 관리자 권한)이 "auto" 인자로
:: 호출하는 경로는 UAC 재확인을 건너뛴다. start로 띄운 자식이 부모의 관리자 권한을 완전히
:: 상속하지 못해 fltmc가 실패하고, 매번 UAC 재승격(-Verb RunAs)을 시도하다 무인 환경이라
:: 동의를 받을 인터랙티브 데스크톱이 없어 조용히 실패하며 python이 아예 시작을 못하는
:: 문제가 있었음 (2026-07-09 실측 확인).
if "%~1"=="auto" goto gotAdmin
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

"venv32\Scripts\python.exe" "era\leader_order_manager.py"

echo.
echo ----------------------------------------------------------
echo [OK] ERA Order Manager has terminated.
echo.
if "%1"=="auto" (
    echo [AUTO] Skipping pause in auto mode.
    exit
)
pause
