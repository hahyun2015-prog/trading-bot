@echo off
title AMATS Daily Reconnect Scheduler Installer
color 0B

echo ====================================================
echo   AMATS Daily Reconnect Task Scheduler Installer
echo ====================================================
echo.

rem Check administrator privileges
net session > nul 2>&1
if errorlevel 1 (
    echo [ERROR] This script must be run as Administrator.
    echo Please right-click this file and select 'Run as administrator'.
    echo.
    pause
    exit /b 1
)

set "TASK_NAME=AMATS Daily Reconnect"
set "RECONNECT_BAT=%~dp0reconnect_kiwoom.bat"

echo [*] Removing existing task if it exists...
schtasks /delete /tn "%TASK_NAME%" /f > nul 2>&1

echo [*] Registering new daily task at 07:00 AM...
schtasks /create /tn "%TASK_NAME%" /tr "cmd.exe /c \"%RECONNECT_BAT%\"" /sc daily /st 07:00 /rl highest /f

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to register task scheduler.
    echo Please check file path or permissions.
) else (
    echo.
    echo ====================================================
    echo   [SUCCESS] Daily Reconnect Scheduler Registered!
    echo   - Task Name: %TASK_NAME%
    echo   - Run Time: Daily at 07:00 AM
    echo   - Target File: %RECONNECT_BAT%
    echo ====================================================
)

echo.
timeout /t 5
exit /b 0
