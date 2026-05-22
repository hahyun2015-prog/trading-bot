@echo off
color 0B
echo ===================================================
echo     Futures Order Manager (32-bit Environment)
echo ===================================================
echo.
echo Starting Kiwoom API Order Manager...
echo Please log in to the Kiwoom mock investment server.
echo.

..\ai_trader\venv32\Scripts\python.exe -u order_manager.py

echo.
pause
