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

def run_volatility_breakout(df, config_name, k=0.5, is_short_enabled=False, stop_loss_pt=None, overnight=False):
    POINT_VALUE = 250000 
    INITIAL_CAPITAL = 50000000
    current_capital = INITIAL_CAPITAL
    position = 0 
    entry_price = 0.0
    total_trades = 0
    winning_trades = 0
    total_profit_pts = 0.0
    
    # Calculate daily range
    # Since we have Day/Night data, "Day" means a trading session.
    # Kiwoom dates are standard, but let's just group by calendar day.
    df['date_only'] = df.index.date
    daily_stats = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily_stats['range'] = daily_stats['high'] - daily_stats['low']
    daily_stats['prev_range'] = daily_stats['range'].shift(1)
    
    df = df.join(daily_stats[['prev_range']], on='date_only')
    
    current_day = None
    day_open = 0.0
    entry_day = None
    
    for i in range(1, len(df)):
        curr_row = df.iloc[i]
        current_price = curr_row['close']
        current_time = df.index[i]
        date_only = curr_row['date_only']
        
        if pd.isna(curr_row['prev_range']):
            continue
            
        if current_day != date_only:
            current_day = date_only
            day_open = curr_row['open']
            
        is_closing_time = False
        if current_time.hour == 15 and current_time.minute >= 30: is_closing_time = True
        elif current_time.hour == 4 and current_time.minute >= 50: is_closing_time = True
            
        target_price_long = day_open + (curr_row['prev_range'] * k)
        target_price_short = day_open - (curr_row['prev_range'] * k)
        
        if position != 0:
            exit_signal = False
            profit_pt = 0.0
            
            if position == 1:
                current_profit = current_price - entry_price
                if stop_loss_pt is not None and current_price <= entry_price - stop_loss_pt:
                    profit_pt = current_profit
                    exit_signal = True
                elif not overnight and is_closing_time:
                    profit_pt = current_profit
                    exit_signal = True
                elif overnight == 'next_open' and entry_day != current_day:
                    # Exit at the open of the next day
                    profit_pt = day_open - entry_price
                    exit_signal = True
                elif overnight == 'sar' and curr_row['low'] <= target_price_short:
                    profit_pt = target_price_short - entry_price
                    exit_signal = True
            elif position == -1:
                current_profit = entry_price - current_price
                if stop_loss_pt is not None and current_price >= entry_price + stop_loss_pt:
                    profit_pt = current_profit
                    exit_signal = True
                elif not overnight and is_closing_time:
                    profit_pt = current_profit
                    exit_signal = True
                elif overnight == 'next_open' and entry_day != current_day:
                    profit_pt = entry_price - day_open
                    exit_signal = True
                elif overnight == 'sar' and curr_row['high'] >= target_price_long:
                    profit_pt = entry_price - target_price_long
                    exit_signal = True

            if exit_signal:
                actual_profit = profit_pt - 0.05 # Slippage
                total_profit_pts += actual_profit
                current_capital += actual_profit * POINT_VALUE
                total_trades += 1
                if actual_profit > 0: winning_trades += 1
                position = 0
        
        if position == 0 and not is_closing_time:
            # Check for Long breakout
            if curr_row['high'] >= target_price_long:
                position = 1
                entry_price = target_price_long
                entry_day = current_day
            # Check for Short breakout
            elif is_short_enabled and curr_row['low'] <= target_price_short:
                position = -1
                entry_price = target_price_short
                entry_day = current_day
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    return {'name': config_name, 'trades': total_trades, 'win_rate': win_rate, 'profit_pts': total_profit_pts, 'profit_pct': profit_pct}


def main():
    print("오버나이트 리서치용 데이터 로딩 중...")
    df = load_data()
    
    res1 = run_volatility_breakout(df, "1. 당일청산 (원본 최고수익)", k=0.5, is_short_enabled=True, overnight=False)
    res2 = run_volatility_breakout(df, "2. 오버나이트 (익일 시가 청산)", k=0.5, is_short_enabled=True, overnight='next_open')
    res3 = run_volatility_breakout(df, "3. 오버나이트 (무한 스위칭 SAR)", k=0.5, is_short_enabled=True, overnight='sar')
    
    results = [res1, res2, res3]
    
    print("\n=========================================================================")
    print(f"{'전략 구성 (오버나이트 테스트)':<35} | {'매매횟수':<6} | {'승률':<6} | {'누적수익(pt)':<10} | {'자본수익률(%)'}")
    print("-" * 73)
    for r in results:
        print(f"{r['name']:<35} | {r['trades']:<8} | {r['win_rate']:<7.1f}% | {r['profit_pts']:<13.2f} | {r['profit_pct']:<10.2f}%")
    print("=========================================================================")

if __name__ == "__main__":
    main()
