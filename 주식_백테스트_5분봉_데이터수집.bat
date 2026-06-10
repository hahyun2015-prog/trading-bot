@echo off
cls
echo ===================================================
echo     AMATS Stock 5-Min Bar Data Collector
echo ===================================================
echo.
echo * Required Conditions:
echo 1. Kiwoom HTS (Hero4) must be running.
echo 2. Login must be completed.
echo.
echo Press any key to start downloading historical data...
pause > nul
echo.
echo [1/2] Changing directory...
cd ai_trader
echo [2/2] Running downloader script...
..\venv32\Scripts\python.exe download_history_5min.py
echo.
echo ===================================================
echo [Success] 5-Min bar data collected successfully!
echo ===================================================
echo.
pause
