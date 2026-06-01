@echo off
rem chcp 65001 (Disabled to prevent CMD UTF-8 parser bug)
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "R=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "YLW=%ESC%[93m"
set "CYN=%ESC%[96m"
set "GRN=%ESC%[92m"
set "RED=%ESC%[91m"
set "GRY=%ESC%[90m"
set "WHT=%ESC%[97m"
set "BLU=%ESC%[94m"

cls
echo.
echo %GRY%  ====================================================%R%
echo   %CYN%%BOLD%AMATS%R%  %YLW%%BOLD%[ SETUP ]%R%  %WHT%트레이딩 환경 초기 설정%R%
echo %GRY%  ====================================================%R%
echo   %GRY%AI_T_Agent\venv32  가상환경 생성 및 패키지 설치%R%
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.

if exist "%~dp0venv32\Scripts\python.exe" (
    echo   %YLW%[알림]%R%  venv32 가 이미 존재합니다.
    echo   %GRY%         패키지 업데이트만 진행합니다.%R%
    echo.
    goto :install_packages
)

echo   %WHT%[필수] 32비트 Python 3.x 실행파일 경로를 입력하세요%R%
echo.
echo   %GRY%  예시  C:\Python38-32\python.exe%R%
echo   %GRY%        C:\Users\사용자\AppData\Local\Programs\Python\Python38-32\python.exe%R%
echo.
set /p "PY32_PATH=  경로 입력  ▶  "
echo.

if not exist "%PY32_PATH%" (
    echo   %RED%[오류]%R%  경로에 Python 실행파일이 없습니다.
    echo   %GRY%         %PY32_PATH%%R%
    echo.
    pause
    exit /b 1
)

:: 32비트 여부 확인
"%PY32_PATH%" -c "import struct; assert struct.calcsize('P')==4" 2>nul
if errorlevel 1 (
    echo   %RED%[오류]%R%  입력한 Python 이 %RED%%BOLD%64비트%R% 입니다.
    echo   %YLW%[조치]%R%  Kiwoom OpenAPI 는 %WHT%%BOLD%32비트 Python%R% 이 필수입니다.
    echo   %GRY%         python.org → Windows installer (32-bit) 를 설치하세요.%R%
    echo.
    pause
    exit /b 1
)
echo   %GRN%[확인]%R%  32비트 Python 감지됨

echo.
echo   %YLW%[1/3]%R%  venv32 가상환경 생성 중...
"%PY32_PATH%" -m venv "%~dp0venv32"
if errorlevel 1 (
    echo   %RED%[오류]%R%  가상환경 생성 실패. Python 설치 상태를 확인하세요.
    echo.
    pause
    exit /b 1
)
echo   %GRN%      V  완료%R%

:install_packages
echo.
echo   %YLW%[2/3]%R%  필수 패키지 설치 중...
echo   %GRY%         PyQt5  requests  beautifulsoup4  pandas  numpy%R%
"%~dp0venv32\Scripts\pip" install --upgrade pip --quiet
"%~dp0venv32\Scripts\pip" install PyQt5 requests beautifulsoup4 pandas numpy --quiet
if errorlevel 1 (
    echo   %RED%[오류]%R%  패키지 설치 실패. 인터넷 연결을 확인하세요.
    echo.
    pause
    exit /b 1
)
echo   %GRN%      V  완료%R%

echo.
echo   %YLW%[3/3]%R%  config.json 확인...
if exist "%~dp0config\config.json" (
    echo   %GRN%      V  config\config.json 감지됨%R%
) else (
    echo   %RED%      X  config\config.json 없음%R%
    echo   %YLW%         설정 파일을 직접 생성해야 합니다.%R%
)

echo.
echo %GRY%  ====================================================%R%
echo   %GRN%%BOLD%설치 완료!%R%  이제 다음 순서로 실행하세요.
echo %GRY%  ────────────────────────────────────────────────────%R%
echo.
echo   %BLU%%BOLD%Step 1%R%  %WHT%config\config.json%R%  설정 확인
echo          %GRY%environment, telegram, gemini_api_key 항목%R%
echo.
echo   %BLU%%BOLD%Step 2%R%  %WHT%run_tca.bat%R%  실행  %GRY%(텔레그램 관제 시작)%R%
echo.
echo   %BLU%%BOLD%Step 3%R%  텔레그램에서  %CYN%!시스템시작%R%  입력
echo          %GRY%(ERA 주문엔진 자동 구동)%R%
echo.
echo %GRY%  ====================================================%R%
echo.
pause
