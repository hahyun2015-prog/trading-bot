import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open(r'c:\Antigravity\AI_T_Agent\era\era_order_manager.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

in_func = False
count = 0
for i, line in enumerate(lines):
    if 'def check_connection_status' in line:
        in_func = True
    if in_func:
        print(f"{i+1}: {line.strip()}")
        count += 1
        if count >= 45:
            break
