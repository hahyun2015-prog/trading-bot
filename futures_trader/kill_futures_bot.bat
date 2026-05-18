@echo off
echo ===================================================
echo     Stopping Futures Auto-Trader System...
echo ===================================================
echo.

echo Terminating Futures Order Manager...
powershell -c "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'futures_order_manager.py' -and $_.Name -match 'python' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq *Futures Order Manager*" /t >nul 2>&1

echo Terminating Futures Auto Loop...
powershell -c "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'futures_auto_loop.py' -and $_.Name -match 'python' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1

echo Terminating Other Python Processes (Data Collector, Telegram)...
powershell -c "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'data_collector.py' -and $_.Name -match 'python' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1

echo Terminating Kiwoom API Processes...
taskkill /f /im opstarter.exe /t >nul 2>&1
taskkill /f /im ncStarter.exe /t >nul 2>&1

echo Closing Console Windows...
taskkill /f /fi "WINDOWTITLE eq *Futures Auto-Trader System*" /t >nul 2>&1
powershell -c "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_futures_bot.bat' -and $_.Name -match 'cmd' } | Invoke-CimMethod -MethodName Terminate" >nul 2>&1

echo.
echo All futures trading bot processes have been stopped successfully.
ping 127.0.0.1 -n 4 >nul
