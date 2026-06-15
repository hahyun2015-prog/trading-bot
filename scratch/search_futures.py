import os
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

log_path = r"era\era_order_manager.log"

if not os.path.exists(log_path):
    print("Log file not found.")
    exit(1)

with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

print("=== Last 40 Futures Events ===")
futures_lines = []
for line in lines:
    line_str = line.strip()
    if "선물" in line_str or "F 2026" in line_str or "A0567" in line_str:
        futures_lines.append(line_str)

for line in futures_lines[-40:]:
    print(line)
