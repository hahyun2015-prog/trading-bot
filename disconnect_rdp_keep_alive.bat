@echo off
:: AMATS RDP Keep-Alive Disconnector
:: This script disconnects your Remote Desktop session while forcing Windows 
:: to keep the GUI session unlocked and active on the local console.
:: WARNING: You MUST run this batch file as Administrator!

echo ===================================================
echo   AMATS RDP Safe Disconnector (Keep GUI Active)
echo ===================================================
echo.

:: Check for Administrator privileges
openfiles >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please run this batch file as Administrator!
    echo         Right-click -> 'Run as administrator'
    echo.
    pause
    exit /b 1
)

echo [1/2] Disconnecting RDP session while keeping local GUI console active...
%windir%\System32\tscon.exe %sessionname% /dest:console

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Direct tscon sessionname failed. Attempting Session ID query...
    for /f "tokens=3-4" %%a in ('query session %username%') do (
        if "%%b"=="Active" (
            echo Found Active Session ID: %%a. Redirecting...
            %windir%\System32\tscon.exe %%a /dest:console
        )
    )
)

echo.
echo [2/2] RDP successfully disconnected. GUI console session remains active!
echo       You can safely close this window now if it didn't close automatically.
echo.
timeout /t 5
