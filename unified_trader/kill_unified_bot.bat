@echo off
echo ===================================================
echo     Stopping Unified Auto-Trader System...
echo ===================================================
echo.

echo Terminating Unified Order Manager...
powershell -c "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'unified_order_manager.py' -and $_.Name -match 'python' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq *Unified Order Manager*" /t >nul 2>&1

echo Terminating Unified Auto Loop...
powershell -c "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'unified_auto_loop.py' -and $_.Name -match 'python' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1

echo Terminating Kiwoom API Processes...
taskkill /f /im opstarter.exe /t >nul 2>&1
taskkill /f /im ncStarter.exe /t >nul 2>&1

echo Closing Console Windows...
taskkill /f /fi "WINDOWTITLE eq *Unified Auto-Trader System*" /t >nul 2>&1
powershell -c "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_unified_bot.bat' -and $_.Name -match 'cmd' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1

echo.
echo All trading bot processes have been stopped successfully.
ping 127.0.0.1 -n 4 >nul
