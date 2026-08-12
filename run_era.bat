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
    echo [*] 관리자 권한을 요청 중입니다... ^(UAC 승인 필요^)
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
echo   AMATS [ ERA ] Order ^& Risk Management Engine (Admin)
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

:: 기동 전 구문 자가검사 - 디스크 손상/편집 사고로 파일이 깨진 채 기동하면
:: 스케줄 작업 경로에서는 콘솔이 숨겨져 IndentationError가 눈에 띄지 않고, 시스템이
:: 조용히 죽은 상태로 장을 통째로 날린다 (2026-07-31 실측: 클라우드 동기화가
:: era_order_manager.py를 덮어써 기동 실패, 원인 파악까지 장중 수 시간 소요).
echo [CHECK] Verifying Python syntax before launch...
"venv32\Scripts\python.exe" -m py_compile "era\era_order_manager.py" "era\leader_order_manager.py"
if %errorlevel% neq 0 (
    echo.
    echo ==========================================================
    echo  [FATAL] Python syntax check FAILED - aborting startup.
    echo  era\era_order_manager.py or leader_order_manager.py is broken.
    echo  Restore from the timestamped .bak next to the file, e.g.
    echo    copy /Y "era\era_order_manager.py.bak_before_indicators_live_20260812_203829" "era\era_order_manager.py"
    echo  Do NOT use "git checkout -- era\era_order_manager.py" - the live file is
    echo  intentionally ahead of git HEAD ^(measurement-ledger hooks + indicator
    echo  single-sourcing^) and that command would silently discard both.
    echo ==========================================================
    echo.
    if "%1"=="auto" exit /b 1
    pause
    exit /b 1
)
echo [OK] Syntax check passed.
echo.

:: ===== [2026-08-12] indicators.py import self-check =====
:: WHY: era\era_order_manager.py does "import indicators as ind" at module top level
::      (line 20). py_compile above only COMPILES - it never executes imports - so a
::      missing/broken root indicators.py sails past it, prints "[OK] Syntax check
::      passed", and then dies with ModuleNotFoundError. Under the scheduled-task path
::      (era\auto_reconnect_era.bat) the console is hidden, so that failure is silent
::      and the engine is simply absent for the session. Same silent-death class as the
::      2026-07-31 incident. This check executes the import for real and stops here.
echo [CHECK] Verifying indicators.py import...
"venv32\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.getcwd()); import indicators; print('[OK] indicators.py import OK -> ' + indicators.__file__)"
if %errorlevel% neq 0 (
    echo.
    echo ==========================================================
    echo  [FATAL] indicators.py import FAILED - aborting startup.
    echo  era\era_order_manager.py cannot run without the root indicators.py module.
    echo  Expected at: %CD%\indicators.py
    echo  It is tracked in git - restore with:  git checkout -- indicators.py
    echo ==========================================================
    echo.
    if "%1"=="auto" exit /b 1
    pause
    exit /b 1
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
