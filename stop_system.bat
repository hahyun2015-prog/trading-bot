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

title AMATS System Safe Shutdown
echo ==========================================================
echo        AMATS [ ERA & TCA ] Safe Shutdown Manager
echo ==========================================================
echo.
echo   [1] 일반 종료 (보유 포지션 유지하며 프로그램만 안전 종료)
echo   [2] 긴급 정지 (보유 포지션 전량 시장가 청산 후 프로그램 종료)
echo   [3] 취소 (종료하지 않고 창 닫기)
echo.
echo ----------------------------------------------------------
set /p choice="원하시는 작업 번호를 입력하세요 (1-3): "

if "%choice%"=="1" goto clean_shutdown
if "%choice%"=="2" goto emergency_stop
if "%choice%"=="3" goto cancel
goto invalid

:clean_shutdown
echo.
echo ----------------------------------------------------------
echo  [1] 일반 종료 시퀀스를 구동합니다.
echo ----------------------------------------------------------
echo.

:: 워치독이 프로세스 종료를 크래시로 오인해 끼어들지 않도록 종료 전 플래그를 먼저 만들어둠
echo %date% %time% > "%~dp0system_stopped.flag"

:: ERA 주문 엔진 종료
if exist "era\era.pid" (
    for /f "usebackq" %%i in ("era\era.pid") do (
        echo [-] ERA 주문 엔진 종료 중... (PID: %%i)
        taskkill /f /pid %%i >nul 2>&1
    )
    del "era\era.pid" >nul 2>&1
) else (
    echo [!] era.pid 파일을 찾을 수 없습니다. 백그라운드 프로세스 정리 시도...
    taskkill /f /fi "IMAGENAME eq python.exe" /fi "WINDOWTITLE eq *ERA*" >nul 2>&1
)

:: TCA 컨트롤러 종료
if exist "tca\tca.pid" (
    for /f "usebackq" %%i in ("tca\tca.pid") do (
        echo [-] TCA 컨트롤러 종료 중... (PID: %%i)
        taskkill /f /pid %%i >nul 2>&1
    )
    del "tca\tca.pid" >nul 2>&1
) else (
    echo [!] tca.pid 파일을 찾을 수 없습니다. 백그라운드 프로세스 정리 시도...
    taskkill /f /fi "IMAGENAME eq python.exe" /fi "WINDOWTITLE eq *TCA*" >nul 2>&1
)

:: 키움 OpenAPI 잔여 프로세스 정리
echo [-] 키움 OpenAPI 잔여 프로세스 정리 중...
taskkill /f /im opstarter.exe /t >nul 2>&1
taskkill /f /im ncStarter.exe /t >nul 2>&1
taskkill /f /im KOA_STARTER.exe /t >nul 2>&1

echo.
echo ==========================================================
echo  AMATS 시스템 일반 종료 완료 (보유 포지션 유지됨)
echo ==========================================================
echo.
pause
exit

:emergency_stop
echo.
echo ----------------------------------------------------------
echo  [2] 긴급 정지 및 전량 청산 시퀀스를 구동합니다.
echo ----------------------------------------------------------
echo.
echo [!] 긴급정지 플래그 생성 중...
echo %date% %time% > emergency_kill.flag

:: 워치독이 이 종료 시퀀스 중간에 ERA/TCA를 재기동하지 않도록 플래그를 먼저 만들어둠
echo %date% %time% > "%~dp0system_stopped.flag"

echo.
echo [*] ERA 엔진이 플래그를 감지하고 포지션을 청산하도록 대기 중입니다...
echo     (5초 동안 대기한 뒤 프로세스를 강제 정리합니다.)
echo.
ping 127.0.0.1 -n 6 >nul

:: ERA 주문 엔진 종료
if exist "era\era.pid" (
    for /f "usebackq" %%i in ("era\era.pid") do (
        echo [-] ERA 주문 엔진 종료 중... (PID: %%i)
        taskkill /f /pid %%i >nul 2>&1
    )
    del "era\era.pid" >nul 2>&1
) else (
    taskkill /f /fi "IMAGENAME eq python.exe" /fi "WINDOWTITLE eq *ERA*" >nul 2>&1
)

:: TCA 컨트롤러 종료
if exist "tca\tca.pid" (
    for /f "usebackq" %%i in ("tca\tca.pid") do (
        echo [-] TCA 컨트롤러 종료 중... (PID: %%i)
        taskkill /f /pid %%i >nul 2>&1
    )
    del "tca\tca.pid" >nul 2>&1
) else (
    taskkill /f /fi "IMAGENAME eq python.exe" /fi "WINDOWTITLE eq *TCA*" >nul 2>&1
)

:: 키움 OpenAPI 잔여 프로세스 정리
echo [-] 키움 OpenAPI 잔여 프로세스 정리 중...
taskkill /f /im opstarter.exe /t >nul 2>&1
taskkill /f /im ncStarter.exe /t >nul 2>&1
taskkill /f /im KOA_STARTER.exe /t >nul 2>&1

:: 플래그 파일 정리
if exist "emergency_kill.flag" ( del "emergency_kill.flag" >nul 2>&1 )

echo.
echo ==========================================================
echo  AMATS 시스템 긴급 정지 완료 (전량 청산 시도됨)
echo ==========================================================
echo.
pause
exit

:cancel
echo.
echo 작업을 취소했습니다. 아무 키나 누르면 종료됩니다.
pause
exit

:invalid
echo.
echo 잘못된 입력입니다. 1에서 3 사이의 숫자를 입력해 주세요.
echo.
pause
goto gotAdmin
