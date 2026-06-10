@echo off
color 0E
echo =======================================================
echo    Futures Trading System - Auto Scheduler Setup
echo =======================================================
echo.
echo This script will register two tasks in Windows Task Scheduler:
echo 1) Start the bot automatically at 08:30 AM (Mon-Fri)
echo 2) Kill the bot automatically at 05:15 AM (Tue-Sat)
echo    (After the night session closes and before Kiwoom server maintenance)
echo.
echo Please RIGHT-CLICK this file and select "Run as Administrator"
echo If you haven't run this as Administrator, it might fail.
echo.
pause

set "START_SCRIPT=%~dp0run_futures_bot.bat"
set "STOP_SCRIPT=%~dp0kill_futures_bot.bat"

echo.
echo [*] Registering Startup Task (08:30 AM, Mon-Fri)
schtasks /create /f /tn "FuturesTrader_Start" /tr "cmd.exe /c start \"\" \"%START_SCRIPT%\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:30 /rl highest /it

echo.
echo [*] Registering Shutdown Task (05:15 AM, Tue-Sat)
schtasks /create /f /tn "FuturesTrader_Stop" /tr "cmd.exe /c start \"\" \"%STOP_SCRIPT%\"" /sc weekly /d TUE,WED,THU,FRI,SAT /st 05:15 /rl highest /it

echo.
echo =======================================================
echo Setup Complete!
echo Windows will now automatically run the futures bot.
echo =======================================================
pause
