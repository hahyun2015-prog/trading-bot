@echo off
color 0A
echo =======================================================
echo    Unified Trading System - Auto Scheduler Setup
echo =======================================================
echo.
echo This script will register two tasks in Windows Task Scheduler:
echo 1) Start the bot automatically at 08:30 AM (Mon-Fri)
echo 2) Kill the bot automatically at 03:40 PM (Mon-Fri)
echo.
echo Please RIGHT-CLICK this file and select "Run as Administrator"
echo If you haven't run this as Administrator, it might fail.
echo.
pause

set "START_SCRIPT=%~dp0run_unified_bot.bat"
set "STOP_SCRIPT=%~dp0kill_unified_bot.bat"

echo.
echo [*] Registering Startup Task (08:30 AM)
schtasks /create /f /tn "UnifiedTrader_Start" /tr "cmd.exe /c start \"\" \"%START_SCRIPT%\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:30 /rl highest /it

echo.
echo [*] Registering Shutdown Task (03:40 PM)
schtasks /create /f /tn "UnifiedTrader_Stop" /tr "cmd.exe /c start \"\" \"%STOP_SCRIPT%\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:40 /rl highest /it

echo.
echo =======================================================
echo Setup Complete!
echo Windows will now automatically run the trading bot on weekdays.
echo (Make sure your PC is turned on and not asleep at 08:30 AM)
echo =======================================================
pause
