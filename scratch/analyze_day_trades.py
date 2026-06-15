import re
import os

def analyze():
    log_path = r"c:\Antigravity\AI_T_Agent\era\era_order_manager.log"
    if not os.path.exists(log_path):
        print("Log file not found.")
        return

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    today_start_idx = 0
    for idx in range(len(lines) - 1, -1, -1):
        if "[ERA 주간선물] 08:40" in lines[idx] or "08:40 세션 준비" in lines[idx]:
            today_start_idx = idx
            break
    
    if today_start_idx == 0:
        today_start_idx = max(0, len(lines) - 1000)

    today_lines = lines[today_start_idx:]

    pattern = r"\[주식 실체결 확정\]\s+([^(]+)\((\d+)\)\s+\|\s+([\d,]+)원\s+\|\s+(\d+)주\s+\|\s+([+-])(매수|매도)"
    
    trades = {}
    for line in today_lines:
        match = re.search(pattern, line)
        if match:
            name = match.group(1).strip()
            code = match.group(2).strip()
            price = int(match.group(3).replace(",", ""))
            qty = int(match.group(4))
            side = match.group(5)
            
            if code not in trades:
                trades[code] = {'name': name, 'buys': [], 'sells': []}
            
            if side == '+':
                trades[code]['buys'].append((price, qty))
            elif side == '-':
                trades[code]['sells'].append((price, qty))

    completed_trades = []
    for code, info in trades.items():
        name = info['name']
        buys = info['buys']
        sells = info['sells']
        
        total_buy_qty = sum(q for p, q in buys)
        total_buy_amt = sum(p * q for p, q in buys)
        avg_buy_price = total_buy_amt / total_buy_qty if total_buy_qty > 0 else 0
        
        total_sell_qty = sum(q for p, q in sells)
        total_sell_amt = sum(p * q for p, q in sells)
        avg_sell_price = total_sell_amt / total_sell_qty if total_sell_qty > 0 else 0
        
        if total_buy_qty > 0 and total_sell_qty > 0:
            matched_qty = min(total_buy_qty, total_sell_qty)
            buy_cost = avg_buy_price * matched_qty
            sell_value = avg_sell_price * matched_qty
            pnl_gross = sell_value - buy_cost
            
            completed_trades.append({
                'code': code,
                'name': name,
                'qty': matched_qty,
                'buy_cost': buy_cost,
                'sell_value': sell_value,
                'pnl_gross': pnl_gross
            })

    if not completed_trades:
        print("No completed day trades found for today.")
        return

    # 두 가지 수수료율 정의
    # 1. 실전 거래 수수료율 (매수 0.015%, 매도 0.015%, 거래세 0.18%)
    live_buy_fee_rate = 0.00015
    live_sell_fee_rate = 0.00015
    live_tax_rate = 0.0018
    
    # 2. 모의 투자 수수료율 (매수 0.35%, 매도 0.35%, 거래세 0.18%)
    mock_buy_fee_rate = 0.0035
    mock_sell_fee_rate = 0.0035
    mock_tax_rate = 0.0018

    print("\n=== TODAY DAY TRADING ANALYSIS WITH FEES & TAXES ===")
    
    total_buy_cost = 0
    total_sell_value = 0
    total_pnl_gross = 0
    
    total_live_fees_tax = 0
    total_mock_fees_tax = 0
    
    completed_rows = []
    
    for t in completed_trades:
        buy_cost = t['buy_cost']
        sell_value = t['sell_value']
        pnl_gross = t['pnl_gross']
        
        total_buy_cost += buy_cost
        total_sell_value += sell_value
        total_pnl_gross += pnl_gross
        
        # 실전 세금 및 수수료 계산
        live_fees_tax = (buy_cost * live_buy_fee_rate) + (sell_value * live_sell_fee_rate) + (sell_value * live_tax_rate)
        live_pnl_net = pnl_gross - live_fees_tax
        live_pnl_pct = (live_pnl_net / buy_cost) * 100
        total_live_fees_tax += live_fees_tax
        
        # 모의 세금 및 수수료 계산
        mock_fees_tax = (buy_cost * mock_buy_fee_rate) + (sell_value * mock_sell_fee_rate) + (sell_value * mock_tax_rate)
        mock_pnl_net = pnl_gross - mock_fees_tax
        mock_pnl_pct = (mock_pnl_net / buy_cost) * 100
        total_mock_fees_tax += mock_fees_tax
        
        completed_rows.append({
            'name': t['name'],
            'code': t['code'],
            'qty': t['qty'],
            'pnl_gross': pnl_gross,
            'live_pnl_net': live_pnl_net,
            'live_pnl_pct': live_pnl_pct,
            'mock_pnl_net': mock_pnl_net,
            'mock_pnl_pct': mock_pnl_pct
        })
        
        print(f"{t['name']}({t['code']}): Qty {t['qty']} | Gross {pnl_gross:+,.0f} | Live Net {live_pnl_net:+,.0f} ({live_pnl_pct:+.2f}%) | Mock Net {mock_pnl_net:+,.0f} ({mock_pnl_pct:+.2f}%)")

    live_overall_pct = ((total_pnl_gross - total_live_fees_tax) / total_buy_cost) * 100
    mock_overall_pct = ((total_pnl_gross - total_mock_fees_tax) / total_buy_cost) * 100
    
    print("--------------------------------------------------")
    print(f"Total Invested Amount: {total_buy_cost:,.0f}원")
    print(f"Total Gross Profit/Loss: {total_pnl_gross:+,.0f}원")
    print("\n--- 1. LIVE TRADING ACCOUNT (실전 거래 요율 적용) ---")
    print(f"Total Live Fees & Taxes: {total_live_fees_tax:,.0f}원")
    print(f"Live Net Profit/Loss: {total_pnl_gross - total_live_fees_tax:+,.0f}원")
    print(f"Live Net Win Rate: {sum(1 for r in completed_rows if r['live_pnl_net'] > 0)} / {len(completed_rows)} ({sum(1 for r in completed_rows if r['live_pnl_net'] > 0)/len(completed_rows)*100:.2f}%)")
    print(f"Live Net Return Rate: {live_overall_pct:+.2f}%")
    
    print("\n--- 2. MOCK TRADING ACCOUNT (모의 투자 요율 적용) ---")
    print(f"Total Mock Fees & Taxes: {total_mock_fees_tax:,.0f}원")
    print(f"Mock Net Profit/Loss: {total_pnl_gross - total_mock_fees_tax:+,.0f}원")
    print(f"Mock Net Win Rate: {sum(1 for r in completed_rows if r['mock_pnl_net'] > 0)} / {len(completed_rows)} ({sum(1 for r in completed_rows if r['mock_pnl_net'] > 0)/len(completed_rows)*100:.2f}%)")
    print(f"Mock Net Return Rate: {mock_overall_pct:+.2f}%")

if __name__ == "__main__":
    analyze()
