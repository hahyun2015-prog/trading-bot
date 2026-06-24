import sqlite3
import pandas as pd
import numpy as np
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

def load_data():
    db_path = "futures_data.db"
    if not os.path.exists(db_path):
        print("DB not found")
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    # Load A0567000 which is the active Mini Futures contract containing today's data
    df = pd.read_sql_query("SELECT date, open, high, low, close FROM futures_ohlcv WHERE code = 'A0567000' ORDER BY date ASC", conn)
    conn.close()
    
    df['date_day'] = df['date'].str[:8]
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.drop_duplicates(subset=['date'], inplace=True)
    df.set_index('dt', inplace=True)
    return df

# Kalman Filter class
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

def calculate_tr_and_atr(df):
    grouped = df.groupby('date_day')
    sorted_days = sorted(grouped.groups.keys())
    
    daily_tr = {}
    prev_close = None
    for day in sorted_days:
        day_df = grouped.get_group(day)
        day_high = day_df['high'].max()
        day_low = day_df['low'].min()
        day_close = day_df['close'].iloc[-1]
        
        if prev_close is None:
            tr = day_high - day_low
        else:
            tr = max(day_high - day_low, abs(day_high - prev_close), abs(day_low - prev_close))
        daily_tr[day] = tr
        prev_close = day_close
    return daily_tr, sorted_days

def run_simulation(df, daily_tr, sorted_days, test_days, floor_mode='fixed_2.0', verbose=True):
    grouped = df.groupby('date_day')
    
    if verbose:
        print(f"\n=========================================")
        print(f"  SIMULATION: Floor Mode = {floor_mode}")
        print(f"=========================================")
    
    summary = {}
    
    for day in test_days:
        day_candles = grouped.get_group(day).sort_values('date')
        
        # Calculate 3-day ATR for reference
        day_idx = sorted_days.index(day)
        prev_3 = sorted_days[day_idx-3:day_idx]
        atr3 = np.mean([daily_tr[d] for d in prev_3])
        
        if floor_mode == 'fixed_2.0':
            current_atr = 2.0
            sl_floor = 2.0
            if verbose: print(f"\n[{day}] 3-day ATR: {atr3:.2f} pt | SL Floor: {sl_floor:.2f} pt (Scaled Mode)")
        elif floor_mode == 'volatility_3day':
            current_atr = atr3
            sl_floor = max(min(0.8 * current_atr, 2.0 * current_atr), 2.0)
            if verbose: print(f"\n[{day}] 3-day ATR: {atr3:.2f} pt | SL Floor: {sl_floor:.2f} pt (Scaled Mode)")
        elif floor_mode == 'kalman_static':
            # Run Static Kalman Filter on all daily TR values up to yesterday
            prev_days = sorted_days[:day_idx]
            kf_atr = None
            P_atr = 1.0
            Q_atr = 0.002
            R_atr = 0.2
            for d in prev_days:
                tr_val = daily_tr[d]
                if kf_atr is None:
                    kf_atr = tr_val
                else:
                    P_atr = P_atr + Q_atr
                    K_atr = P_atr / (P_atr + R_atr)
                    kf_atr = kf_atr + K_atr * (tr_val - kf_atr)
                    P_atr = (1 - K_atr) * P_atr
            current_atr = kf_atr
            sl_floor = max(min(0.8 * current_atr, 2.0 * current_atr), 2.0)
            if verbose: print(f"\n[{day}] Static Kalman ATR: {kf_atr:.2f} pt | SL Floor: {sl_floor:.2f} pt (Scaled Mode)")
        elif floor_mode == 'kalman_adaptive':
            # Run Adaptive Kalman Filter on all daily TR values up to yesterday
            prev_days = sorted_days[:day_idx]
            kf_atr_ad = None
            P_atr_ad = 1.0
            history_tr = []
            for d in prev_days:
                tr_val = daily_tr[d]
                history_tr.append(tr_val)
                if len(history_tr) > 14:
                    history_tr.pop(0)
                if kf_atr_ad is None:
                    kf_atr_ad = tr_val
                else:
                    R_ad = np.var(history_tr) if len(history_tr) >= 5 else 0.1
                    if R_ad < 0.01: R_ad = 0.01
                    innovation = tr_val - kf_atr_ad
                    Q_ad = 0.05 * R_ad + 0.1 * (innovation ** 2)
                    P_atr_ad = P_atr_ad + Q_ad
                    K_ad = P_atr_ad / (P_atr_ad + R_ad)
                    kf_atr_ad = kf_atr_ad + K_ad * innovation
                    P_atr_ad = (1 - K_ad) * P_atr_ad
            current_atr = kf_atr_ad
            sl_floor = max(min(0.8 * current_atr, 2.0 * current_atr), 2.0)
            if verbose: print(f"\n[{day}] Adaptive Kalman ATR: {kf_atr_ad:.2f} pt | SL Floor: {sl_floor:.2f} pt (Scaled Mode)")
        
        pos = 0
        entry_price = 0.0
        trade_count = 0
        peak = 0.0
        
        trades_logged = []
        sl_count = 0
        tp_count = 0
        ts_count = 0
        total_pnl = 0.0
        
        # Precompute Kalman for each candle locally (last 40 candles)
        for idx, row in day_candles.iterrows():
            hist = df.loc[df.index <= idx]
            if len(hist) < 40:
                continue
            
            closes_short = hist['close'].tail(40).values
            kf = KalmanFilter1D(Q=0.0001, R=0.5)
            kf_prices = []
            for z in closes_short:
                kf_prices.append(kf.update(z))
            
            kf_price = kf_prices[-1]
            errors = closes_short - np.array(kf_prices)
            std_error = np.std(errors[-20:])
            if pd.isna(std_error) or std_error <= 0:
                std_error = 0.5
                
            c_high = row['high']
            c_low = row['low']
            c_close = row['close']
            time_hhmm = row['date'][8:14]
            
            c_upper = kf_price + std_error * 1.0
            c_lower = kf_price - std_error * 1.0
            
            is_last = (idx == day_candles.index[-1])
            
            if is_last and pos != 0:
                exit_p = c_close
                pnl = (exit_p - entry_price) * pos
                trades_logged.append(f"  - {time_hhmm}: [CLOSE FORCE EXIT] LONG Exit at {exit_p:.2f} | PnL: {pnl:+.2f} pt")
                total_pnl += pnl
                pos = 0
                break
                
            if pos == 1:
                if c_high > peak: peak = c_high
                # Live bot had atr_14 = 2.0, so the cap is 1.2 * 2.0 = 2.40
                sl_limit = max(min(3.4 * std_error, 1.2 * current_atr), sl_floor)
                target_tp = kf_price + 3.0 * std_error
                
                if c_low <= entry_price - sl_limit:
                    pnl = -sl_limit
                    trades_logged.append(f"  - {time_hhmm}: [STOP LOSS] LONG Entry: {entry_price:.2f} -> Exit: {entry_price - sl_limit:.2f} | PnL: {pnl:+.2f} pt (SL: {sl_limit:.2f} pt, std_err: {std_error:.2f})")
                    sl_count += 1
                    total_pnl += pnl
                    pos = 0
                elif c_high >= target_tp:
                    pnl = target_tp - entry_price
                    trades_logged.append(f"  - {time_hhmm}: [TAKE PROFIT] LONG Entry: {entry_price:.2f} -> Exit: {target_tp:.2f} | PnL: {pnl:+.2f} pt")
                    tp_count += 1
                    total_pnl += pnl
                    pos = 0
                elif peak - entry_price >= 1.5 * std_error:
                    ts_price = peak - 0.5 * std_error
                    if c_low <= ts_price:
                        pnl = ts_price - entry_price
                        trades_logged.append(f"  - {time_hhmm}: [TRAILING STOP] LONG Entry: {entry_price:.2f} -> Exit: {ts_price:.2f} | PnL: {pnl:+.2f} pt (Peak: {peak:.2f})")
                        ts_count += 1
                        total_pnl += pnl
                        pos = 0
                        
            elif pos == -1:
                if c_low < peak or peak == 0: peak = c_low
                sl_limit = max(min(3.4 * std_error, 1.2 * current_atr), sl_floor)
                target_tp = kf_price - 3.0 * std_error
                
                if c_high >= entry_price + sl_limit:
                    pnl = -sl_limit
                    trades_logged.append(f"  - {time_hhmm}: [STOP LOSS] SHORT Entry: {entry_price:.2f} -> Exit: {entry_price + sl_limit:.2f} | PnL: {pnl:+.2f} pt (SL: {sl_limit:.2f} pt, std_err: {std_error:.2f})")
                    sl_count += 1
                    total_pnl += pnl
                    pos = 0
                elif c_low <= target_tp:
                    pnl = entry_price - target_tp
                    trades_logged.append(f"  - {time_hhmm}: [TAKE PROFIT] SHORT Entry: {entry_price:.2f} -> Exit: {target_tp:.2f} | PnL: {pnl:+.2f} pt")
                    tp_count += 1
                    total_pnl += pnl
                    pos = 0
                elif entry_price - peak >= 1.5 * std_error:
                    ts_price = peak + 0.5 * std_error
                    if c_high >= ts_price:
                        pnl = entry_price - ts_price
                        trades_logged.append(f"  - {time_hhmm}: [TRAILING STOP] SHORT Entry: {entry_price:.2f} -> Exit: {ts_price:.2f} | PnL: {pnl:+.2f} pt (Peak: {peak:.2f})")
                        ts_count += 1
                        total_pnl += pnl
                        pos = 0
            else:
                # Check entry (raised daily limit to 10)
                if trade_count < 10:
                    if c_high >= c_upper:
                        pos = 1
                        entry_price = c_upper
                        peak = c_high
                        trade_count += 1
                    elif c_low <= c_lower:
                        pos = -1
                        entry_price = c_lower
                        peak = c_low
                        trade_count += 1
                        
        if verbose:
            if not trades_logged:
                print("  No trades executed.")
            else:
                for log in trades_logged:
                    print(log)
        
        summary[day] = {
            'trades': trade_count,
            'sl': sl_count,
            'tp': tp_count,
            'ts': ts_count,
            'pnl': total_pnl
        }
    return summary

def main():
    df = load_data()
    daily_tr, sorted_days = calculate_tr_and_atr(df)
    
    # Simulate all available days (exclude first 15 days to allow ATR calculation)
    test_days = sorted_days[15:]
    
    print(f"Starting simulation for {len(test_days)} days...")
    
    sum_baseline = run_simulation(df, daily_tr, sorted_days, test_days, 'fixed_2.0', verbose=False)
    sum_dynamic = run_simulation(df, daily_tr, sorted_days, test_days, 'volatility_3day', verbose=False)
    sum_kalman_static = run_simulation(df, daily_tr, sorted_days, test_days, 'kalman_static', verbose=False)
    sum_kalman_adaptive = run_simulation(df, daily_tr, sorted_days, test_days, 'kalman_adaptive', verbose=False)
    
    print("\n\n" + "="*80)
    print(f"  SIMULATION COMPARISON SUMMARY ({test_days[0]} ~ {test_days[-1]} | {len(test_days)} Days)")
    print("="*80)
    for name, summary in [("Baseline (SL Floor 2.0)", sum_baseline),
                          ("Dynamic (3d SMA ATR)", sum_dynamic),
                          ("Kalman Static ATR", sum_kalman_static),
                          ("Kalman Adaptive ATR", sum_kalman_adaptive)]:
        total_trades = sum(d['trades'] for d in summary.values())
        total_sl = sum(d['sl'] for d in summary.values())
        total_tp = sum(d['tp'] for d in summary.values())
        total_ts = sum(d['ts'] for d in summary.values())
        total_pnl = sum(d['pnl'] for d in summary.values())
        win_rate = (total_tp + total_ts) / total_trades * 100 if total_trades > 0 else 0
        print(f"  - {name:<25}: Trades={total_trades:<4} SL={total_sl:<3} TP={total_tp:<3} TS={total_ts:<3} WinRate={win_rate:.1f}% | PnL={total_pnl:+.2f} pt")
    print("="*80)

if __name__ == '__main__':
    main()
