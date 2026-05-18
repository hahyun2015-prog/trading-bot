import sqlite3
import pandas as pd
import numpy as np
import ta
import sys
import warnings
warnings.filterwarnings("ignore")

def load_data():
    conn = sqlite3.connect("futures_data.db")
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.drop_duplicates(subset=['date'], keep='last', inplace=True)
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
    return df

def prepare_hama(df, period=10):
    # 1. EMA of OHLC
    df['ema_o'] = df['open'].ewm(span=period, adjust=False).mean()
    df['ema_h'] = df['high'].ewm(span=period, adjust=False).mean()
    df['ema_l'] = df['low'].ewm(span=period, adjust=False).mean()
    df['ema_c'] = df['close'].ewm(span=period, adjust=False).mean()

    # 2. Heikin Ashi of EMAs
    hama_close = (df['ema_o'] + df['ema_h'] + df['ema_l'] + df['ema_c']) / 4
    
    hama_open = np.zeros(len(df))
    hama_open[0] = (df['ema_o'].iloc[0] + df['ema_c'].iloc[0]) / 2
    
    # Calculate HAMA Open sequentially
    hc_vals = hama_close.values
    ho_vals = hama_open
    for i in range(1, len(df)):
        ho_vals[i] = (ho_vals[i-1] + hc_vals[i-1]) / 2
        
    df['hama_c'] = hama_close
    df['hama_o'] = ho_vals
    
    # Color: True if Blue (Uptrend), False if Red (Downtrend)
    df['hama_trend'] = df['hama_c'] > df['hama_o']
    
    return df

def run_hama_simulation(df, config_name, stop_loss_pt=1.0):
    POINT_VALUE = 250000 
    INITIAL_CAPITAL = 50000000
    current_capital = INITIAL_CAPITAL
    position = 0 
    entry_price = 0.0
    total_trades = 0
    winning_trades = 0
    total_profit_pts = 0.0
    
    for i in range(20, len(df)):
        curr_row = df.iloc[i]
        prev_row = df.iloc[i-1]
        current_price = curr_row['close']
        current_time = df.index[i]
        
        is_closing_time = False
        if current_time.hour == 15 and current_time.minute >= 30: is_closing_time = True
        elif current_time.hour == 4 and current_time.minute >= 50: is_closing_time = True
            
        trend_blue = curr_row['hama_trend']
        trend_red = not trend_blue
        prev_trend_blue = prev_row['hama_trend']
        
        signal_changed_to_blue = trend_blue and not prev_trend_blue
        signal_changed_to_red = trend_red and prev_trend_blue
        
        if position != 0:
            exit_signal = False
            profit_pt = 0.0
            
            if position == 1:
                current_profit = current_price - entry_price
                if current_price <= (entry_price - stop_loss_pt):
                    profit_pt = current_profit
                    exit_signal = True
                elif is_closing_time:
                    profit_pt = current_profit
                    exit_signal = True
                elif signal_changed_to_red: # Opposite color
                    profit_pt = current_profit
                    exit_signal = True
                    
            elif position == -1:
                current_profit = entry_price - current_price
                if current_price >= (entry_price + stop_loss_pt):
                    profit_pt = current_profit
                    exit_signal = True
                elif is_closing_time:
                    profit_pt = current_profit
                    exit_signal = True
                elif signal_changed_to_blue: # Opposite color
                    profit_pt = current_profit
                    exit_signal = True
                    
            if exit_signal:
                actual_profit = profit_pt - 0.05
                total_profit_pts += actual_profit
                current_capital += actual_profit * POINT_VALUE
                total_trades += 1
                if actual_profit > 0: winning_trades += 1
                position = 0
                
        # Entry
        if position == 0 and not is_closing_time:
            if signal_changed_to_blue:
                position = 1
                entry_price = current_price
            elif signal_changed_to_red:
                position = -1
                entry_price = current_price
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    return {'name': config_name, 'trades': total_trades, 'win_rate': win_rate, 'profit_pts': total_profit_pts, 'profit_pct': profit_pct}

def main():
    print("HAMA KOSPI 200 데이터 로딩 중...")
    df = load_data()
    df = prepare_hama(df, period=10)
    
    res1 = run_hama_simulation(df, "1. HAMA 10 (손절 -1.0pt)", stop_loss_pt=1.0)
    res2 = run_hama_simulation(df, "2. HAMA 10 (손절 -2.0pt)", stop_loss_pt=2.0)
    
    # 하프 HAMA (period 5)
    df2 = load_data()
    df2 = prepare_hama(df2, period=5)
    res3 = run_hama_simulation(df2, "3. HAMA 5 (민감형, -1pt)", stop_loss_pt=1.0)
    
    results = [res1, res2, res3]
    print("\n=========================================================================")
    print(f"{'전략 구성 (HAMA 추세추종)':<35} | {'매매횟수':<6} | {'승률':<6} | {'누적수익(pt)':<10} | {'자본수익률(%)'}")
    print("-" * 73)
    for r in results:
        print(f"{r['name']:<35} | {r['trades']:<8} | {r['win_rate']:<7.1f}% | {r['profit_pts']:<13.2f} | {r['profit_pct']:<10.2f}%")
    print("=========================================================================")

if __name__ == "__main__":
    main()
