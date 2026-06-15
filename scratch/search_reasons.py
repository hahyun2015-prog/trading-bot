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

print("=== Search for Sell Trigger Reasons ===")
for i in range(start_idx, len(lines)):
    line_str = lines[i].strip()
    if ("255440" in line_str) and ("-매도" in line_str or "주문" in line_str or "청산" in line_str or "매도주문" in line_str):
        print(f"\n--- Context around line {i} ---")
        for j in range(max(start_idx, i-5), min(len(lines), i+3)):
            marker = ">>>" if j == i else "   "
            print(f"{marker} {lines[j].strip()}")
