@echo off
cd /d "%~dp0"

title AMATS 야간선물 데이터 수집기 (실서버 LIVE, 주문기능 없음)
echo ==========================================================
echo   야간선물 데이터 수집기 - 임시 도구
echo   실서버(LIVE) 접속 / 주문 기능 전혀 없음 (시세 수집 전용)
echo ==========================================================
echo.

if not exist "venv32\Scripts\python.exe" (
    echo [ERROR] venv32 environment not found!
    pause
    exit /b 1
)

echo [OK] Starting collector...
echo      To terminate, press Ctrl+C.
echo ----------------------------------------------------------
echo.

"venv32\Scripts\python.exe" "era\night_futures_data_collector.py"

echo.
echo [OK] Collector has terminated.
pause
