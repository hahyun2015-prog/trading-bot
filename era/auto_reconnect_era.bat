@echo off
rem chcp 65001 (Disabled to prevent CMD UTF-8 parser bug)
title AMATS ERA Auto-Reconnect
color 0C

rem 이 스크립트는 작업 스케줄러에 RunLevel=Highest로 등록된 "AMATS ERA Reconnect" 태스크를
rem 통해서만 실행되도록 바뀌었음(era_order_manager.py/tca_controller.py/reconnect_kiwoom.bat
rem 모두 schtasks /run으로 호출). 따라서 항상 이미 관리자 권한으로 실행되며, 과거의 UAC
rem 자체승격(-Verb RunAs) 로직은 화면잠김/RDP끊김 등 무인 환경에서 동의를 받을 인터랙티브
rem 데스크톱이 없어 pause에서 영원히 멈추는 문제가 있어 제거함.
pushd "%CD%"
CD /D "%~dp0"

echo ===================================================
echo     AMATS ERA Auto-Reconnecting System
echo ===================================================
echo.

:: AMATS Watchdog(2분 주기 생존감시)가 아래 60초 대기 구간 중 ERA가 죽어있는 걸 보고
:: 끼어들어 키움 세션이 안 비워진 채로 너무 일찍 재기동시키지 못하도록 일시 차단
echo %date% %time% > "%~dp0..\system_stopped.flag"

echo [1/3] Terminating existing ERA and Kiwoom processes...

:: 1. Kill ERA process using era.pid if it exists
if exist "%~dp0era.pid" (
    set /p ERA_PID=<"%~dp0era.pid"
    if not "%ERA_PID%"=="" (
        echo Terminating ERA PID %ERA_PID%...
        taskkill /f /pid %ERA_PID% >nul 2>&1
    )
    del /f /q "%~dp0era.pid" >nul 2>&1
)

:: 2. Hard kill zombie ERA processes just in case
echo Terminating ERA/TCA python processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*leader_order_manager.py*' -or $_.CommandLine -like '*tca_controller.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

:: 2b. 종료 확인 (2026-08-08 도입) — 위 taskkill/Stop-Process는 >nul 2>&1로 실패를 가리므로,
:: 권한 부족 등으로 조용히 실패해도 그대로 진행해왔다. 그 결과 옛 프로세스가 포트 9991을
:: 계속 쥔 채 60초 뒤 재기동이 충돌해서 죽는 사고가 실제로 있었다(이 배치를 관리자 권한
:: 없이 직접 실행했을 때 재현 — 정상 경로인 schtasks/TCA 트리거는 권한을 상속받아 대부분
:: 문제없지만, 실패를 감지 못 하는 구조 자체가 위험하므로 명시적으로 확인한다).
:: tasklist로 PID 존재만 확인한다 — Get-CimInstance의 CommandLine 필터는 관리자 권한
:: 프로세스를 비관리자 세션에서 조회할 때 빈 값을 반환해(2026-08-08 직접 재현 확인)
:: 바로 이 시나리오에서 오탐(거짓 성공)을 낸다.
if not defined ERA_PID goto eraKillOk
set "KILL_RETRY=0"
:checkEraKilled
tasklist /FI "PID eq %ERA_PID%" 2>nul | find /i "python.exe" >nul
if errorlevel 1 goto eraKillOk
set /a KILL_RETRY+=1
if %KILL_RETRY% GEQ 5 goto eraKillFailed
ping 127.0.0.1 -n 3 >nul
goto checkEraKilled

:eraKillFailed
echo.
echo [FATAL] 기존 ERA 프로세스를 종료하지 못했습니다 (권한 부족 추정 — 이 배치가
echo         관리자 권한 없이 실행되면 옛 프로세스에 대한 taskkill이 조용히 실패합니다).
echo         포트 충돌로 재기동이 실패하는 것보다 안전하니, 여기서 중단합니다.
"%~dp0..\venv32\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0..'); import notifier, time; notifier.send_message('🚨 <b>[ERA 재연동 실패]</b> 기존 프로세스 종료 실패(권한 부족 추정)로 재기동을 중단했습니다. 관리자 권한으로 다시 시도해주세요.'); time.sleep(2)" >nul 2>&1
if exist "%~dp0..\system_stopped.flag" del /f /q "%~dp0..\system_stopped.flag" >nul 2>&1
timeout /t 3 >nul
exit /b 1

:eraKillOk

:: 3. Terminate Kiwoom OpenAPI processes to clear session
echo Terminating Kiwoom OpenAPI helper processes...
taskkill /f /im opstarter.exe /t >nul 2>&1
taskkill /f /im ncStarter.exe /t >nul 2>&1
taskkill /f /im coStarter.exe /t >nul 2>&1
taskkill /f /im KOA_STARTER.exe /t >nul 2>&1

echo.
echo [2/3] Waiting 60 seconds for Kiwoom API session and sockets to clear...
ping 127.0.0.1 -n 61 >nul 2>&1

echo.
echo [3/3] Restarting ERA Trading Engine...
rem "auto" 인자로 run_era.bat의 UAC 재확인을 건너뛴다 - 이 배치 자체가 이미 관리자 권한
rem 스케줄 작업으로 실행 중인데, start로 띄운 자식이 그 권한을 상속 못 받아 매번 UAC
rem 재승격을 시도하다 무인 환경이라 조용히 실패하는 문제가 있었음 (2026-07-09 실측 확인)
start "" "%~dp0..\run_era.bat" auto

:: 재기동을 시작했으니 워치독 차단 해제 (ERA 자체가 또는 다음 watchdog 주기가 정상 감시하게 함)
if exist "%~dp0..\system_stopped.flag" del /f /q "%~dp0..\system_stopped.flag" >nul 2>&1

:: Re-enable Windows Task Scheduler task to ensure auto-start on next boot
echo Re-enabling Windows Task Scheduler AutoStart...
schtasks /change /tn "AMATS AutoStart" /enable >nul 2>&1

echo.
echo Reconnection sequence completed. This window will now close.
timeout /t 3 >nul
exit
