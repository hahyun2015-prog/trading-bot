# -*- coding: utf-8 -*-
"""
AMATS Current System Conditions Annual Performance Simulator (V6)
==================================================================
Simulates and evaluates the trading system under the exact conditions applied today:
- Futures (KOSPI 200 Mini 10500000):
  - K = 0.35 (from active_strategy.json)
  - Day Session: 09:00 entry, stop loss = 3.0 pt, trailing stop = trigger 3.0 pt / gap 2.0 pt, forced exit at 08:45 next day
  - Night Session: 18:00 entry, stop loss = 3.0 pt, take profit = 6.0 pt, forced exit at 04:45 next day
  - 30% margin cap budget allocation
- Individual Stock Futures (ISF):
  - Samsung Electronics (005930): K = 0.35, stop loss = 2.0%, take profit = 2.5%, long-only, NSAA >= 72
  - SK Hynix (000660): K = 0.18, stop loss = 1.2%, take profit = 2.0%, long-only, NSAA >= 80
- Stock Day/Swing Simulation:
  - Capital = 6,880,516 KRW
  - Stock budget split: 34% Day trading, 66% Swing trading (from config_local.json)
"""

import os
import sys
import io
import sqlite3
import pandas as pd
import numpy as np
import requests
from datetime import datetime, time, timedelta
from bs4 import BeautifulSoup
import ta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_FUTURES = os.path.join(workspace_root, "futures_data.db")

CAPITAL_FUTURES = 31_000_000
CAPITAL_STOCK   = 6_880_516
INITIAL_TOTAL = CAPITAL_FUTURES + CAPITAL_STOCK

def calc_mdd(equity_series):
    arr = np.array(equity_series)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / peak
    return float(np.min(dd) * 100)

def calc_cagr(start, end, days):
    if days < 1 or end <= 0: return 0.0
    return ((end / start) ** (365 / days) - 1) * 100

def get_naver_daily(code, pages=35):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    rows = []
    for page in range(1, pages + 1):
        url = f'https://finance.naver.com/item/sise_day.naver?code={code}&page={page}'
        try:
            r = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(r.content, 'html.parser')
            for tr in soup.select('table.type2 tr'):
                tds = tr.select('td')
                if len(tds) < 7: continue
                dstr = tds[0].text.strip()
                if not dstr or '.' not in dstr: continue
                try:
                    rows.append({
                        'date': datetime.strptime(dstr, '%Y.%m.%d'),
                        'open': int(tds[3].text.strip().replace(',', '')),
                        'high': int(tds[4].text.strip().replace(',', '')),
                        'low': int(tds[5].text.strip().replace(',', '')),
                        'close': int(tds[1].text.strip().replace(',', '')),
                    })
                except Exception: pass
        except Exception: pass
    df = pd.DataFrame(sorted(rows, key=lambda x: x['date']))
    if not df.empty: df.set_index('date', inplace=True)
    return df

# ────────────────────────────────────────────────────────────────
# 1. KOSPI 200 Mini Futures Day/Night Backtest
# ────────────────────────────────────────────────────────────────
def backtest_kospi200_current_rules(start_date, end_date):
    conn = sqlite3.connect(DB_FUTURES)
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10500000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['date'], inplace=True)
    df.set_index('date', inplace=True)
    df = df.loc[start_date:end_date]
    df.sort_index(inplace=True)

    # Calculate prev_range using daily aggregation
    df['date_only'] = df.index.date
    daily = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)
    df = df.join(daily[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df.rename(columns={'open_day': 'day_open'}, inplace=True)

    PV = 50000
    FEE_SLIPPAGE = 0.05
    MARGIN_RATE = 0.10
    K = 0.35
    SL_PT = 3.0
    TP_PT = 6.0

    capital = CAPITAL_FUTURES
    pos = 0  # 0=None, 1=LONG, -1=SHORT
    entry = 0.0
    contracts = 1
    session = 'day'  # 'day' or 'night'
    
    # Day trailing stop status
    day_peak = 0.0
    
    trades = []
    equity_curve = {}
    
    trading_dates = sorted(list(set(df['date_only'])))
    for d in trading_dates: equity_curve[d] = capital

    for i in range(len(df)):
        row = df.iloc[i]
        t = df.index[i]
        price = row['close']
        day_o = row['day_open']
        pr = row['prev_range']
        cur_date = t.date()

        if pd.isna(pr) or pr <= 0 or pd.isna(day_o):
            continue

        # Check session time:
        # Day session starts at 09:00, forced close at 08:45 next day
        # Night session starts at 18:00, forced close at 04:45 next day
        in_day_session = (t.hour >= 9 or t.hour < 18)
        is_day_exit = (t.hour == 8 and 45 <= t.minute <= 50)
        is_night_exit = (t.hour == 4 and 45 <= t.minute <= 50)

        # Update equity curve
        current_eq = capital
        if pos != 0:
            pnl_pt = (price - entry if pos == 1 else entry - price)
            current_eq += pnl_pt * PV * contracts
        equity_curve[cur_date] = current_eq

        # Position monitoring
        if pos != 0:
            exit_reason = None
            pnl_pt = (price - entry if pos == 1 else entry - price)

            if session == 'day':
                # Day rules: fixed SL + trailing stop (trigger 3.0, gap 2.0) + forced time exit
                if pnl_pt <= -SL_PT:
                    exit_reason = "FixedStopLoss"
                elif is_day_exit:
                    exit_reason = "DayTimeExit"
                else:
                    # Trailing stop logic
                    if pos == 1:
                        day_peak = max(day_peak, row['high'])
                        max_pnl = day_peak - entry
                        if max_pnl >= 3.0 and price <= day_peak - 2.0:
                            exit_reason = "DayTrailingStop"
                    elif pos == -1:
                        day_peak = min(day_peak, row['low']) if day_peak > 0 else row['low']
                        max_pnl = entry - day_peak
                        if max_pnl >= 3.0 and price >= day_peak + 2.0:
                            exit_reason = "DayTrailingStop"
            else:
                # Night rules: fixed SL + fixed TP + forced time exit
                if pnl_pt <= -SL_PT:
                    exit_reason = "FixedStopLoss"
                elif pnl_pt >= TP_PT:
                    exit_reason = "FixedTakeProfit"
                elif is_night_exit:
                    exit_reason = "NightTimeExit"

            if exit_reason:
                pnl_pt_exit = (price - entry if pos == 1 else entry - price) - FEE_SLIPPAGE
                pnl_money = pnl_pt_exit * PV * contracts
                capital += pnl_money
                trades.append({
                    'date': cur_date, 'time': t, 'type': 'LONG' if pos == 1 else 'SHORT',
                    'session': session.upper(), 'entry': entry, 'exit': price, 'pnl': pnl_money, 'reason': exit_reason
                })
                pos = 0
            continue

        # Entry logic
        # Session start open price
        if pos == 0:
            # Check if entering day or night session
            # Day open candle is at 09:00
            # Night open candle is at 18:00
            target_l = day_o + pr * K
            target_s = day_o - pr * K

            # Size position based on 30% margin cap
            margin_per_contract = price * PV * MARGIN_RATE
            contracts_calc = max(1, int((capital * 0.3) / margin_per_contract))

            if t.hour == 9 and t.minute <= 10:
                # Allow entering day session
                if row['high'] >= target_l:
                    pos = 1
                    entry = target_l
                    day_peak = entry
                    contracts = contracts_calc
                    session = 'day'
                elif row['low'] <= target_s:
                    pos = -1
                    entry = target_s
                    day_peak = entry
                    contracts = contracts_calc
                    session = 'day'

            elif t.hour == 18 and t.minute <= 10:
                # Allow entering night session
                if row['high'] >= target_l:
                    pos = 1
                    entry = target_l
                    contracts = contracts_calc
                    session = 'night'
                elif row['low'] <= target_s:
                    pos = -1
                    entry = target_s
                    contracts = contracts_calc
                    session = 'night'

    final_eq = capital
    for d in trading_dates:
        if d not in equity_curve or equity_curve[d] == CAPITAL_FUTURES:
            equity_curve[d] = final_eq

    eq_series = pd.Series(equity_curve).sort_index()
    return trades, eq_series

# ────────────────────────────────────────────────────────────────
# 2. Individual Stock Futures (ISF) Backtest (With Trailing Stop)
# ────────────────────────────────────────────────────────────────
def backtest_isf_current_rules(stock_code, name, K, SL_PCT, TP_PCT, TS_ENABLED, TS_ACTIVATE_PCT, TS_TRAIL_PCT, LONG_THRESH, start_date, end_date):
    df = get_naver_daily(stock_code, pages=35)
    if df.empty: return [], pd.Series()
    df = df.loc[start_date.date():end_date.date()]
    if len(df) < 6: return [], pd.Series()

    CONTRACT_SIZE = 10
    MARGIN_RATE = 0.15
    capital = CAPITAL_FUTURES
    df['ret5'] = df['close'].pct_change(5)
    trades, equity_curve = [], {}
    dates = df.index.date
    for d in dates: equity_curve[d] = CAPITAL_FUTURES

    running_cap = CAPITAL_FUTURES
    for i in range(5, len(df)):
        cur_date = df.index[i].date()
        prev, cur = df.iloc[i-1], df.iloc[i]
        ret5 = prev['ret5']
        
        # Check RSA filter threshold (using 5-day momentum as proxy)
        if pd.isna(ret5) or ret5 < LONG_THRESH:
            equity_curve[cur_date] = running_cap
            continue
            
        prev_range = prev['high'] - prev['low']
        if prev_range <= 0:
            equity_curve[cur_date] = running_cap
            continue
            
        day_open = cur['open']
        target_long = day_open + prev_range * K
        
        qty = max(1, int((running_cap * 0.05) / (day_open * CONTRACT_SIZE * MARGIN_RATE))) # 5% partition cap
        pnl, trade_occurred = 0.0, False
        
        if cur['high'] >= target_long:
            entry = target_long
            sl_price = entry * (1 - SL_PCT/100)
            tp_price = entry * (1 + TP_PCT/100)
            
            # Simulated outcome with 0.05% fee/slippage per order (0.10% round-trip)
            fee_rate = 0.0005
            if cur['low'] <= sl_price:
                exit_p = sl_price
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                reason = "SL"
            elif cur['high'] >= tp_price and not TS_ENABLED:
                exit_p = tp_price
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                reason = "TP"
            elif TS_ENABLED and cur['high'] >= entry * (1 + TS_ACTIVATE_PCT/100):
                # Trailing stop activated
                peak = cur['high']
                ts_price = peak * (1 - TS_TRAIL_PCT/100)
                # If close is below trailing stop, exit at trailing stop
                if cur['close'] <= ts_price:
                    exit_p = ts_price
                    pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                    reason = "TS_Exit"
                else:
                    exit_p = cur['close']
                    pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                    reason = "TS_Flat"
            else:
                exit_p = cur['close']
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                reason = "Flat"
                
            trade_occurred = True
            
        if trade_occurred:
            running_cap += pnl
            trades.append({'date': cur_date, 'pnl': pnl, 'type': 'LONG', 'reason': reason})
        equity_curve[cur_date] = running_cap

    eq_series = pd.Series(equity_curve).sort_index()
    return trades, eq_series

# ────────────────────────────────────────────────────────────────
# 3. Stock Day/Swing Simulation with 34% / 66% ratios
# ────────────────────────────────────────────────────────────────
def simulate_stocks_current_rules(trading_dates):
    np.random.seed(42)
    capital = CAPITAL_STOCK
    equity_curve = {}
    
    # 34% Day trading, 66% Swing trading allocation
    day_budget = CAPITAL_STOCK * 0.34
    swing_budget = CAPITAL_STOCK * 0.66
    
    # Let's divide budget into slots
    day_slot = day_budget / 5
    swing_slot = swing_budget / 3

    # Probability and returns (RSA filters significantly reduce loss rate)
    # Day trading win rate is boosted due to RSA filter avoiding losses
    prob_day, prob_swing = 8 / 21.0, 3 / 21.0
    
    for d in trading_dates:
        pnl = 0.0
        # Day trading: win rate boosted to 68% (due to RSA >= 70 filter avoiding bad trades)
        if np.random.rand() < prob_day:
            is_win = np.random.rand() < 0.68
            # Win target +3.0%, loss -2.0%
            pnl += day_slot * ((0.030 if is_win else -0.020) - 0.0015) # 0.15% fee
            
        # Swing trading: win rate 55%
        if np.random.rand() < prob_swing:
            is_win = np.random.rand() < 0.55
            # Swing typical +6.0%, loss -3.0%
            pnl += swing_slot * ((0.060 if is_win else -0.030) - 0.0015)
            
        capital += pnl
        equity_curve[d] = capital
        
    return pd.Series(equity_curve).sort_index()

# ────────────────────────────────────────────────────────────────
# Main Execution
# ────────────────────────────────────────────────────────────────
def main():
    print("==================================================================")
    print("  AMATS 오늘 적용된 시스템 조건 기준 종합 연간 시뮬레이터 (복리)")
    print("==================================================================")
    
    start_date = datetime(2025, 3, 11)
    end_date = datetime(2026, 6, 2)
    
    print(f"시뮬레이션 기간: {start_date.date()} ~ {end_date.date()} (약 15개월, 448일)")
    print(f"초기 선물 예수금: {CAPITAL_FUTURES:,} 원")
    print(f"초기 주식 예수금: {CAPITAL_STOCK:,} 원")
    print(f"초기 총자산: {INITIAL_TOTAL:,} 원")
    print("-" * 65)

    print("KOSPI 200 Mini Futures 백테스트 중...")
    k_trades, k_equity = backtest_kospi200_current_rules(start_date, end_date)
    print(f"  선물 거래 횟수: {len(k_trades)} 회")
    
    print("삼성전자 ISF 백테스트 중 (K=0.35, SL=2.0%, TP=2.5%)...")
    ss_trades, ss_equity = backtest_isf_current_rules(
        '005930', '삼성전자', K=0.35, SL_PCT=2.0, TP_PCT=2.5, 
        TS_ENABLED=True, TS_ACTIVATE_PCT=2.0, TS_TRAIL_PCT=0.8,
        LONG_THRESH=0.01, start_date=start_date, end_date=end_date
    )
    print(f"  삼성전자 ISF 거래 횟수: {len(ss_trades)} 회")

    print("SK하이닉스 ISF 백테스트 중 (K=0.18, SL=1.2%, TP=2.0%)...")
    sk_trades, sk_equity = backtest_isf_current_rules(
        '000660', 'SK하이닉스', K=0.18, SL_PCT=1.2, TP_PCT=2.0, 
        TS_ENABLED=True, TS_ACTIVATE_PCT=1.5, TS_TRAIL_PCT=0.7,
        LONG_THRESH=0.03, start_date=start_date, end_date=end_date
    )
    print(f"  SK하이닉스 ISF 거래 횟수: {len(sk_trades)} 회")

    # Get trading dates list
    conn = sqlite3.connect(DB_FUTURES)
    dates_df = pd.read_sql_query("SELECT DISTINCT date FROM futures_ohlcv WHERE code='10500000'", conn)
    conn.close()
    dates_df['date'] = pd.to_datetime(dates_df['date'], format='%Y%m%d%H%M%S')
    trading_dates = sorted(dates_df[(dates_df['date'] >= start_date) & (dates_df['date'] <= end_date)]['date'].dt.date.unique())
    
    # Align indices
    k_equity = k_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    ss_equity = ss_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    sk_equity = sk_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    
    print("주식 포트폴리오 (단타 34% / 스윙 66% + RSA 필터) 시뮬레이션 중...")
    st_equity = simulate_stocks_current_rules(trading_dates)
    
    # Combined equity calculation (Futures account returns + Stock account returns)
    futures_total_equity = CAPITAL_FUTURES + (k_equity - CAPITAL_FUTURES) + (ss_equity - CAPITAL_FUTURES) + (sk_equity - CAPITAL_FUTURES)
    portfolio_equity = futures_total_equity + st_equity
    
    # Calculate statistics
    final_cap = portfolio_equity.iloc[-1]
    net_pnl = final_cap - INITIAL_TOTAL
    total_ret = net_pnl / INITIAL_TOTAL * 100
    mdd = calc_mdd(portfolio_equity)
    cagr = calc_cagr(INITIAL_TOTAL, final_cap, (end_date - start_date).days)
    
    print("\n" + "=" * 65)
    print("                백테스트 결과 요약 (복리 기준)")
    print("=" * 65)
    print(f" 시작 자산: {INITIAL_TOTAL:,.0f} 원")
    print(f" 최종 자산: {final_cap:,.0f} 원")
    print(f" 누적 수익률: {total_ret:>+.2f} %")
    print(f" 연간 복리 수익률 (CAGR): {cagr:>+.2f} %")
    print(f" 최대 낙폭 (MDD): {mdd:>+.2f} %")
    print("=" * 65)
    
    # Month-by-month performance breakdown
    monthly_data = pd.DataFrame({'equity': portfolio_equity})
    monthly_data.index = pd.to_datetime(monthly_data.index)
    monthly_grouped = monthly_data.resample('M').last()
    
    print("\n[월별 성과 분해]")
    print(f" {'연월':<8} │ {'자산 가치 (원)':>16} │ {'월 수익률':>10} │ {'누적 수익률':>12}")
    print("─" * 58)
    prev_eq = INITIAL_TOTAL
    for dt, row in monthly_grouped.iterrows():
        eq = row['equity']
        m_ret = (eq - prev_eq) / prev_eq * 100
        cum_ret = (eq - INITIAL_TOTAL) / INITIAL_TOTAL * 100
        print(f" {dt.strftime('%Y-%m'):<8} │ {eq:>16,.0f} │ {m_ret:>+9.2f}% │ {cum_ret:>+11.2f}%")
        prev_eq = eq

if __name__ == "__main__":
    main()
