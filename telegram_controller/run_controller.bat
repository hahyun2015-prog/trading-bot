@echo off
cd /d "%~dp0"
color 0D
echo ===================================================
echo     Telegram Remote Controller
echo ===================================================
echo.
echo Waiting for commands from Telegram...
echo Do not close this window if you want to use remote control.
echo.

:LOOP
..\ai_trader\venv64\Scripts\python.exe -u telegram_controller.py

echo.
echo [Warning] Telegram Controller closed or crashed.
echo Restarting automatically in 5 seconds...
timeout /t 5
goto LOOP
