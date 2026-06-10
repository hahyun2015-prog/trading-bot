@echo off
rem chcp 65001 (Disabled to prevent CMD UTF-8 parser bug)
title AMATS ERA Auto-Reconnect
color 0C

echo ===================================================
echo     AMATS ERA Auto-Reconnecting System
echo ===================================================
echo.
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
echo Terminating any remaining era_order_manager.py processes...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'era_order_manager.py' -and $_.Name -match 'python' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1

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
start "" "%~dp0..\run_era.bat"

echo.
echo Reconnection sequence completed. This window will now close.
timeout /t 3 >nul
exit
