@echo off
color 0B
echo ===================================================
echo     Futures Backtester (64-bit Environment)
echo ===================================================
echo.
echo Running strategy backtest...
echo.

..\ai_trader\venv64\Scripts\python.exe -u backtester.py

echo.
pause
