import json

with open('scratch/today_stock_prices.json', 'r', encoding='utf-8') as f:
    stocks = json.load(f)

print("=== 주식 포지션 오늘 시뮬레이션 결과 ===")
total_stock_pnl = 0.0
total_stock_buy_value = 0.0
total_stock_sell_value = 0.0

stock_summary = []

for s in stocks:
    code = s['code']
    name = s['name']
    strat = s['strategy']
    qty = s['qty']
    buy_p = s['buy_price']
    
    # Exits:
    exit_price = s['close'] # default exit is close
    exit_reason = "15:15 장마감 일괄청산" if strat == 'DAY' else "15:14 종가 10MA 이탈 청산"
    
    # Check DAY stop loss
    if strat == 'DAY':
        sl_price = int(buy_p * 0.98)
        if s['open'] < sl_price:
            exit_price = s['open']
            exit_reason = f"시초가 갭하락 손절 (-2% 이하 시가 청산)"
        elif s['low'] <= sl_price:
            exit_price = sl_price
            exit_reason = f"장중 고정 손절선(-2%) 터치 청산"
    
    # Check SWING 5MA/10MA breach (15:14 close check)
    elif strat == 'SWING':
        # hard stop loss: we don't have breakout open price, but we check if today's close is below 10MA
        if s['close'] < s['ma_10']:
            exit_price = s['close']
            exit_reason = "종가 10MA 이탈 전량 청산"
        elif s['close'] < s['ma_5']:
            # half sold
            exit_price = s['close']
            exit_reason = "종가 5MA 이탈 50% 청산 (나머지 홀딩)"
            # we will assume 50% sold at close
        else:
            exit_price = s['close']
            exit_reason = "조건 만족 홀딩 (청산 없음)"
            
    # Calculate PnL
    buy_val = buy_p * qty
    
    if exit_reason == "종가 5MA 이탈 50% 청산 (나머지 홀딩)":
        sell_val = (exit_price * (qty / 2)) + (s['close'] * (qty / 2)) # in this case it's same, but for bookkeeping
        pnl = (exit_price - buy_p) * (qty / 2) # PnL of the sold half
        pnl_pct = ((exit_price / buy_p) - 1.0) * 100
        # for reporting, we count it as half liquidated
        status = f"50% 매도 ({qty/2:.1f}주)"
    elif exit_reason == "조건 만족 홀딩 (청산 없음)":
        sell_val = s['close'] * qty
        pnl = (s['close'] - buy_p) * qty # floating pnl
        pnl_pct = ((s['close'] / buy_p) - 1.0) * 100
        status = "홀딩"
    else:
        sell_val = exit_price * qty
        pnl = (exit_price - buy_p) * qty
        pnl_pct = ((exit_price / buy_p) - 1.0) * 100
        status = "전량 매도"
        
    total_stock_pnl += pnl
    total_stock_buy_value += buy_val
    if status == "전량 매도" or status.startswith("50%"):
        total_stock_sell_value += sell_val
    else:
        total_stock_sell_value += buy_val # keep capital same for holding
        
    stock_summary.append({
        'name': name,
        'code': code,
        'strat': strat,
        'qty': qty,
        'buy_p': buy_p,
        'exit_p': exit_price,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'reason': exit_reason,
        'status': status
    })

print("\n상세 요약:")
for ss in stock_summary:
    print(f"[{ss['strat']}] {ss['name']}({ss['code']}) {ss['qty']}주 | 평단 {ss['buy_p']:,}원 -> 청산/종가 {ss['exit_p']:,}원 | 손익: {ss['pnl']:+,.0f}원 ({ss['pnl_pct']:+.2f}%) | 사유: {ss['reason']}")

print("\n=== 주식 종합 성과 ===")
print(f"총 매입 금액: {total_stock_buy_value:,.0f}원")
print(f"총 실현/평가 손익: {total_stock_pnl:+,.0f}원 (수익률: {total_stock_pnl/total_stock_buy_value*100:+.2f}%)")
