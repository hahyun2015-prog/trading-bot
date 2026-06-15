$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("C:\Users\Daon\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\amats_startup.lnk")
$Shortcut.TargetPath = "c:\Antigravity\AI_T_Agent\startup.bat"
$Shortcut.WorkingDirectory = "c:\Antigravity\AI_T_Agent"
$Shortcut.Save()
Write-Host "Startup shortcut created successfully."
