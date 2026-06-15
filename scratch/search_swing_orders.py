import os
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

log_path = r"era\era_order_manager.log"
today_str = "2026-06-12"

if not os.path.exists(log_path):
    print("Log file not found.")
    exit(1)

with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

start_idx = -1
for i, line in enumerate(lines):
    if "daily_balance_history" in line and today_str in line:
        start_idx = i
        break

if start_idx == -1:
    start_idx = max(0, len(lines) - 2000)

today_lines = lines[start_idx:]

codes = ["241790", "040160"]
print("=== Search for SWING Orders ===")
for line in today_lines:
    l = line.strip()
    if any(c in l for c in codes):
        if any(keyword in l for keyword in ["주문", "체결", "매도", "매수", "발동", "익절", "손절"]):
            print(l)
