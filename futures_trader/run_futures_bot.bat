@echo off
title Futures Auto-Trader System
color 0E
echo ===================================================
echo     Futures Auto-Trader System (Day ^& Night Mode)
echo ===================================================
echo.

echo [Step 1] Launching Futures Order Manager (32-bit)
echo ===================================================
echo Starting Order Manager in a new background window...
echo Please log in to the Kiwoom mock server in the new window!
echo DO NOT CLOSE the new window while trading!
start "Futures Order Manager" cmd /c "..\venv32\Scripts\python.exe -u futures_order_manager.py"

echo.
echo ===================================================
echo [Step 2] Launching Futures Auto-Trader Loop
echo ===================================================
echo.
echo Starting the infinite loop for:
echo  1. Strategy Engine (BB ^& RSI Divergence)
echo.
echo Press Ctrl+C anytime to stop the loop.
echo.
echo Waiting 10 seconds for Order Manager login to complete...
timeout /t 10

..\ai_trader\venv64\Scripts\python.exe -u futures_auto_loop.py

echo.
pause
