@echo off
chcp 65001 > nul
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "R=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "BLU=%ESC%[94m"
set "CYN=%ESC%[96m"
set "GRN=%ESC%[92m"
set "YLW=%ESC%[93m"
set "RED=%ESC%[91m"
set "GRY=%ESC%[90m"
set "WHT=%ESC%[97m"

cls
echo.
echo %GRY%  ════════════════════════════════════════════════════%R%
echo   %CYN%%BOLD%AMATS%R%  %BLU%%BOLD%[ TCA ]%R%  %WHT%텔레그램 중앙 관제 에이전트%R%
echo %GRY%  ════════════════════════════════════════════════════%R%
echo   %GRY%Telegram Bot  ·  원격 명령  ·  시스템 모니터링%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.
echo   %GRY%사용 가능한 텔레그램 명령어:%R%
echo   %BLU%!상태%R%  %GRY%·%R%  %BLU%!주식현황%R%  %GRY%·%R%  %BLU%!선물현황%R%  %GRY%·%R%  %BLU%!도움말%R%
echo   %BLU%!시스템시작%R%  %GRY%·%R%  %BLU%!시스템종료%R%  %GRY%·%R%  %BLU%!긴급정지%R%
echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
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

if exist "%~dp0venv32\Scripts\python.exe" (
    echo   %GRN%[확인]%R%  venv32 환경으로 실행합니다.
    echo   %YLW%[시작]%R%  TCA 관제 에이전트 구동 중...
    echo.
    "%~dp0venv32\Scripts\python.exe" "%~dp0tca\tca_controller.py"
) else (
    echo   %YLW%[알림]%R%  venv32 없음 — 시스템 Python 으로 실행합니다.
    echo   %YLW%[시작]%R%  TCA 관제 에이전트 구동 중...
    echo.
    python "%~dp0tca\tca_controller.py"
)

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %YLW%[종료]%R%  TCA 에이전트가 중지되었습니다.
echo.
pause
