import os
import re

log_path = r"era\era_order_manager.log"
today_str = "2026-06-24"

if not os.path.exists(log_path):
    print("Log not found")
    exit()

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

# Let's find all [주식 실체결 확정] and [주간선물 실체결 확정]
stock_trades = []
futures_trades = []

stock_pat = re.compile(r"\[주식 실체결 확정\]\s+(.*?)\((\d+)\)\s*\|\s*([\d,]+)원\s*\|\s*(\d+)주\s*\|\s*([+-])(매수|매도)")
fut_pat = re.compile(r"\[주간선물 실체결 확정\]\s+미니 F 202607\(0567000\)\s*\|\s*([\d\.]+)\s*\|\s*(\d+)계약\s*\|\s*([+-])(매수|매도)")

stock_orders = []
fut_orders = []
stock_order_pat = re.compile(r"\[주식 주문\]\s+(.*?)\((\d+)\)\s*\|\s*([\d,]+)원\s*\|\s*(\d+)주\s*\|\s*(매수|매도)")
fut_order_pat = re.compile(r"\[주간선물 주문\]\s+(LONG|SHORT)\s+(진입|청산)\s*\|\s*([\d\.]+)pt\s*\|\s*(\d+)계약\s*\|\s*(A0567000)")

for line in today_lines:
    s_match = stock_pat.search(line)
    if s_match:
        stock_trades.append(s_match.groups())
    f_match = fut_pat.search(line)
    if f_match:
        futures_trades.append(f_match.groups())
        
    so_match = stock_order_pat.search(line)
    if so_match:
        stock_orders.append(so_match.groups())
    fo_match = fut_order_pat.search(line)
    if fo_match:
        fut_orders.append(fo_match.groups())

print(f"Parsed {len(stock_trades)} stock trade logs, {len(futures_trades)} futures trade logs.")
print(f"Parsed {len(stock_orders)} stock order logs, {len(fut_orders)} futures order logs.")

# Let's print the orders vs executions to see slippage
print("\n--- Futures Orders vs Executions ---")
# Let's match order logs with execution logs by time or sequence
for o in fut_orders:
    print(f"Order: {o[0]} {o[1]} | target price: {o[2]} pt | qty: {o[3]}")
for e in futures_trades:
    print(f"Exec: price: {e[0]} pt | qty: {e[1]} | action: {e[3]}")
