import subprocess
try:
    # Get detailed process info using PowerShell through Python to bypass shell quoting issues
    cmd = 'powershell -Command "Get-CimInstance Win32_Process -Filter \\"name = \'python.exe\'\\" | Select-Object ProcessId, CommandLine | Format-List"'
    out = subprocess.check_output(cmd, shell=True)
    print(out.decode('cp949', errors='ignore'))
except Exception as e:
    print(f"Error checking processes: {e}")
