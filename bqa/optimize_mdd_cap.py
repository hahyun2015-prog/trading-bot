# -*- coding: utf-8 -*-
"""
AMATS Risk-Return Optimizer (MDD < 50% Constraint) - OPTIMIZED
===============================================================
Sweeps parameters for KOSPI 200 Mini Futures (K, Margin Cap, SL, TP, ATR Cutoff)
to maximize CAGR while keeping MDD strictly below 50%.
Uses fast numpy array iteration to run in seconds.
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
    if len(arr) == 0: return 0.0
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
# Parametrized KOSPI 200 Mini Futures day/night backtest (FAST)
# ────────────────────────────────────────────────────────────────
def backtest_kospi200_fast(dates, closes, highs, lows, day_opens, prev_ranges, atrs, date_onlies, trading_dates, K, MARGIN_CAP, SL_PT, TP_PT, ATR_CUTOFF=None):
    PV = 50000
    FEE_SLIPPAGE = 0.05
    MARGIN_RATE = 0.10

    capital = CAPITAL_FUTURES
    pos = 0
    entry = 0.0
    contracts = 1
    session = 'day'
    day_peak = 0.0
    
    equity_curve = {d: capital for d in trading_dates}
    n = len(closes)

    for i in range(n):
        price = closes[i]
        high = highs[i]
        low = lows[i]
        day_o = day_opens[i]
        pr = prev_ranges[i]
        atr_val = atrs[i]
        t = dates[i]
        cur_date = date_onlies[i]

        if np.isnan(pr) or pr <= 0 or np.isnan(day_o) or np.isnan(atr_val):
            continue

        is_day_exit = (t.hour == 8 and 45 <= t.minute <= 50)
        is_night_exit = (t.hour == 4 and 45 <= t.minute <= 50)

        # Update equity curve
        current_eq = capital
        if pos != 0:
            pnl_pt = (price - entry if pos == 1 else entry - price)
            current_eq += pnl_pt * PV * contracts
        equity_curve[cur_date] = current_eq

        if pos != 0:
            exit_reason = None
            pnl_pt = (price - entry if pos == 1 else entry - price)

            if session == 'day':
                if pnl_pt <= -SL_PT:
                    exit_reason = "SL"
                elif is_day_exit:
                    exit_reason = "Exit"
                else:
                    if pos == 1:
                        day_peak = max(day_peak, high)
                        max_pnl = day_peak - entry
                        if max_pnl >= 3.0 and price <= day_peak - 2.0:
                            exit_reason = "TS"
                    elif pos == -1:
                        day_peak = min(day_peak, low) if day_peak > 0 else low
                        max_pnl = entry - day_peak
                        if max_pnl >= 3.0 and price >= day_peak + 2.0:
                            exit_reason = "TS"
            else:
                if pnl_pt <= -SL_PT:
                    exit_reason = "SL"
                elif pnl_pt >= TP_PT:
                    exit_reason = "TP"
                elif is_night_exit:
                    exit_reason = "Exit"

            if exit_reason:
                pnl_pt_exit = (price - entry if pos == 1 else entry - price) - FEE_SLIPPAGE
                pnl_money = pnl_pt_exit * PV * contracts
                capital += pnl_money
                pos = 0
            continue

        if pos == 0:
            target_l = day_o + pr * K
            target_s = day_o - pr * K

            # ATR Cutoff filter (skip trading if ATR is too low)
            if ATR_CUTOFF is not None and atr_val < ATR_CUTOFF:
                continue

            margin_per_contract = price * PV * MARGIN_RATE
            contracts_calc = max(1, int((capital * MARGIN_CAP) / margin_per_contract))

            if t.hour == 9 and t.minute <= 10:
                if high >= target_l:
                    pos = 1; entry = target_l; day_peak = entry; contracts = contracts_calc; session = 'day'
                elif low <= target_s:
                    pos = -1; entry = target_s; day_peak = entry; contracts = contracts_calc; session = 'day'
            elif t.hour == 18 and t.minute <= 10:
                if high >= target_l:
                    pos = 1; entry = target_l; contracts = contracts_calc; session = 'night'
                elif low <= target_s:
                    pos = -1; entry = target_s; contracts = contracts_calc; session = 'night'

    final_eq = capital
    for d in trading_dates:
        if d not in equity_curve or equity_curve[d] == CAPITAL_FUTURES:
            equity_curve[d] = final_eq

    eq_series = pd.Series(equity_curve).sort_index()
    return eq_series

# ────────────────────────────────────────────────────────────────
# ISF backtest (cached fast version)
# ────────────────────────────────────────────────────────────────
def backtest_isf_cached(df_stock, K, SL_PCT, TP_PCT, TS_ENABLED, TS_ACTIVATE_PCT, TS_TRAIL_PCT, LONG_THRESH):
    if df_stock.empty or len(df_stock) < 6: return pd.Series()
    CONTRACT_SIZE = 10
    MARGIN_RATE = 0.15
    capital = CAPITAL_FUTURES
    df = df_stock.copy()
    df['ret5'] = df['close'].pct_change(5)
    equity_curve = {}
    dates = df.index.date
    for d in dates: equity_curve[d] = CAPITAL_FUTURES

    running_cap = CAPITAL_FUTURES
    fee_rate = 0.0005  # 0.05% per order (0.10% round-trip)

    for i in range(5, len(df)):
        cur_date = df.index[i].date()
        prev, cur = df.iloc[i-1], df.iloc[i]
        ret5 = prev['ret5']
        
        if pd.isna(ret5) or ret5 < LONG_THRESH:
            equity_curve[cur_date] = running_cap
            continue
            
        prev_range = prev['high'] - prev['low']
        if prev_range <= 0:
            equity_curve[cur_date] = running_cap
            continue
            
        day_open = cur['open']
        target_long = day_open + prev_range * K
        
        qty = max(1, int((running_cap * 0.05) / (day_open * CONTRACT_SIZE * MARGIN_RATE)))
        pnl, trade_occurred = 0.0, False
        
        if cur['high'] >= target_long:
            entry = target_long
            sl_price = entry * (1 - SL_PCT/100)
            tp_price = entry * (1 + TP_PCT/100)
            
            if cur['low'] <= sl_price:
                exit_p = sl_price
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
            elif cur['high'] >= tp_price and not TS_ENABLED:
                exit_p = tp_price
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
            elif TS_ENABLED and cur['high'] >= entry * (1 + TS_ACTIVATE_PCT/100):
                peak = cur['high']
                ts_price = peak * (1 - TS_TRAIL_PCT/100)
                if cur['close'] <= ts_price:
                    exit_p = ts_price
                    pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                else:
                    exit_p = cur['close']
                    pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
            else:
                exit_p = cur['close']
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                
            trade_occurred = True
            
        if trade_occurred:
            running_cap += pnl
        equity_curve[cur_date] = running_cap

    return pd.Series(equity_curve).sort_index()

# ────────────────────────────────────────────────────────────────
# Stock DAY/SWING simulation
# ────────────────────────────────────────────────────────────────
def simulate_stocks_current_rules(trading_dates):
    np.random.seed(42)
    capital = CAPITAL_STOCK
    equity_curve = {}
    day_budget = CAPITAL_STOCK * 0.34
    swing_budget = CAPITAL_STOCK * 0.66
    day_slot = day_budget / 5
    swing_slot = swing_budget / 3
    prob_day, prob_swing = 8 / 21.0, 3 / 21.0
    
    for d in trading_dates:
        pnl = 0.0
        if np.random.rand() < prob_day:
            is_win = np.random.rand() < 0.68
            pnl += day_slot * ((0.030 if is_win else -0.020) - 0.0015)
        if np.random.rand() < prob_swing:
            is_win = np.random.rand() < 0.55
            pnl += swing_slot * ((0.060 if is_win else -0.030) - 0.0015)
        capital += pnl
        equity_curve[d] = capital
    return pd.Series(equity_curve).sort_index()

# ────────────────────────────────────────────────────────────────
# Main Optimization
# ────────────────────────────────────────────────────────────────
def main():
    start_date = datetime(2025, 3, 11)
    end_date = datetime(2026, 6, 2)
    
    print("선물 가격 데이터 로드 중...")
    conn = sqlite3.connect(DB_FUTURES)
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10500000' ORDER BY date ASC"
    df_futures = pd.read_sql_query(query, conn)
    conn.close()

    df_futures['date'] = pd.to_datetime(df_futures['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df_futures.dropna(subset=['date'], inplace=True)
    df_futures.set_index('date', inplace=True)
    df_futures = df_futures.loc[start_date:end_date]
    df_futures.sort_index(inplace=True)

    df_futures['date_only'] = df_futures.index.date
    daily = df_futures.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)
    df_futures = df_futures.join(daily[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df_futures.rename(columns={'open_day': 'day_open'}, inplace=True)
    
    # Calculate 14-day ATR for cutoff filters
    df_futures['atr'] = ta.volatility.AverageTrueRange(high=df_futures['high'], low=df_futures['low'], close=df_futures['close'], window=14).average_true_range()

    print("네이버 금융 주식 데이터 캐싱 중...")
    df_ss = get_naver_daily('005930', pages=35).loc[start_date.date():end_date.date()]
    df_sk = get_naver_daily('000660', pages=35).loc[start_date.date():end_date.date()]

    trading_dates = sorted(df_futures['date_only'].unique())
    
    print("정적 컴포넌트 사전 연산 중...")
    st_equity = simulate_stocks_current_rules(trading_dates)
    
    # Prepare Numpy arrays for fast futures simulation
    dates = df_futures.index.to_pydatetime()
    closes = df_futures['close'].values
    highs = df_futures['high'].values
    lows = df_futures['low'].values
    day_opens = df_futures['day_open'].values
    prev_ranges = df_futures['prev_range'].values
    atrs = df_futures['atr'].values
    date_onlies = df_futures['date_only'].values

    # Grid Search space
    K_LIST = [0.30, 0.35, 0.40]
    MARGIN_CAP_LIST = [0.30, 0.40, 0.50, 0.60] # Max margin limit
    SL_PT_LIST = [2.5, 3.0, 3.5]
    TP_PT_LIST = [5.0, 6.0, 7.0]
    ATR_CUTOFF_LIST = [None, 0.6, 0.8, 1.0]

    results = []
    
    total_combinations = len(K_LIST) * len(MARGIN_CAP_LIST) * len(SL_PT_LIST) * len(TP_PT_LIST) * len(ATR_CUTOFF_LIST)
    print(f"총 {total_combinations}개 조합 탐색 시작...")

    count = 0
    for K in K_LIST:
        # Precompute ISF for this K
        ss_equity = backtest_isf_cached(df_ss, K=K, SL_PCT=2.0, TP_PCT=2.5, TS_ENABLED=True, TS_ACTIVATE_PCT=2.0, TS_TRAIL_PCT=0.8, LONG_THRESH=0.01)
        sk_equity = backtest_isf_cached(df_sk, K=0.18, SL_PCT=1.2, TP_PCT=2.0, TS_ENABLED=True, TS_ACTIVATE_PCT=1.5, TS_TRAIL_PCT=0.7, LONG_THRESH=0.03) # fixed SK K
        
        ss_equity = ss_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
        sk_equity = sk_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)

        for m_cap in MARGIN_CAP_LIST:
            for sl in SL_PT_LIST:
                for tp in TP_PT_LIST:
                    for cutoff in ATR_CUTOFF_LIST:
                        count += 1
                        
                        # Run parametrized futures (FAST version)
                        k_equity = backtest_kospi200_fast(dates, closes, highs, lows, day_opens, prev_ranges, atrs, date_onlies, trading_dates, K, m_cap, sl, tp, cutoff)
                        k_equity = k_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
                        
                        # Combine portfolio equity
                        futures_total_equity = CAPITAL_FUTURES + (k_equity - CAPITAL_FUTURES) + (ss_equity - CAPITAL_FUTURES) + (sk_equity - CAPITAL_FUTURES)
                        portfolio_equity = futures_total_equity + st_equity
                        
                        final_cap = portfolio_equity.iloc[-1]
                        mdd = calc_mdd(portfolio_equity)
                        
                        # Filter for MDD < 50% (and mdd > -50%)
                        if mdd > -50.0:
                            cagr = calc_cagr(INITIAL_TOTAL, final_cap, (end_date - start_date).days)
                            results.append({
                                'K': K, 'MARGIN_CAP': m_cap, 'SL': sl, 'TP': tp, 'ATR_CUTOFF': cutoff,
                                'cagr': cagr, 'mdd': mdd, 'final_cap': final_cap
                            })

    # Sort results by CAGR descending
    results.sort(key=lambda x: x['cagr'], reverse=True)
    
    print("\n" + "="*85)
    print("                최적 조합 Top 15 (MDD < 50% 제약 만족)")
    print("="*85)
    print(f" {'순위':<3} │ {'K값':<4} │ {'증거금캡':<6} │ {'손절pt':<5} │ {'익절pt':<5} │ {'ATR필터':<6} │ {'CAGR (%)':>10} │ {'MDD (%)':>10}")
    print("─"*85)
    
    for idx, r in enumerate(results[:15]):
        rank = idx + 1
        cutoff_str = f"{r['ATR_CUTOFF']:.1f}pt" if r['ATR_CUTOFF'] is not None else "None"
        print(f" {rank:<3} │ {r['K']:<4.2f} │ {r['MARGIN_CAP']:<6.2f} │ {r['SL']:<5.1f} │ {r['TP']:<5.1f} │ {cutoff_str:<6} │ {r['cagr']:>10.2f}% │ {r['mdd']:>10.2f}%")
        
    print("="*85)

if __name__ == "__main__":
    main()
