import os
import io
import sys

# Force output to use UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

log_path = r"era\era_order_manager.log"
today_str = "2026-06-12"

if not os.path.exists(log_path):
    print("Log file not found.")
    exit(1)

with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

# Find the start of today's logs
start_idx = -1
for i, line in enumerate(lines):
    if "daily_balance_history" in line and today_str in line:
        start_idx = i
        break

if start_idx == -1:
    for i, line in enumerate(lines):
        if today_str in line:
            start_idx = i
            break

if start_idx == -1:
    start_idx = max(0, len(lines) - 2000)

today_lines = lines[start_idx:]

print(f"Today's logs start at line {start_idx} (out of {len(lines)}). Found {len(today_lines)} lines.\n")

stock_trades = []
futures_trades = []
other_events = []

for line in today_lines:
    line_str = line.strip()
    if not line_str:
        continue
        
    # Check if this line is a trade confirmation, order execution, trigger, or status message
    is_stock = False
    is_futures = False
    
    # Classify based on keywords
    if "주식" in line_str or any(c in line_str for c in ["241790", "255440", "383310", "040160", "042700", "000430"]):
        # But filter out massive tick-by-tick logs if they are too noisy
        if "진입 차단" in line_str or "감시 작동" in line_str:
            continue
        is_stock = True
        
    if "선물" in line_str or "F 202607" in line_str or "A0567000" in line_str or "0567000" in line_str:
        if "진입 차단" in line_str or "감시 작동" in line_str or "휩소 방지" in line_str:
            continue
        is_futures = True

    if is_stock:
        stock_trades.append(line_str)
    elif is_futures:
        futures_trades.append(line_str)
    else:
        if any(x in line_str for x in ["daily_balance_history", "복원", "계좌"]):
            other_events.append(line_str)

print("=== [STOCK TRADING EVENTS] ===")
for t in stock_trades:
    print(t)

print("\n=== [FUTURES TRADING EVENTS] ===")
for t in futures_trades:
    print(t)

print("\n=== [OTHER INTERESTING EVENTS] ===")
for e in other_events:
    print(e)
