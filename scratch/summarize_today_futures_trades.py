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

print(f"Analyzing {len(today_lines)} log lines for futures trades...\n")

# Trace trades
# When we see "+매수" or "-매도" for "미니 F 202607(0567000)" or "A0567000"
# Let's keep a history of executions:
# (time_approx, price, qty, action)
executions = []
exec_pattern = re.compile(r"\[주간선물 실체결 확정\]\s+미니 F 202607\(0567000\)\s*\|\s*([\d\.]+)\s*\|\s*(\d+)계약\s*\|\s*([+-])(매수|매도)")
pnl_pattern = re.compile(r"\[주간선물\]\s+(LONG|SHORT)\s+(손절|트레일링 스탑|익절)\s+발동!\s+진입:([\d\.]+)\s+현재:([\d\.]+)\s+손실:([\-\d\.]+)pt")
pnl_pattern_alt = re.compile(r"\[주간선물\]\s+(LONG|SHORT)\s+(손절|트레일링 스탑|익절)\s+발동!\s+최고가:([\d\.]+)\s+현재가\(청산\):([\d\.]+)\s+익절:([\+\-\d\.]+)pt")
pnl_pattern_alt2 = re.compile(r"\[주간선물\]\s+(LONG|SHORT)\s+(손절|트레일링 스탑|익절)\s+발동!\s+최저가:([\d\.]+)\s+현재가\(청산\):([\d\.]+)\s+익절:([\+\-\d\.]+)pt")

trade_list = []
active_position = None # {type, entry_price, qty}

POINT_VALUE = 250000

for line in today_lines:
    l = line.strip()
    match = exec_pattern.search(l)
    if match:
        price = float(match.group(1))
        qty = int(match.group(2))
        action = match.group(4) # '매수' or '매도'
        
        # Determine if this opens or closes/reduces a position
        if active_position is None:
            # Open position
            pos_type = "LONG" if action == "매수" else "SHORT"
            active_position = {"type": pos_type, "entry_price": price, "qty": qty}
            print(f"Position OPENED: {pos_type} 1 contract at {price:.2f} pt")
        else:
            # We have a position. Check if this is the opposite action (exit) or addition
            pos_type = active_position["type"]
            is_exit = (pos_type == "LONG" and action == "매도") or (pos_type == "SHORT" and action == "매수")
            
            if is_exit:
                # Calculate PnL
                entry = active_position["entry_price"]
                pnl_pt = (price - entry) if pos_type == "LONG" else (entry - price)
                pnl_krw = pnl_pt * POINT_VALUE * qty
                trade_list.append({
                    "type": pos_type,
                    "entry": entry,
                    "exit": price,
                    "qty": qty,
                    "pnl_pt": pnl_pt,
                    "pnl_krw": pnl_krw
                })
                print(f"Position CLOSED: {pos_type} 1 contract at {price:.2f} pt (Entry: {entry:.2f} pt) | PnL: {pnl_pt:+.2f} pt ({pnl_krw:+,.0f}원)")
                active_position = None
            else:
                # Adding to position (not expected here based on log, but handle just in case)
                # For simplicity, average the price
                old_qty = active_position["qty"]
                old_price = active_position["entry_price"]
                new_qty = old_qty + qty
                avg_price = (old_price * old_qty + price * qty) / new_qty
                active_position["entry_price"] = avg_price
                active_position["qty"] = new_qty
                print(f"Position ADDED: {pos_type} {qty} contract at {price:.2f} pt. Avg price: {avg_price:.2f} pt")

print("\n=== Futures Trading Summary Table ===")
print(f"{'순번':<4} | {'구분':<5} | {'진입가':<8} | {'청산가':<8} | {'수량':<4} | {'손익(pt)':<8} | {'손익(원)':<12}")
print("-" * 65)

total_pnl_pt = 0
total_pnl_krw = 0
for idx, t in enumerate(trade_list):
    print(f"{idx+1:<4} | {t['type']:<5} | {t['entry']:<8.2f} | {t['exit']:<8.2f} | {t['qty']:<4} | {t['pnl_pt']:+8.2f} | {t['pnl_krw']:+12,.0f}원")
    total_pnl_pt += t['pnl_pt']
    total_pnl_krw += t['pnl_krw']

print("-" * 65)
print(f"총 손익 합계: {total_pnl_pt:+.2f} pt | {total_pnl_krw:+,.0f}원")

if active_position:
    print(f"\n현재 보유중인 포지션: {active_position['type']} {active_position['qty']}계약 | 평단: {active_position['entry_price']:.2f}pt")
else:
    print("\n현재 보유중인 포지션 없음.")
