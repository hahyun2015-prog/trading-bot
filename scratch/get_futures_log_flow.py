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

print(f"=== Futures Log Flow for {today_str} ===")
for line in today_lines:
    l = line.strip()
    if any(x in l for x in ["선물", "0567000", "KOSPI200"]):
        if not any(y in l for y in ["진입 차단", "휩소 방지", "감시 작동", "오류", "TR 요청"]):
            print(l)
