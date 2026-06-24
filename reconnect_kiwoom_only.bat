@echo off
title AMATS Kiwoom Only Reconnect
echo Requesting Kiwoom OpenAPI Reconnection (without restarting ERA engine)...
echo. > "%~dp0reconnect_kiwoom.flag"
echo Reconnection flag file created successfully.
timeout /t 2 >nul
exit
