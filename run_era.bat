@echo off
chcp 65001 > nul
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "R=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "RED=%ESC%[91m"
set "GRN=%ESC%[92m"
set "YLW=%ESC%[93m"
set "CYN=%ESC%[96m"
set "GRY=%ESC%[90m"
set "WHT=%ESC%[97m"

cls
echo.
echo %GRY%  ════════════════════════════════════════════════════%R%
echo   %CYN%%BOLD%AMATS%R%  %RED%%BOLD%[ ERA ]%R%  %WHT%주문 / 리스크 관리 엔진%R%
echo %GRY%  ════════════════════════════════════════════════════%R%
echo   %GRY%Kiwoom OpenAPI  ·  32bit Python  ·  실시간 자동매매%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.

if not exist "%~dp0venv32\Scripts\python.exe" (
    echo   %RED%[오류]%R%  venv32 가상환경을 찾을 수 없습니다.
    echo   %YLW%[조치]%R%  %WHT%setup_env.bat%R% 을 먼저 실행하세요.
    echo.
    pause
    exit /b 1
)

echo   %GRN%[확인]%R%  venv32 가상환경 감지됨
echo.

:: ── 코드 자동 업데이트 (git pull) ───────────────────────────────
where git >nul 2>&1
if %errorlevel% equ 0 (
    echo   %CYN%[동기화]%R%  GitHub 최신 코드 확인 중...
    git -C "%~dp0" pull origin main --quiet 2>&1
    if %errorlevel% equ 0 (
        echo   %GRN%[동기화]%R%  코드 최신 상태 확인 완료
        for /f %%v in ('git -C "%~dp0" log --oneline -1') do echo   %GRY%         버전: %%v%R%
    ) else (
        echo   %YLW%[동기화]%R%  git pull 실패 — 현재 버전으로 계속 실행합니다.
    )
) else (
    echo   %GRY%[동기화]%R%  git 미설치 — 코드 동기화 건너뜀
)
echo.

echo   %YLW%[시작]%R%  ERA 엔진 구동 중...
echo   %GRY%         종료: Ctrl+C 또는 창 닫기%R%
echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.

"%~dp0venv32\Scripts\python.exe" "%~dp0era\era_order_manager.py"

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %YLW%[종료]%R%  ERA 엔진이 중지되었습니다.
echo.
pause
