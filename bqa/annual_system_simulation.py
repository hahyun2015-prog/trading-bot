# -*- coding: utf-8 -*-
"""
AMATS Finalized System Annual Performance Simulator (V5 - ATR Cutoff Tuning)
=============================================================================
Simulates and evaluates "Ultra-Low Volatility Trade Filtering" (ATR Cutoff).
If ATR is below the cutoff threshold, new trades are completely skipped.
"""

import os
import sys
import io
import sqlite3
import pandas as pd
import numpy as np
import requests
from datetime import datetime
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

def backtest_kospi200_enhanced(start_date, end_date, risk_per_trade=0.015, atr_floor=None, max_contracts=None, atr_cutoff=None):
    conn = sqlite3.connect(DB_FUTURES)
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10500000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['date'], inplace=True)
    df.set_index('date', inplace=True)
    df = df.loc[start_date:end_date]
    df.sort_index(inplace=True)

    bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_m'] = bb.bollinger_mavg()
    df['bb_h'] = bb.bollinger_hband()
    df['bb_l'] = bb.bollinger_lband()
    df['bb_width'] = (df['bb_h'] - df['bb_l']) / df['bb_m']
    df['bb_width_min_100'] = df['bb_width'].rolling(window=100, min_periods=20).min()
    df['is_squeeze'] = df['bb_width'] <= df['bb_width_min_100'] * 1.20
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
    
    macd_ind = ta.trend.MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
    df['macd_diff'] = macd_ind.macd_diff()

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

    capital = CAPITAL_FUTURES
    pos = 0
    entry = 0.0
    contracts = 1
    trailing_stop = 0.0
    max_favorable = 0.0
    
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
        atr_val = row['atr']
        cur_date = t.date()

        if pd.isna(pr) or pd.isna(atr_val) or atr_val <= 0 or pd.isna(day_o):
            continue

        target_l = day_o + pr * K
        target_s = day_o - pr * K
        morning_close = (t.hour == 8 and 45 <= t.minute <= 50)

        effective_atr = atr_val
        if atr_floor is not None and effective_atr < atr_floor:
            effective_atr = atr_floor

        current_eq = capital
        if pos != 0:
            pnl_pt = (price - entry if pos == 1 else entry - price)
            current_eq += pnl_pt * PV * contracts
        equity_curve[cur_date] = current_eq

        if pos != 0:
            exit_reason = None
            if pos == 1:
                if price > max_favorable:
                    max_favorable = price
                    trailing_stop = max_favorable - effective_atr * 2.5
                if price <= trailing_stop: exit_reason = "TrailingStop"
                elif morning_close: exit_reason = "MarketClose"
            elif pos == -1:
                if price < max_favorable:
                    max_favorable = price
                    trailing_stop = max_favorable + effective_atr * 2.5
                if price >= trailing_stop: exit_reason = "TrailingStop"
                elif morning_close: exit_reason = "MarketClose"

            if exit_reason:
                pnl_pt = (price - entry if pos == 1 else entry - price) - FEE_SLIPPAGE
                pnl_money = pnl_pt * PV * contracts
                capital += pnl_money
                trades.append({
                    'date': cur_date, 'time': t, 'type': 'LONG' if pos == 1 else 'SHORT',
                    'entry': entry, 'exit': price, 'pnl': pnl_money, 'reason': exit_reason
                })
                pos = 0
            continue

        if pos == 0 and not morning_close:
            if row['is_squeeze']: continue
            
            # ATR Cutoff filter (skip trading if raw ATR is too low)
            if atr_cutoff is not None and atr_val < atr_cutoff:
                continue

            risk_amount = capital * risk_per_trade
            stop_distance = effective_atr * 2.0
            contracts_calc = max(1, int(risk_amount / (stop_distance * PV)))

            margin_per_contract = price * PV * MARGIN_RATE
            max_contracts_by_margin = max(1, int((capital * 0.3) / margin_per_contract))
            contracts = min(contracts_calc, max_contracts_by_margin)

            if max_contracts is not None:
                contracts = min(contracts, max_contracts)

            if row['high'] >= target_l and row['rsi'] >= 50 and row['macd_diff'] > 0:
                pos = 1
                entry = target_l
                max_favorable = entry
                trailing_stop = entry - stop_distance
            elif row['low'] <= target_s and row['rsi'] <= 50 and row['macd_diff'] < 0:
                pos = -1
                entry = target_s
                max_favorable = entry
                trailing_stop = entry + stop_distance

    final_eq = capital
    for d in trading_dates:
        if d not in equity_curve or equity_curve[d] == CAPITAL_FUTURES:
            equity_curve[d] = final_eq

    eq_series = pd.Series(equity_curve).sort_index()
    return trades, eq_series

# ────────────────────────────────────────────────────────────────
# ISF & Stock Day/Swing Simulator (Same as V2)
# ────────────────────────────────────────────────────────────────
def backtest_isf_daily(stock_code, name, K, SL_PCT, TP_PCT, LONG_THRESH, start_date, end_date):
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
            sl_p, tp_p = entry * (1 - SL_PCT/100), entry * (1 + TP_PCT/100)
            if cur['low'] <= sl_p: pnl, reason = (sl_p - entry) * CONTRACT_SIZE * qty, "SL"
            elif cur['high'] >= tp_p: pnl, reason = (tp_p - entry) * CONTRACT_SIZE * qty, "TP"
            else: pnl, reason = (cur['close'] - entry) * CONTRACT_SIZE * qty, "Flat"
            trade_occurred = True
        if trade_occurred:
            running_cap += pnl
            trades.append({'date': cur_date, 'pnl': pnl, 'type': 'LONG', 'reason': reason})
        equity_curve[cur_date] = running_cap

    eq_series = pd.Series(equity_curve).sort_index()
    return trades, eq_series

def simulate_stocks_daily(trading_dates):
    np.random.seed(42)
    capital = CAPITAL_STOCK
    equity_curve = {}
    prob_day, prob_swing = 8 / 21.0, 3 / 21.0
    day_budget, swing_budget = CAPITAL_STOCK * 0.60, CAPITAL_STOCK * 0.40
    day_slot, swing_slot = day_budget / 5, swing_budget / 3

    for d in trading_dates:
        pnl = 0.0
        if np.random.rand() < prob_day:
            is_win = np.random.rand() < 0.60
            pnl += day_slot * ((0.025 if is_win else -0.020) - 0.0025)
        if np.random.rand() < prob_swing:
            is_win = np.random.rand() < 0.55
            pnl += swing_slot * ((0.060 if is_win else -0.030) - 0.0025)
        capital += pnl
        equity_curve[d] = capital
    return pd.Series(equity_curve).sort_index()

# ────────────────────────────────────────────────────────────────
# Main Execution (Tuning scenarios comparison)
# ────────────────────────────────────────────────────────────────
def run_scenario(label, risk, atr_floor, max_contracts, atr_cutoff, start_date, end_date, ss_equity, sk_equity, st_equity):
    k_trades, k_equity = backtest_kospi200_enhanced(start_date, end_date, risk, atr_floor, max_contracts, atr_cutoff)
    futures_equity = CAPITAL_FUTURES + (k_equity - CAPITAL_FUTURES) + (ss_equity - CAPITAL_FUTURES) + (sk_equity - CAPITAL_FUTURES)
    portfolio_equity = futures_equity + st_equity
    
    final_cap = portfolio_equity.iloc[-1]
    net_pnl = final_cap - INITIAL_TOTAL
    total_ret = net_pnl / INITIAL_TOTAL * 100
    mdd = calc_mdd(portfolio_equity)
    cagr = calc_cagr(INITIAL_TOTAL, final_cap, (end_date - start_date).days)
    
    return {
        'label': label, 'trades': len(k_trades), 'final_cap': final_cap,
        'return': total_ret, 'cagr': cagr, 'mdd': mdd
    }

def main():
    print("==========================================================")
    print("  AMATS 초저변동성 구간 거래 필터링(ATR Cutoff) 성과 비교")
    print("==========================================================")
    
    start_date = datetime(2025, 3, 11)
    end_date = datetime(2026, 6, 2)
    
    print("정적 컴포넌트 계산 중...")
    _, ss_equity = backtest_isf_daily('005930', '삼성전자', K=0.35, SL_PCT=2.0, TP_PCT=2.5, LONG_THRESH=0.01, start_date=start_date, end_date=end_date)
    _, sk_equity = backtest_isf_daily('000660', 'SK하이닉스', K=0.18, SL_PCT=1.2, TP_PCT=2.0, LONG_THRESH=0.03, start_date=start_date, end_date=end_date)
    
    conn = sqlite3.connect(DB_FUTURES)
    dates_df = pd.read_sql_query("SELECT DISTINCT date FROM futures_ohlcv WHERE code='10500000'", conn)
    conn.close()
    dates_df['date'] = pd.to_datetime(dates_df['date'], format='%Y%m%d%H%M%S')
    trading_dates = sorted(dates_df[(dates_df['date'] >= start_date) & (dates_df['date'] <= end_date)]['date'].dt.date.unique())
    
    ss_equity = ss_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    sk_equity = sk_equity.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    st_equity = simulate_stocks_daily(trading_dates)
    
    scenarios = [
        # (Label, Risk, ATR Floor, Max Contracts, ATR Cutoff)
        ('Baseline (Risk 1.5%, No Cutoff)', 0.015, None, None, None),
        ('ATR Cutoff 0.5pt (Risk 1.5%)', 0.015, None, None, 0.5),
        ('ATR Cutoff 1.0pt (Risk 1.5%)', 0.015, None, None, 1.0),
        ('ATR Cutoff 1.5pt (Risk 1.5%)', 0.015, None, None, 1.5),
        
        # Test under safer 0.5% risk
        ('Baseline (Risk 0.5%, No Cutoff)', 0.005, None, None, None),
        ('ATR Cutoff 0.5pt (Risk 0.5%)', 0.005, None, None, 0.5),
        ('ATR Cutoff 1.0pt (Risk 0.5%)', 0.005, None, None, 1.0),
        ('ATR Cutoff 1.5pt (Risk 0.5%)', 0.005, None, None, 1.5),
        
        # Joint tuning with ATR Floor
        ('Risk 0.5% + ATR Floor 1.0pt (No Cutoff)', 0.005, 1.0, 10, None),
        ('Risk 0.5% + ATR Floor 1.0pt + ATR Cutoff 0.5pt', 0.005, 1.0, 10, 0.5),
        ('Risk 0.5% + ATR Floor 1.0pt + ATR Cutoff 1.0pt', 0.005, 1.0, 10, 1.0),
    ]
    
    results = []
    for label, risk, floor, max_c, cutoff in scenarios:
        res = run_scenario(label, risk, floor, max_c, cutoff, start_date, end_date, ss_equity, sk_equity, st_equity)
        results.append(res)
        
    print("\n" + "="*95)
    print("                              시나리오 성과 비교 요약")
    print("="*95)
    print(f" {'시나리오':<50} | {'거래수':<5} | {'최종자산 (원)':>16} | {'누적수익률':>10} | {'CAGR':>8} | {'MDD':>7}")
    print("-" * 105)
    for r in results:
        print(f" {r['label']:<48} | {r['trades']:<5} | {r['final_cap']:>16,.0f} | {r['return']:>+9.1f}% | {r['cagr']:>+7.1f}% | {r['mdd']:>6.2f}%")
    print("="*95)

if __name__ == "__main__":
    main()
