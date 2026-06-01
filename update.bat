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
echo   %CYN%%BOLD%AMATS%R%  %MGT%%BOLD%[ UPDATE ]%R%  %WHT%코드 동기화 및 업데이트%R%
echo %GRY%  ====================================================%R%
echo   %GRY%GitHub origin/main → 이 PC 자동 적용%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.

:: ── 1. git 설치 확인 ───────────────────────────────────────────────
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo   %RED%[오류]%R%  git 이 설치되어 있지 않습니다.
    echo   %YLW%[조치]%R%  https://git-scm.com 에서 Git for Windows 를 설치하세요.
    echo.
    pause
    exit /b 1
)

:: ── 2. 실행 중인 ERA 프로세스 감지 ────────────────────────────────
set "ERA_WAS_RUNNING=0"
if exist "%~dp0era\era.pid" (
    set /p ERA_PID=<"%~dp0era\era.pid"
    tasklist /FI "PID eq %ERA_PID%" 2>nul | find /i "python.exe" >nul
    if not errorlevel 1 (
        set "ERA_WAS_RUNNING=1"
        echo   %YLW%[감지]%R%  ERA 엔진 실행 중 (PID: %ERA_PID%) - 업데이트 전 자동 정지
        taskkill /f /pid %ERA_PID% >nul 2>&1
        del /f /q "%~dp0era\era.pid" >nul 2>&1
        timeout /t 2 /nobreak >nul
    )
)

:: ── 3. git pull ────────────────────────────────────────────────────
echo   %CYN%[업데이트]%R%  GitHub origin/main 에서 최신 코드를 가져옵니다...
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.

git -C "%~dp0" pull origin main

if %errorlevel% neq 0 (
    echo.
    echo   %RED%[오류]%R%  git pull 실패. 네트워크 또는 인증 상태를 확인하세요.
    echo   %GRY%          충돌이 있다면: git stash → git pull → git stash pop%R%
    echo.
    pause
    exit /b 1
)

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %GRN%[완료]%R%  코드 업데이트 성공
echo.

:: ── 4. 현재 커밋 정보 출력 ─────────────────────────────────────────
echo   %WHT%최신 버전:%R%
git -C "%~dp0" log --oneline -3
echo.

:: ── 5. ERA 재시작 여부 ────────────────────────────────────────────
if "%ERA_WAS_RUNNING%"=="1" (
    echo %GRY%  ────────────────────────────────────────────────────%R%
    echo   %YLW%[알림]%R%  ERA 가 업데이트 전에 실행 중이었습니다.
    set /p "RESTART=  ERA 를 다시 시작하시겠습니까? (Y/N)  ▶  "
    if /i "%RESTART%"=="Y" (
        echo   %GRN%[재시작]%R%  ERA 엔진을 재구동합니다...
        start "" "%~dp0run_era.bat"
    )
) else (
    echo   %GRY%ERA 는 실행 중이 아니었습니다. 필요 시 run_era.bat 으로 수동 시작하세요.%R%
)

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %CYN%[팁]%R%  다른 PC 에서도 이 스크립트를 실행하면 동일한 버전으로 맞춰집니다.
echo   %CYN%[팁]%R%  텔레그램에서 %WHT%!코드업데이트%R% 명령으로 원격 실행도 가능합니다.
echo.
pause
