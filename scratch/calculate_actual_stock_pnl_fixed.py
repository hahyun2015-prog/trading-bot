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

# Track restored positions from the log:
# [ERA] 기존 포지션 복원 완료: {'040160': {'strategy': 'SWING', 'half_sold': False, 'open_price': 9060.0}, '217190': {'strategy': 'DAY', 'half_sold': False, 'open_price': 5290}, '255440': {'strategy': 'SWING', 'half_sold': False, 'open_price': 8310.0}}
# Let's search the log for restoration and get starting positions.
# If we look at the lines:
# - 누리플렉스(040160) | 49주 | 평단: 9,069원 (하프매도여부: False)
# - 제너셈(217190) | 90주 | 평단: 5,290원 (or similar? wait, let's find the exact lines)
# Let's search the today_lines for "[SWING]" or "[DAY]" restoration lines.
restored_positions = {
    "040160": {"name": "누리플렉스", "qty": 49, "price": 9069.0},
    "217190": {"name": "제너셈", "qty": 90, "price": 5290.0}, # we saw -90 qty in previous run, meaning it sold 90, so starting was 90
    "255440": {"name": "야스", "qty": 57, "price": 8258.0} # from the line: - [SWING] 야스(255440) | 57주 | 평단: 8,258원
}

# Parse execution logs
# Let's support both [주식 체결 확인] and [주식 실체결 확정]
# Format: [주식 체결 확인] 누리플렉스(040160) | 9,120원 | 4주 | +매수
# Or: [주식 실체결 확정] 누리플렉스(040160) | 9,120원 | 4주 | +매수
# Also sometimes -매도 or +매수
pattern = re.compile(r"\[주식\s+(?:체결 확인|실체결 확정)\]\s+(.*?)\((\d+)\)\s*\|\s*([\d,]+)원\s*\|\s*(\d+)주\s*\|\s*([+-])(매수|매도)")

trades = {}
for code, pos in restored_positions.items():
    trades[code] = {
        "name": pos["name"],
        "buys": [(pos["price"], pos["qty"])],
        "sells": []
    }

for line in today_lines:
    l = line.strip()
    match = pattern.search(l)
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

print("=== STOCK TRADING ANALYSIS ===")
print(f"{'종목명(코드)':<20} | {'총 매수수량':<8} | {'평균매수가':<10} | {'총 매도수량':<8} | {'평균매도가':<10} | {'현재보유':<6} | {'실현손익':<12}")
print("-" * 90)

total_realized_pnl = 0

for code, info in trades.items():
    name_code = f"{info['name']}({code})"
    
    total_buy_qty = sum(qty for _, qty in info['buys'])
    total_buy_val = sum(p * qty for p, qty in info['buys'])
    avg_buy_price = total_buy_val / total_buy_qty if total_buy_qty > 0 else 0
    
    total_sell_qty = sum(qty for _, qty in info['sells'])
    total_sell_val = sum(p * qty for p, qty in info['sells'])
    avg_sell_price = total_sell_val / total_sell_qty if total_sell_qty > 0 else 0
    
    current_qty = total_buy_qty - total_sell_qty
    
    # Realized PnL is based on minimum of buy and sell quantity
    realized_qty = min(total_buy_qty, total_sell_qty)
    if realized_qty > 0:
        realized_pnl = (avg_sell_price - avg_buy_price) * realized_qty
    else:
        realized_pnl = 0
        
    total_realized_pnl += realized_pnl
    
    pnl_str = f"{realized_pnl:+,.0f}원" if realized_pnl != 0 else "0원"
    print(f"{name_code:<20} | {total_buy_qty:<12} | {avg_buy_price:<12.1f} | {total_sell_qty:<12} | {avg_sell_price:<12.1f} | {current_qty:<8} | {pnl_str:>12}")

print("-" * 90)
print(f"오늘 총 실현손익 (주식 합계): {total_realized_pnl:+,.0f}원")
