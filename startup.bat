@echo off
chcp 65001 > nul
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "R=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "CYN=%ESC%[96m"
set "GRN=%ESC%[92m"
set "YLW=%ESC%[93m"
set "GRY=%ESC%[90m"
set "WHT=%ESC%[97m"

cls
echo.
echo %GRY%  ════════════════════════════════════════════════════%R%
echo   %CYN%%BOLD%AMATS%R%  %WHT%자동 시작 시퀀스%R%
echo %GRY%  ════════════════════════════════════════════════════%R%
echo.

:: ── venv32 존재 확인 ──────────────────────────────────────────────
if not exist "%~dp0venv32\Scripts\python.exe" (
    echo   %YLW%[경고]%R%  venv32 가상환경 없음 — setup_env.bat 을 먼저 실행하세요.
    echo.
    pause
    exit /b 1
)
set "PY=%~dp0venv32\Scripts\python.exe"

:: ── TCA 관제 에이전트 시작 ────────────────────────────────────────
echo   %GRN%[1/2]%R%  TCA 텔레그램 관제 에이전트 시작...
start "AMATS | TCA 관제" cmd /k ""%PY%" "%~dp0tca\tca_controller.py""
echo         %GRY%└ 창 제목: AMATS ^| TCA 관제%R%

:: 5초 대기 (TCA 초기화 여유)
timeout /t 5 /nobreak > nul

:: ── ERA 주문 엔진 시작 ────────────────────────────────────────────
echo   %GRN%[2/2]%R%  ERA 주문/리스크 엔진 시작...
start "AMATS | ERA 엔진" cmd /k ""%PY%" "%~dp0era\era_order_manager.py""
echo         %GRY%└ 창 제목: AMATS ^| ERA 엔진%R%

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %CYN%AMATS 전체 시스템 가동 완료%R%
echo   %GRY%  • 텔레그램에서 !상태 로 연결 확인%R%
echo   %GRY%  • 이 창은 자동으로 닫힙니다.%R%
echo %GRY%  ════════════════════════════════════════════════════%R%
echo.

timeout /t 3 /nobreak > nul
exit
