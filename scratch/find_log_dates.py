import os

log_path = r"era\era_order_manager.log"
today_str = "2026-06-12"

with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

print("=== Log lines for 383310 ===")
for i, line in enumerate(lines):
    if "383310" in line:
        print(f"Line {i:5d}: {line.strip()}")

print("\n=== Today's marker lines ===")
for i, line in enumerate(lines):
    if "daily_balance_history" in line and today_str in line:
        print(f"Line {i:5d}: {line.strip()}")
