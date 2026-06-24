import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

log_path = r"era\era_order_manager.log"
today_str = "2026-06-24"

if not os.path.exists(log_path):
    print("Log not found")
    exit()

try:
    with open(log_path, "r", encoding="cp949") as f:
        lines = f.readlines()
except Exception:
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

start_idx = -1
for i, line in enumerate(lines):
    if "daily_balance_history" in line and today_str in line:
        start_idx = i
        break
if start_idx == -1:
    start_idx = max(0, len(lines) - 4000)

today_lines = lines[start_idx:]

print("=== SEARCHING FOR BLOCKING / LIMIT / HALTING MESSAGES (UTF-8) ===")
for idx, line in enumerate(today_lines):
    line_str = line.strip()
    if any(k in line_str for k in ["차단", "초과", "정지", "제한", "한도", "Halt", "halt", "limit", "max", "횟수"]):
        print(f"[{idx}] {line_str}")
