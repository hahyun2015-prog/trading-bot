@echo off
chcp 65001 > nul
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "R=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "GRN=%ESC%[92m"
set "CYN=%ESC%[96m"
set "YLW=%ESC%[93m"
set "RED=%ESC%[91m"
set "MGT=%ESC%[95m"
set "GRY=%ESC%[90m"
set "WHT=%ESC%[97m"

cls
echo.
echo %GRY%  ════════════════════════════════════════════════════%R%
echo   %CYN%%BOLD%AMATS%R%  %GRN%%BOLD%[ STA ]%R%  %WHT%스크리닝 / 테마 분석%R%
echo %GRY%  ════════════════════════════════════════════════════%R%
echo   %GRY%Kiwoom 연동  ·  32bit Python 필요%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.
echo   %WHT%실행할 모듈을 선택하세요%R%
echo.
echo   %GRN%%BOLD% 1 %R%  %WHT%테마 대장주 추적기%R%  %GRY%theme_tracker.py%R%
echo      %GRY%네이버 테마 크롤링 + 외인/기관 수급 필터%R%
echo.
echo   %YLW%%BOLD% 2 %R%  %WHT%스윙 스크리너%R%       %GRY%swing_screener.py%R%
echo      %GRY%60일 신고가 돌파 + 거래량 폭발 스윙 종목 발굴%R%
echo.
echo   %MGT%%BOLD% 3 %R%  %WHT%분봉 데이터 수집%R%     %GRY%screener.py%R%
echo      %GRY%top_volume_theme 종목의 3분봉 일괄 수집%R%
echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.
set /p "CHOICE=  선택 (1 / 2 / 3)  ▶  "
echo.

if not exist "%~dp0venv32\Scripts\python.exe" (
    echo   %RED%[오류]%R%  venv32 가상환경이 없습니다.
    echo   %YLW%[조치]%R%  setup_env.bat 을 먼저 실행하세요.
    echo.
    pause
    exit /b 1
)

if "%CHOICE%"=="1" (
    echo   %GRN%[실행]%R%  테마 대장주 추적기 시작...
    echo %GRY%  ────────────────────────────────────────────────────%R%
    echo.
    "%~dp0venv32\Scripts\python.exe" "%~dp0sta\theme_tracker.py"
) else if "%CHOICE%"=="2" (
    echo   %YLW%[실행]%R%  스윙 스크리너 시작...
    echo %GRY%  ────────────────────────────────────────────────────%R%
    echo.
    "%~dp0venv32\Scripts\python.exe" "%~dp0sta\swing_screener.py"
) else if "%CHOICE%"=="3" (
    echo   %MGT%[실행]%R%  분봉 데이터 수집 시작...
    echo %GRY%  ────────────────────────────────────────────────────%R%
    echo.
    "%~dp0venv32\Scripts\python.exe" "%~dp0sta\screener.py"
) else (
    echo   %RED%[오류]%R%  잘못된 입력입니다. (1, 2, 3 중 선택)
)

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %YLW%[완료]%R%  작업이 종료되었습니다.
echo.
pause
