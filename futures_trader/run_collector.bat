@echo off
color 0A
echo ===================================================
echo     Futures Data Collector (32-bit Environment)
echo ===================================================
echo.
echo Starting Kiwoom API Data Collector...
echo Please log in to the Kiwoom mock investment server.
echo.

..\ai_trader\venv32\Scripts\python.exe -u data_collector.py

echo.
pause
