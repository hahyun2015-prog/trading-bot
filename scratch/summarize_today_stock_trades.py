import os
import io
import sys
import re

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
    for i, line in enumerate(lines):
        if today_str in line:
            start_idx = i
            break

if start_idx == -1:
    start_idx = max(0, len(lines) - 2000)

today_lines = lines[start_idx:]

trade_pattern = re.compile(r"\[주식 실체결 확정\]\s+(.*?)\((\d+)\)\s*\|\s*([\d,]+)원\s*\|\s*(\d+)주\s*\|\s*([+-])(매수|매도)")

trades = {}

for line in today_lines:
    line_str = line.strip()
    match = trade_pattern.search(line_str)
    if match:
        name, code, price_str, qty_str, sign, action = match.groups()
        price = int(price_str.replace(",", ""))
        qty = int(qty_str)
        
        if code not in trades:
            trades[code] = {"name": name, "buys": [], "sells": []}
            
        if action == "매수":
            trades[code]["buys"].append((price, qty))
        else:
            trades[code]["sells"].append((price, qty))

print("=========================================")
# Print stock summary table
print(f"{'종목명(코드)':<20} | {'총 매수수량':<8} | {'평균매수가':<10} | {'총 매도수량':<8} | {'평균매도가':<10} | {'현재보유':<6} | {'실현손익':<12}")
print("-" * 90)

total_realized_pnl = 0

for code, info in trades.items():
    name_code = f"{info['name']}({code})"
    
    # Calculate Buy average
    total_buy_qty = sum(qty for _, qty in info['buys'])
    total_buy_val = sum(p * qty for p, qty in info['buys'])
    avg_buy_price = total_buy_val / total_buy_qty if total_buy_qty > 0 else 0
    
    # Calculate Sell average
    total_sell_qty = sum(qty for _, qty in info['sells'])
    total_sell_val = sum(p * qty for p, qty in info['sells'])
    avg_sell_price = total_sell_val / total_sell_qty if total_sell_qty > 0 else 0
    
    current_qty = total_buy_qty - total_sell_qty
    
    # Realized PnL is based on matching sold quantity with buy price
    # We can approximate realized profit as: (avg_sell - avg_buy) * sold_qty if we assume FIFO or simple average
    # Let's check how many were sold
    realized_qty = min(total_buy_qty, total_sell_qty)
    if realized_qty > 0:
        realized_pnl = (avg_sell_price - avg_buy_price) * realized_qty
    else:
        realized_pnl = 0
    
    total_realized_pnl += realized_pnl
    
    print(f"{name_code:<20} | {total_buy_qty:<12} | {avg_buy_price:<12.1f} | {total_sell_qty:<12} | {avg_sell_price:<12.1f} | {current_qty:<8} | {realized_pnl:+=12,.0f}원")

print("-" * 90)
print(f"오늘 총 실현손익 (주식 합계): {total_realized_pnl:+,.0f}원")
