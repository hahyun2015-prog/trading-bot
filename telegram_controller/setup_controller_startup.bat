@echo off
color 0D
echo =======================================================
echo    Telegram Remote Controller - Auto Startup Setup
echo =======================================================
echo.
echo This script will register the Telegram Controller to run
echo automatically in the background whenever you log into Windows.
echo.
echo Please RIGHT-CLICK this file and select "Run as Administrator"
echo If you haven't run this as Administrator, it might fail.
echo.
pause

set "START_SCRIPT=%~dp0run_controller.bat"

echo.
echo [*] Registering Startup Task (On Logon)
schtasks /create /f /tn "TelegramController_Start" /tr "cmd.exe /c start \"\" \"%START_SCRIPT%\"" /sc onlogon /rl highest /it

echo.
echo =======================================================
echo Setup Complete!
echo The Telegram Remote Controller will now start automatically
echo whenever you turn on your computer.
echo =======================================================
pause
