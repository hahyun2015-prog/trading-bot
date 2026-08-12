@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: 사용자가 stop_system.bat으로 의도적으로 종료했거나, 키움 재연결 시퀀스가 진행 중이면 감시를 건너뜀
if exist "system_stopped.flag" exit /b 0
rem (2026-08-13) 긴급정지 플래그도 존중한다. 종전에는 system_stopped.flag 만 봐서,
rem   emergency_kill.flag 로 멈춘 상태를 "ERA down" 으로 오판해 되살릴 수 있었다.
if exist "emergency_kill.flag" exit /b 0
rem 재접속 시퀀스가 도는 중에는 감시를 쉰다. 종전에는 auto_reconnect_era.bat 이
rem   이 목적으로 system_stopped.flag 를 직접 만들었다가 끝에서 지웠는데, 그 삭제가
rem   사용자가 만든 정지 플래그까지 함께 지워버리는 사고 경로였다. 마커를 분리한다.
if exist "reconnect_in_progress.flag" exit /b 0

if not exist "%~dp0venv32\Scripts\python.exe" exit /b 1
set "PY=%~dp0venv32\Scripts\python.exe"

call :check_alive "era\era.pid" ERA_ALIVE
call :check_alive "tca\tca.pid" TCA_ALIVE

:: 재시작 쿨다운: 직전 재시작 후 2회(4분)는 재시작을 건너뜀 (연속 크래시->워치독 재기동 악순환 방지)
call :apply_cooldown "watchdog_cooldown_era.tmp" ERA_ALIVE
call :apply_cooldown "watchdog_cooldown_tca.tmp" TCA_ALIVE

if "%ERA_ALIVE%"=="0" (
    echo [%date% %time%] ERA down - restarting >> "%~dp0watchdog.log"
    (echo 2) > "%~dp0watchdog_cooldown_era.tmp"
    rem era_order_manager.py 직접 실행은 리더종목 필터를 우회하므로, 검증된 예약 작업을 트리거한다
    schtasks /run /tn "AMATS ERA Reconnect" >nul 2>&1
)
if "%TCA_ALIVE%"=="0" (
    echo [%date% %time%] TCA down - restarting >> "%~dp0watchdog.log"
    (echo 2) > "%~dp0watchdog_cooldown_tca.tmp"
    start "AMATS | TCA" "%PY%" "%~dp0tca\tca_controller.py"
)

:: KIS 야간선물 수집기는 세션 창(평일 17:55~다음날 05:15)에만 살아있어야 하므로,
:: 그 창 안에서만 생존을 확인하고 죽어있으면 재기동한다 (2026-08-07 재부팅으로
:: 수집기가 죽었는데 세션 도중 아무도 되살리지 않은 사고 이후 추가).
call :check_window KIS_WINDOW
if "%KIS_WINDOW%"=="1" (
    call :check_alive "kis\night_collector.pid" KIS_ALIVE
    call :apply_cooldown "watchdog_cooldown_kis.tmp" KIS_ALIVE
)
if "%KIS_WINDOW%"=="1" if "%KIS_ALIVE%"=="0" (
    echo [%date% %time%] KIS night collector down - restarting >> "%~dp0watchdog.log"
    (echo 2) > "%~dp0watchdog_cooldown_kis.tmp"
    schtasks /run /tn "AMATS KIS Night Collector Start" >nul 2>&1
)
exit /b 0

:check_alive
:: %1 = pid 파일 경로, %2 = 결과를 담을 변수명. 파일이 없거나 비어있거나, 그 PID로
:: 살아있는 python 프로세스를 못 찾으면 0(죽음), 찾으면 1(생존)로 설정.
set "%~2=0"
if not exist %1 exit /b 0
set "_PID="
for /f "usebackq delims=" %%i in (%1) do set "_PID=%%i"
if not defined _PID exit /b 0
tasklist /fi "PID eq %_PID%" 2>nul | findstr /i "python" >nul
if %errorlevel%==0 set "%~2=1"
exit /b 0

:apply_cooldown
:: %1 = 쿨다운 파일, %2 = 생존 변수명. 파일이 있으면 카운트를 1 감소시키고
:: 카운트가 남아있는 동안엔 해당 프로세스를 살아있는 것으로 처리(재시작 건너뜀).
if not exist %1 exit /b 0
set "_CD="
for /f "usebackq delims=" %%i in (%1) do set "_CD=%%i"
if not defined _CD ( del /f /q %1 >nul 2>&1 & exit /b 0 )
set /a _CD-=1
if !_CD! gtr 0 (
    (echo !_CD!) > %1
    set "%~2=1"
) else (
    del /f /q %1 >nul 2>&1
)
exit /b 0

:check_window
:: %1 = 결과를 담을 변수명. 지금이 KIS 야간선물 세션 창(평일 17:55~다음날 05:15,
:: "AMATS KIS Night Collector Start/Stop" 예약 작업의 요일 설정과 동일)인지 확인.
set "%~1=0"
"%PY%" -c "import datetime,sys; n=datetime.datetime.now(); wd=n.weekday(); t=n.time(); ok=(wd in (0,1,2,3,4) and t>=datetime.time(17,55)) or (wd in (1,2,3,4,5) and t<datetime.time(5,15)); sys.exit(0 if ok else 1)" >nul 2>&1
if %errorlevel%==0 set "%~1=1"
exit /b 0
