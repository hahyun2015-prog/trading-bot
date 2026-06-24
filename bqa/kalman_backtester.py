import sqlite3
import pandas as pd
import numpy as np
import os
import sys
import argparse

class KalmanFilter1D:
    def __init__(self, Q=0.0001, R=0.5):
        self.Q = Q
        self.R = R
        self.x = None
        self.P = 1.0

    def update(self, z):
        if self.x is None:
            self.x = z
            return self.x
        self.P = self.P + self.Q
        K = self.P / (self.P + self.R)
        self.x = self.x + K * (z - self.x)
        self.P = (1 - K) * self.P
        return self.x

# Constants
POINT_VALUE  = 250_000
MARGIN_RATE  = 0.10
MARGIN_CAP   = 0.30
SLIP_FEE_PT  = 0.05
INIT_CAPITAL = 50_000_000

def load_futures_data(code='10100000'):
    db_path = "c:\\Antigravity\\AI_T_Agent\\futures_data.db"
    if not os.path.exists(db_path):
        print(f"[-] Database not found at: {db_path}")
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    query = f"SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '{code}' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df['date_day'] = df['date'].str[:8]
        df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        df.set_index('dt', inplace=True)
    return df

def run_production_rolling_k(df):
    # Re-implement the 20-day rolling optimization baseline to run in the same environment
    daily = df.groupby('date_day').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).reset_index()
    
    grouped_days = df.groupby('date_day')
    sorted_days = sorted(grouped_days.groups.keys())
    
    # 20-day rolling optimization to find best K-value for each day
    optimized_k_map = {}
    for i in range(len(sorted_days)):
        day_key = sorted_days[i]
        if i < 20:
            optimized_k_map[day_key] = 0.5
            continue
            
        last_20_days = sorted_days[i-20:i]
        k_candidates = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]
        best_candidate_k = 0.5
        max_profit = float('-inf')
        
        for cand_k in k_candidates:
            total_profit = 0.0
            for day in last_20_days:
                day_candles = grouped_days.get_group(day)
                if day_candles.empty: continue
                
                idx_daily = daily[daily['date_day'] == day].index[0]
                prev_day_row = daily.iloc[idx_daily - 1]
                pr = prev_day_row['high'] - prev_day_row['low']
                if pr <= 0: continue
                
                day_open_val = day_candles.iloc[0]['open']
                tgt_l_val = day_open_val + pr * cand_k
                tgt_s_val = day_open_val - pr * cand_k
                
                pos_val = 0
                entry_val = 0.0
                trade_count_val = 0
                
                for _, r in day_candles.iterrows():
                    c_high, c_low, c_close = r['high'], r['low'], r['close']
                    
                    if r['date'] == day_candles.iloc[-1]['date']:
                        if pos_val != 0:
                            pnl = (c_close - entry_val if pos_val == 1 else entry_val - c_close) - 0.10
                            total_profit += pnl
                        break
                        
                    if pos_val == 1:
                        if c_low <= entry_val - 5.0:
                            total_profit += (-5.0 - 0.10)
                            pos_val = 0
                        elif c_high >= entry_val + 10.0:
                            total_profit += (10.0 - 0.10)
                            pos_val = 0
                    elif pos_val == -1:
                        if c_high >= entry_val + 5.0:
                            total_profit += (-5.0 - 0.10)
                            pos_val = 0
                        elif c_low <= entry_val - 10.0:
                            total_profit += (10.0 - 0.10)
                            pos_val = 0
                    else:
                        if trade_count_val < 4:
                            if c_high >= tgt_l_val:
                                pos_val = 1
                                entry_val = tgt_l_val
                                trade_count_val += 1
                            elif c_low <= tgt_s_val:
                                pos_val = -1
                                entry_val = tgt_s_val
                                trade_count_val += 1
                                
            if total_profit > max_profit:
                max_profit = total_profit
                best_candidate_k = cand_k
                
        optimized_k_map[day_key] = best_candidate_k

    # Backtest Execution
    cap = float(INIT_CAPITAL)
    equity = [cap]
    pnls = []
    wins = 0
    
    first_price = df['close'].iloc[0]
    margin_per = first_price * POINT_VALUE * MARGIN_RATE
    safe_budget = INIT_CAPITAL * MARGIN_CAP
    CONTRACTS = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
    
    for i in range(1, len(sorted_days)):
        day_key = sorted_days[i]
        prev_day_key = sorted_days[i-1]
        
        prev_day_data = daily[daily['date_day'] == prev_day_key]
        if prev_day_data.empty: continue
        pr = prev_day_data.iloc[0]['high'] - prev_day_data.iloc[0]['low']
        if pr <= 0: continue
        
        K = optimized_k_map.get(day_key, 0.5)
        day_candles = grouped_days.get_group(day_key).sort_values('date')
        if day_candles.empty: continue
        
        day_open = day_candles.iloc[0]['open']
        tgt_l = day_open + pr * K
        tgt_s = day_open - pr * K
        
        pos = 0
        entry_price = 0.0
        trade_count = 0
        
        for idx, row in day_candles.iterrows():
            c_high, c_low, c_close = row['high'], row['low'], row['close']
            is_last = (idx == day_candles.index[-1])
            
            if is_last:
                if pos != 0:
                    exit_p = c_close - SLIP_FEE_PT if pos == 1 else c_close + SLIP_FEE_PT
                    gain = ((exit_p - entry_price) * pos - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                break
                
            if pos == 1:
                if c_low <= entry_price - 5.0: # Stop Loss 5pt
                    exit_p = entry_price - 5.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                elif c_high >= entry_price + 10.0: # Take Profit 10pt
                    exit_p = entry_price + 10.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            elif pos == -1:
                if c_high >= entry_price + 5.0: # Stop Loss 5pt
                    exit_p = entry_price + 5.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                elif c_low <= entry_price - 10.0: # Take Profit 10pt
                    exit_p = entry_price - 10.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            else:
                if trade_count < 4:
                    if c_high >= tgt_l:
                        pos = 1
                        entry_price = tgt_l + SLIP_FEE_PT
                        trade_count += 1
                    elif c_low <= tgt_s:
                        pos = -1
                        entry_price = tgt_s - SLIP_FEE_PT
                        trade_count += 1

    total = len(pnls)
    if total == 0: return None
    equity = np.array(equity)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (peaks - equity) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0
    
    return {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf}

def run_kalman_breakout_fair(df, Q=0.0001, R=0.5, multiplier=1.0):
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    # Run Kalman Filter
    kf = KalmanFilter1D(Q=Q, R=R)
    kf_prices = np.zeros(len(closes))
    for i in range(len(closes)):
        kf_prices[i] = kf.update(closes[i])
        
    df['kf'] = kf_prices
    df['error'] = df['close'] - df['kf']
    df['std_error'] = df['error'].rolling(window=20).std()
    df['std_error'] = df['std_error'].fillna(0.5)
    
    df['band'] = df['std_error'] * multiplier
    df['upper_band'] = df['kf'] + df['band']
    df['lower_band'] = df['kf'] - df['band']
    
    upper_bands = df['upper_band'].values
    lower_bands = df['lower_band'].values
    
    # Backtest Execution
    cap = float(INIT_CAPITAL)
    equity = [cap]
    pnls = []
    wins = 0
    
    first_price = df['close'].iloc[0]
    margin_per = first_price * POINT_VALUE * MARGIN_RATE
    safe_budget = INIT_CAPITAL * MARGIN_CAP
    CONTRACTS = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
    
    grouped_days = df.groupby('date_day')
    sorted_days = sorted(grouped_days.groups.keys())
    
    for day in sorted_days:
        day_candles = grouped_days.get_group(day).sort_values('date')
        if len(day_candles) < 5:
            continue
            
        pos = 0
        entry_price = 0.0
        trade_count = 0
        
        for idx, row in day_candles.iterrows():
            loc = df.index.get_loc(idx)
            c_high, c_low, c_close = row['high'], row['low'], row['close']
            c_upper, c_lower = upper_bands[loc], lower_bands[loc]
            is_last = (idx == day_candles.index[-1])
            
            if is_last:
                if pos != 0:
                    exit_p = c_close - SLIP_FEE_PT if pos == 1 else c_close + SLIP_FEE_PT
                    gain = ((exit_p - entry_price) * pos - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                break
                
            if pos == 1:
                # Stop Loss 5.0pt
                if c_low <= entry_price - 5.0:
                    exit_p = entry_price - 5.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                # Take Profit 10.0pt
                elif c_high >= entry_price + 10.0:
                    exit_p = entry_price + 10.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            elif pos == -1:
                # Stop Loss 5.0pt
                if c_high >= entry_price + 5.0:
                    exit_p = entry_price + 5.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                # Take Profit 10.0pt
                elif c_low <= entry_price - 10.0:
                    exit_p = entry_price - 10.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            else:
                if trade_count < 4:
                    if c_high >= c_upper:
                        pos = 1
                        entry_price = c_upper + SLIP_FEE_PT
                        trade_count += 1
                    elif c_low <= c_lower:
                        pos = -1
                        entry_price = c_lower - SLIP_FEE_PT
                        trade_count += 1

    total = len(pnls)
    if total == 0: return None
    equity = np.array(equity)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (peaks - equity) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0
    
    return {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Kalman Filter Strategy Backtester")
    parser.add_argument('--code', type=str, default='10100000', help="Futures code to backtest (default: 10100000)")
    parser.add_argument('--q', type=float, default=0.0001, help="Kalman Filter process variance Q (default: 0.0001)")
    parser.add_argument('--r', type=float, default=0.5, help="Kalman Filter measurement variance R (default: 0.5)")
    parser.add_argument('--mult', type=float, default=1.0, help="Kalman Band Multiplier (default: 1.0)")
    parser.add_argument('--compare', action='store_true', help="Compare with production baseline")
    
    args = parser.parse_args()
    
    df = load_futures_data(args.code)
    if df.empty:
        sys.exit(1)
        
    print(f"[*] Loaded {len(df)} candles for code {args.code}")
    print(f"[*] Running Kalman Filter Strategy backtest (Q={args.q}, R={args.r}, Mult={args.mult})...")
    res_kalman = run_kalman_breakout_fair(df.copy(), Q=args.q, R=args.r, multiplier=args.mult)
    
    if res_kalman is None:
        print("[-] No trades executed for Kalman Filter Strategy.")
        sys.exit(0)
        
    print("\n" + "="*80)
    print(f"BACKTEST RESULTS FOR CODE {args.code} (Q={args.q}, R={args.r}, Mult={args.mult})")
    print("="*80)
    print(f"Kalman Filter Strategy:")
    print(f"   - Trades: {res_kalman['trades']} | Win Rate: {res_kalman['win_rate']:.2f}% | Profit Factor: {res_kalman['pf']:.2f}")
    print(f"   - Return: {res_kalman['profit_pct']:+.2f}% | MDD: {res_kalman['mdd']:.2f}%")
    print("="*80)
    
    if args.compare:
        print("[*] Running production baseline strategy backtest...")
        res_prod = run_production_rolling_k(df.copy())
        if res_prod is not None:
            print("\n" + "="*80)
            print("COMPARISON WITH PRODUCTION BASELINE")
            print("="*80)
            print("1) Production Rolling K Strategy:")
            print(f"   - Trades: {res_prod['trades']} | Win Rate: {res_prod['win_rate']:.2f}% | PF: {res_prod['pf']:.2f}")
            print(f"   - Return: {res_prod['profit_pct']:+.2f}% | MDD: {res_prod['mdd']:.2f}%")
            print()
            print("2) Kalman Filter Strategy:")
            print(f"   - Trades: {res_kalman['trades']} | Win Rate: {res_kalman['win_rate']:.2f}% | PF: {res_kalman['pf']:.2f}")
            print(f"   - Return: {res_kalman['profit_pct']:+.2f}% | MDD: {res_kalman['mdd']:.2f}%")
            print("="*80)
