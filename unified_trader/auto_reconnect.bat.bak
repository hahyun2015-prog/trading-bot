@echo off
echo ===================================================
echo     Auto Reconnecting Unified Auto-Trader System...
echo ===================================================
echo.
echo 1. Terminating existing bot processes...
call kill_unified_bot.bat
echo.
echo 2. Waiting 60 seconds for Kiwoom API session to clear...
timeout /t 60
echo.
echo 3. Restarting the bot...
start run_unified_bot.bat
exit
