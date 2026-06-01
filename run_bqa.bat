@echo off
rem chcp 65001 (Disabled to prevent CMD UTF-8 parser bug)
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "R=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "MGT=%ESC%[95m"
set "CYN=%ESC%[96m"
set "GRN=%ESC%[92m"
set "YLW=%ESC%[93m"
set "RED=%ESC%[91m"
set "GRY=%ESC%[90m"
set "WHT=%ESC%[97m"

cls
echo.
echo %GRY%  ====================================================%R%
echo   %CYN%%BOLD%AMATS%R%  %MGT%%BOLD%[ BQA ]%R%  %WHT%백테스트 / 퀀트 최적화%R%
echo %GRY%  ====================================================%R%
echo   %GRY%Kiwoom 불필요  ·  64bit Python 가능%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.
echo   %WHT%실행할 모듈을 선택하세요%R%
echo.
echo   %MGT%%BOLD% 1 %R%  %WHT%K값 파라미터 최적화%R%  %GRY%batch_optimizer.py%R%
echo      %GRY%K=0.1~1.0 전범위 백테스트 → 최적 CAGR/승률 산출%R%
echo      %GRY%결과는 config\active_strategy.json 에 저장됩니다%R%
echo.
echo   %YLW%%BOLD% 2 %R%  %WHT%단일 백테스터%R%        %GRY%backtester.py%R%
echo      %GRY%볼린저밴드 + RSI 다이버전스 전략 단독 검증%R%
echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.
set /p "CHOICE=  선택 (1 / 2)  ▶  "
echo.

if exist "%~dp0venv32\Scripts\python.exe" (
    set "PYTHON=%~dp0venv32\Scripts\python.exe"
    echo   %GRN%[확인]%R%  venv32 환경으로 실행합니다.
) else (
    set "PYTHON=python"
    echo   %YLW%[알림]%R%  시스템 Python 으로 실행합니다.
)
echo.

if "%CHOICE%"=="1" (
    echo   %MGT%[실행]%R%  K값 최적화 시작...  %GRY%(ERA 실전 로직 반영, 완료까지 수 분 소요)%R%
    echo %GRY%  ────────────────────────────────────────────────────%R%
    echo.
    "%PYTHON%" "%~dp0bqa\batch_optimizer.py"
) else if "%CHOICE%"=="2" (
    echo   %YLW%[실행]%R%  단일 백테스터 시작...
    echo %GRY%  ────────────────────────────────────────────────────%R%
    echo.
    "%PYTHON%" "%~dp0bqa\backtester.py"
) else if "%CHOICE%"=="3" (
    echo   %CYN%[실행]%R%  주식 데이터 수집 + 스윙 백테스트 시작...  %GRY%(Kiwoom 불필요)%R%
    echo %GRY%  ────────────────────────────────────────────────────%R%
    echo.
    python "%~dp0bqa\collect_stock_data.py"
) else (
    echo   %RED%[오류]%R%  잘못된 입력입니다. (1, 2, 3 중 선택)
)

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %YLW%[완료]%R%  작업이 종료되었습니다.
echo   %GRY%결과 확인: 텔레그램에서 !최적화결과 입력%R%
echo.
pause
