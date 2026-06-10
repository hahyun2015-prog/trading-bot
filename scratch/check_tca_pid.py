import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open(r'c:\Antigravity\AI_T_Agent\tca\tca_controller.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'pid' in line.lower() or 'tca.pid' in line.lower():
        print(f"Line {i+1}: {line.strip()}")
