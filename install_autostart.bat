@echo off
rem chcp 65001 (Disabled to prevent CMD UTF-8 parser bug)
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "R=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "CYN=%ESC%[96m"
set "GRN=%ESC%[92m"
set "YLW=%ESC%[93m"
set "RED=%ESC%[91m"
set "BLU=%ESC%[94m"
set "GRY=%ESC%[90m"
set "WHT=%ESC%[97m"

cls
echo.
echo %GRY%  ====================================================%R%
echo   %CYN%%BOLD%AMATS%R%  %YLW%%BOLD%[ 자동시작 설치 ]%R%  %WHT%Windows 로그온 시 자동 실행%R%
echo %GRY%  ====================================================%R%
echo   %GRY%이 스크립트는 한 번만 실행하면 됩니다.%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.

:: ── 관리자 권한 확인 ─────────────────────────────────────────────
net session > nul 2>&1
if errorlevel 1 (
    echo   %RED%[오류]%R%  관리자 권한이 필요합니다.
    echo   %YLW%[조치]%R%  이 파일을 %WHT%우클릭 → 관리자 권한으로 실행%R% 하세요.
    echo.
    pause
    exit /b 1
)
echo   %GRN%[확인]%R%  관리자 권한 확인됨

:: ── venv32 존재 확인 ─────────────────────────────────────────────
if not exist "%~dp0venv32\Scripts\python.exe" (
    echo   %RED%[오류]%R%  venv32 가상환경이 없습니다.
    echo   %YLW%[조치]%R%  setup_env.bat 을 먼저 실행하세요.
    echo.
    pause
    exit /b 1
)
echo   %GRN%[확인]%R%  venv32 가상환경 감지됨

echo.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo   %WHT%[설치 항목]%R%
echo.

:: ── 1단계: 작업 스케줄러에 AMATS 자동시작 등록 ───────────────────
echo   %BLU%[1/3]%R%  Windows 작업 스케줄러 등록 중...
set "TASK_NAME=AMATS AutoStart"
set "STARTUP_BAT=%~dp0startup.bat"

:: 기존 태스크 삭제 후 재등록
schtasks /delete /tn "%TASK_NAME%" /f > nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "cmd.exe /c \"%STARTUP_BAT%\"" /sc onlogon /rl highest /f > nul 2>&1

if errorlevel 1 (
    echo         %RED%X  작업 스케줄러 등록 실패%R%
    echo         %GRY%수동으로 startup.bat 을 시작프로그램 폴더에 복사하세요.%R%
) else (
    echo         %GRN%V  로그온 시 자동 실행 등록 완료%R%
    echo         %GRY%   작업 이름: %TASK_NAME%%R%
)

:: ── 2단계: 영웅문4 자동시작 등록 (경로 자동탐색) ─────────────────
echo.
echo   %BLU%[2/3]%R%  영웅문4 자동시작 탐색 중...
set "HERO_EXE="
for %%P in (
    "C:\KiwoomSecurities\영웅문4\HeroWin.exe"
    "C:\Program Files (x86)\Kiwoom\영웅문4\HeroWin.exe"
    "C:\영웅문4\HeroWin.exe"
) do (
    if exist %%P set "HERO_EXE=%%~P"
)

if defined HERO_EXE (
    schtasks /create /tn "AMATS 영웅문4 AutoStart" /tr "\"%HERO_EXE%\"" /sc onlogon /rl highest /f > nul 2>&1
    if errorlevel 1 (
        echo         %YLW%△  영웅문4 자동시작 등록 실패 (수동 추가 필요)%R%
    ) else (
        echo         %GRN%V  영웅문4 자동시작 등록 완료%R%
        echo         %GRY%   경로: %HERO_EXE%%R%
    )
) else (
    echo         %YLW%△  영웅문4 설치 경로를 찾지 못했습니다.%R%
    echo         %GRY%   수동으로 시작프로그램에 영웅문4 추가 필요:%R%
    echo         %GRY%   Windows 키+R → shell:startup → 영웅문4 바로가기 붙여넣기%R%
)

:: ── 3단계: 영웅문4 자동로그인 안내 ──────────────────────────────
echo.
echo   %BLU%[3/3]%R%  영웅문4 자동로그인 설정 안내
echo         %GRY%영웅문4 실행 → 로그인 화면 → %WHT%"자동로그인" 체크%R%%GRY% 후 로그인%R%
echo         %YLW%△  이 단계는 직접 설정하셔야 합니다.%R%

:: ── 설치 완료 요약 ────────────────────────────────────────────────
echo.
echo %GRY%  ====================================================%R%
echo   %GRN%%BOLD%설치 완료!%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.
echo   이제 컴퓨터를 켜면 자동으로 실행됩니다.
echo.
echo   %BLU%%BOLD%확인 사항%R%
echo   %WHT%1.%R%  영웅문4 자동로그인 체크  %GRY%(직접 설정 필요)%R%
echo   %WHT%2.%R%  재부팅 후 텔레그램에서 %CYN%!상태%R% 로 확인
echo.
echo   %GRY%자동시작 해제: install_autostart.bat 를 다시 실행하거나%R%
echo   %GRY%작업 스케줄러에서 'AMATS AutoStart' 삭제%R%
echo.
echo %GRY%  ====================================================%R%
echo.
pause
