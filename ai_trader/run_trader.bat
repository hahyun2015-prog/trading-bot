@echo off
color 0A
echo ===================================================
echo     AI Quant Trading System (Auto-Loop Mode)
echo ===================================================
echo.

echo [Step 1] Launching AI Order Manager (32-bit)
echo ===================================================
echo.
echo Starting Order Manager in a new background window...
echo Please DO NOT CLOSE the new window while trading!
start "AI Order Manager (Do Not Close)" cmd /k ".\venv32\Scripts\python.exe -u order_manager.py"

echo.
echo ===================================================
echo [Step 2] Launching Auto-Trader Loop
echo ===================================================
echo.
echo Starting the infinite loop for:
echo  1. Theme Tracking (Finding Leaders)
echo  2. 3-Min Chart Screening
echo  3. AI Strategy Engine (Combo 1: VWAP + Divergence)
echo.
echo Press Ctrl+C anytime to stop the loop.
echo.

.\venv64\Scripts\python.exe -u auto_loop.py

pause
