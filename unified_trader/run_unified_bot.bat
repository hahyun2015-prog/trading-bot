@echo off
title Unified Auto-Trader System
color 0B
echo ===================================================
echo     Unified Auto-Trader System (Day 60%% + Swing 40%%)
echo ===================================================
echo.

echo Starting Unified Order Manager (Day ^& Swing Integrator)
echo Please log in to the Kiwoom mock server when prompted.
echo The manager will automatically collect data and process strategies.
echo DO NOT CLOSE this window while trading!
echo.
echo Press Ctrl+C anytime to stop.
echo ===================================================
echo.

..\ai_trader\venv32\Scripts\python.exe -u unified_order_manager.py

echo.
pause
