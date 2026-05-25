@echo off
chcp 65001 > nul
cls
echo.
echo   ====================================================
echo     AMATS 자동 시작 시퀀스
echo   ====================================================
echo.

:: ── venv32 존재 확인 ──────────────────────────────────────────────
if not exist "%~dp0venv32\Scripts\python.exe" (
    echo   [경고] venv32 가상환경 없음 — setup_env.bat 을 먼저 실행하세요.
    echo.
    pause
    exit /b 1
)
set "PY=%~dp0venv32\Scripts\python.exe"

:: ── TCA 관제 에이전트 시작 ────────────────────────────────────────
echo   [1/2]  TCA 텔레그램 관제 에이전트 시작...
start "AMATS | TCA 관제" cmd /k ""%PY%" "%~dp0tca\tca_controller.py""
echo         └ 창 제목: AMATS ^| TCA 관제

:: 5초 대기 (TCA 초기화 여유)
timeout /t 5 /nobreak > nul

:: ── ERA 주문 엔진 시작 ────────────────────────────────────────────
echo   [2/2]  ERA 주문/리스크 엔진 시작...
start "AMATS | ERA 엔진" cmd /k ""%PY%" "%~dp0era\era_order_manager.py""
echo         └ 창 제목: AMATS ^| ERA 엔진

echo.
echo   ────────────────────────────────────────────────────
echo   AMATS 전체 시스템 가동 완료
echo     • 텔레그램에서 !상태 로 연결 확인
echo     • 이 창은 자동으로 닫힙니다.
echo   ====================================================
echo.

timeout /t 3 /nobreak > nul
exit
