import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open(r'c:\Antigravity\AI_T_Agent\install_autostart.bat', 'r', encoding='utf-8', errors='ignore') as f:
    print(f.read())
