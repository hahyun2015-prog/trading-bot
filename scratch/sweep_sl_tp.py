import sqlite3
import pandas as pd
import numpy as np

def load_data():
    conn = sqlite3.connect("futures_data.db")
    # Query data for code '10500000' (Mini KOSPI 200 Futures)
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10500000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
    return df

def run_backtest(df, K, SL_pt, TP_pt):
    df = df.copy()
    df['date_only'] = df.index.date
    daily_stats = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily_stats['range'] = daily_stats['high'] - daily_stats['low']
    daily_stats['prev_range'] = daily_stats['range'].shift(1)
    
    df = df.join(daily_stats[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df.rename(columns={'open_day': 'day_open'}, inplace=True)
    
    POINT_VALUE = 250000 
    INITIAL_CAPITAL = 50000000
    current_capital = INITIAL_CAPITAL
    
    position = 0 # 1 = LONG, -1 = SHORT
    entry_price = 0.0
    contracts = 1
    
    total_trades = 0
    winning_trades = 0
    equity_curve = [INITIAL_CAPITAL]
    
    # Simple simulation loop
    for i in range(len(df)):
        curr_row = df.iloc[i]
        current_time = df.index[i]
        current_price = curr_row['close']
        h, l = curr_row['high'], curr_row['low']
        
        day_open = curr_row['day_open']
        prev_range = curr_row['prev_range']
        
        if pd.isna(prev_range):
            continue
            
        target_price_long = day_open + (prev_range * K)
        target_price_short = day_open - (prev_range * K)
        
        is_morning_open = False
        if current_time.hour == 8 and 45 <= current_time.minute <= 50:
            is_morning_open = True
            
        # 1. Check Exit
        if position != 0:
            exit_hit = False
            exit_price = 0.0
            
            # Check forced morning exit
            if is_morning_open:
                exit_price = current_price
                exit_hit = True
            else:
                if position == 1:
                    # Check SL
                    if l <= entry_price - SL_pt:
                        exit_price = entry_price - SL_pt
                        exit_hit = True
                    # Check TP
                    elif h >= entry_price + TP_pt:
                        exit_price = entry_price + TP_pt
                        exit_hit = True
                elif position == -1:
                    # Check SL
                    if h >= entry_price + SL_pt:
                        exit_price = entry_price + SL_pt
                        exit_hit = True
                    # Check TP
                    elif l <= entry_price - TP_pt:
                        exit_price = entry_price - TP_pt
                        exit_hit = True
            
            if exit_hit:
                # Add slippage 0.05pt
                if position == 1:
                    exit_price_adj = exit_price - 0.05
                    profit_pt = exit_price_adj - entry_price
                else:
                    exit_price_adj = exit_price + 0.05
                    profit_pt = entry_price - exit_price_adj
                
                trade_pnl = profit_pt * POINT_VALUE * contracts
                current_capital += trade_pnl
                equity_curve.append(current_capital)
                total_trades += 1
                if trade_pnl > 0:
                    winning_trades += 1
                position = 0
            continue # Already in trade, skip entry
            
        # 2. Check Entry
        if position == 0 and not is_morning_open:
            if curr_row['high'] >= target_price_long:
                position = 1
                entry_price = target_price_long + 0.05 # slippage entry
                contracts = 1
            elif curr_row['low'] <= target_price_short:
                position = -1
                entry_price = target_price_short - 0.05 # slippage entry
                contracts = 1
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    # Calculate MDD
    peak = INITIAL_CAPITAL
    mdd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > mdd:
            mdd = dd
            
    return {
        'SL': SL_pt,
        'TP': TP_pt,
        'trades': total_trades,
        'win_rate': round(win_rate, 2),
        'profit_pct': round(profit_pct, 2),
        'mdd': round(mdd, 2)
    }

def main():
    df = load_data()
    if df.empty:
        print("No data loaded")
        return
        
    print(f"Data range: {df.index[0]} ~ {df.index[-1]}")
    
    # Sweep SL: 1.0 to 4.0, TP: 2.0 to 8.0
    sl_vals = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    tp_vals = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0]
    
    results = []
    for sl in sl_vals:
        for tp in tp_vals:
            res = run_backtest(df, K=0.10, SL_pt=sl, TP_pt=tp)
            results.append(res)
            
    res_df = pd.DataFrame(results)
    # Sort by profit_pct descending
    res_df = res_df.sort_values(by='profit_pct', ascending=False)
    
    print("SL\tTP\tTrades\tWinRate\tProfit%\tMDD%")
    for idx, r in res_df.iterrows():
        print(f"{r['SL']:.1f}\t{r['TP']:.1f}\t{int(r['trades'])}\t{r['win_rate']:.2f}%\t{r['profit_pct']:.2f}%\t{r['mdd']:.2f}%")

if __name__ == '__main__':
    main()
