import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open(r'c:\Antigravity\AI_T_Agent\era\era_order_manager.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print("=== PID file references in era_order_manager.py ===")
for i, line in enumerate(lines):
    if 'era.pid' in line or 'pid' in line.lower():
        # filter out lines containing unrelated things
        if any(w in line for w in ['pid', 'process', 'lock']):
            print(f"Line {i+1}: {line.strip()}")
