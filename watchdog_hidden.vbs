' AMATS watchdog launcher - runs watchdog_check.bat with no console window.
' Task Scheduler ran "cmd.exe /c watchdog_check.bat" directly with
' LogonType=Interactive, so a console flashed on the desktop every 2 minutes.
' Running it through WScript.Shell with window style 0 keeps the exact same
' user, session and elevation - only the window is suppressed.
' Deliberately ASCII-only: .vbs is read as ANSI and non-ASCII text can corrupt it.
Option Explicit
Dim shell, here, cmd
Set shell = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
cmd = "cmd.exe /c """ & here & "watchdog_check.bat"""
' 0 = hidden window, False = do not wait for completion
shell.Run cmd, 0, False
