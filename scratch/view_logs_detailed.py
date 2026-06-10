import io
import sys
import subprocess

# Set stdout to utf-8 to avoid encoding errors
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    output = subprocess.check_output('wmic process where "name=\'python.exe\'" get ProcessId,CommandLine', shell=True, text=True, errors='ignore')
    print("=== Running Python Processes ===")
    print(output)
except Exception as e:
    print(f"Error running wmic: {e}")



