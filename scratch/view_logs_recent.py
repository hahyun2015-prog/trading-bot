import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open(r'c:\Antigravity\AI_T_Agent\era\era_order_manager.log', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")
print("=== Last 50 lines of era_order_manager.log ===")
for line in lines[-50:]:
    print(line.strip())
