import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open(r'c:\Antigravity\AI_T_Agent\era\era_order_manager.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'futures_target_long' in line or 'futures_target_short' in line:
        print(f"Line {i+1}: {line.strip()}")
