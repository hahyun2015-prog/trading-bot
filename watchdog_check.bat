@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: 사용자가 stop_system.bat으로 의도적으로 종료했거나, 키움 재연결 시퀀스가 진행 중이면 감시를 건너뜀
if exist "system_stopped.flag" exit /b 0

if not exist "%~dp0venv32\Scripts\python.exe" exit /b 1
set "PY=%~dp0venv32\Scripts\python.exe"

call :check_alive "era\era.pid" ERA_ALIVE
call :check_alive "tca\tca.pid" TCA_ALIVE

:: 재시작 쿨다운: 직전 재시작 후 2회(4분)는 재시작을 건너뜀 (연속 크래시→워치독 재기동 악순환 방지)
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
