import psutil
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=== Running Python Processes ===")
for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
    try:
        # Check if it's a python process
        info = proc.info
        name = info['name'] or ""
        cmdline = info['cmdline'] or []
        if 'python' in name.lower() or any('python' in arg.lower() for arg in cmdline):
            print(f"PID: {info['pid']}")
            print(f"Exe: {info['exe']}")
            print(f"CmdLine: {cmdline}")
            print("-" * 50)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
