@echo off
cls
echo ====================================================
echo   AMATS Auto Startup Sequence
echo ====================================================
echo.

:: 스마트플러그/재부팅 시 네트워크 카드 활성화 대기 (25초)
echo [*] Waiting for Network Connection Stability (25s)...
ping 127.0.0.1 -n 26 > nul

cd /d "%~dp0"

:: 의도적 종료 플래그 제거 — 사람이 직접(또는 부팅) 시스템을 다시 켜는 것이므로
:: AMATS Watchdog가 더 이상 "꺼진 상태 유지"로 보지 않고 정상적으로 감시를 재개하게 함
if exist "system_stopped.flag" del "system_stopped.flag" >nul 2>&1

:: Auto git pull updates (실패해도 로컬 코드로 계속 진행, 10초 타임아웃)
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

echo [1/3] Launching TCA Telegram Controller...
start "AMATS | TCA" "%PY%" "%~dp0tca\tca_controller.py"


:: Delay for 5 seconds using ping (safe for non-interactive shells)
ping 127.0.0.1 -n 6 > nul

echo [2/3] Launching ERA Trading Engine...
:: era_order_manager.py 직접 실행은 리더종목 필터를 우회하므로, 검증된 예약 작업을 트리거한다
schtasks /run /tn "AMATS ERA Reconnect" >nul 2>&1

echo [3/3] Checking KIS night futures collector...
:: 세션 도중(평일 17:55~다음날 05:15) 재부팅되면 수집기가 죽는데, 예약된 Start 트리거는
:: 하루 한 번뿐이라 다음 트리거까지 아무도 되살리지 않는 사고가 있었음 (2026-08-07).
:: 재부팅이 세션 창 안에서 일어났으면 즉시 예약 작업을 트리거해 빈틈을 메운다.
"%PY%" -c "import datetime,sys; n=datetime.datetime.now(); wd=n.weekday(); t=n.time(); ok=(wd in (0,1,2,3,4) and t>=datetime.time(17,55)) or (wd in (1,2,3,4,5) and t<datetime.time(5,15)); sys.exit(0 if ok else 1)" >nul 2>&1
if %errorlevel% equ 0 (
    echo [KIS] Within night session window - restarting collector...
    schtasks /run /tn "AMATS KIS Night Collector Start" >nul 2>&1
) else (
    echo [KIS] Outside night session window - skip.
)

echo.
echo ====================================================
echo   AMATS System successfully launched.
echo   Check Telegram using !status command.
echo   This window will close automatically.
echo ====================================================
echo.

ping 127.0.0.1 -n 4 > nul
exit
