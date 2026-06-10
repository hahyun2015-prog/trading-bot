import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

log_path = r'c:\Antigravity\AI_T_Agent\era\era_order_manager.log'
keywords = ['선물', '손절', '익절', '청산', 'futures', 'exit', 'stop', 'loss']

print("Reading log...")
with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print(f"Total lines in log: {len(lines)}")
print("Filtering last 1000 lines for keywords...")

# Look at last 1000 lines
last_lines = lines[-1000:]
matches = 0
for i, line in enumerate(last_lines):
    line_num = len(lines) - 1000 + i + 1
    if any(k in line.lower() for k in keywords):
        print(f"Line {line_num}: {line.strip()}")
        matches += 1

print(f"Found {matches} matches.")
