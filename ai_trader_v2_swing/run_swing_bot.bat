@echo off
color 0B
echo ===================================================
echo     AI Quant Trading System - Swing V2
echo ===================================================
echo.

echo [Step 1] Launching Swing Order Manager (32-bit)
echo ===================================================
echo.
echo Starting Order Manager in a new background window...
echo Please DO NOT CLOSE the new window while trading!
start "Swing Order Manager (Do Not Close)" cmd /k "..\ai_trader\venv32\Scripts\python.exe -u swing_order_manager.py"

echo.
echo ===================================================
echo [Step 2] Launching Swing Auto-Trader Loop (32-bit)
echo ===================================================
echo.
echo Starting the timer loop for:
echo  1. Waiting until 15:10 PM
echo  2. Scraping Breakout Stocks (swing_screener.py)
echo.
echo Press Ctrl+C anytime to stop the loop.
echo.

..\ai_trader\venv32\Scripts\python.exe -u swing_auto_loop.py

pause
