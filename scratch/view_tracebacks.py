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

print("=== Search for Tracebacks in era_order_manager.log ===")
for i, line in enumerate(lines):
    if "Traceback" in line:
        print(f"\n--- Traceback found at line {i+1} ---")
        for j in range(max(0, i-2), min(len(lines), i+15)):
            marker = ">>>" if j == i else "   "
            print(f"{marker} {lines[j].strip()}")
