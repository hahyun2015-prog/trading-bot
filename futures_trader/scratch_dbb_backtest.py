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

def prepare_dbb(df):
    indicator_bb2 = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_m'] = indicator_bb2.bollinger_mavg()
    df['bb_h2'] = indicator_bb2.bollinger_hband()
    df['bb_l2'] = indicator_bb2.bollinger_lband()
    
    indicator_bb1 = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=1)
    df['bb_h1'] = indicator_bb1.bollinger_hband()
    df['bb_l1'] = indicator_bb1.bollinger_lband()
    
    return df

def run_dbb_simulation(df, config_name, strategy_type='trend', stop_loss_pt=1.0, fixed_tp=0.0):
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
                else:
                    if strategy_type == 'trend' and current_price < curr_row['bb_h1']:
                        profit_pt = current_profit
                        exit_signal = True
                    elif strategy_type == 'reversion' and fixed_tp > 0 and current_profit >= fixed_tp:
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
                else:
                    if strategy_type == 'trend' and current_price > curr_row['bb_l1']:
                        profit_pt = current_profit
                        exit_signal = True
                    elif strategy_type == 'reversion' and fixed_tp > 0 and current_profit >= fixed_tp:
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
            if strategy_type == 'trend':
                # Enter Long if price crosses above Upper 1SD
                if current_price >= curr_row['bb_h1'] and prev_row['close'] < prev_row['bb_h1']:
                    position = 1
                    entry_price = current_price
                # Enter Short if price crosses below Lower 1SD
                elif current_price <= curr_row['bb_l1'] and prev_row['close'] > prev_row['bb_l1']:
                    position = -1
                    entry_price = current_price
            
            elif strategy_type == 'reversion':
                # Enter Long if price drops below 2SD but closes above 2SD (Extreme Rejection)
                if curr_row['low'] <= curr_row['bb_l2'] and current_price > curr_row['bb_l2']:
                    position = 1
                    entry_price = current_price
                # Enter Short if price spikes above 2SD but closes below 2SD
                elif curr_row['high'] >= curr_row['bb_h2'] and current_price < curr_row['bb_h2']:
                    position = -1
                    entry_price = current_price
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    return {'name': config_name, 'trades': total_trades, 'win_rate': win_rate, 'profit_pts': total_profit_pts, 'profit_pct': profit_pct}

def main():
    print("DBB 데이터 로딩 중...")
    df = load_data()
    df = prepare_dbb(df)
    
    res1 = run_dbb_simulation(df, "1. DBB 추세추종 (손절 -1pt)", strategy_type='trend', stop_loss_pt=1.0)
    res2 = run_dbb_simulation(df, "2. DBB 역추세 (고정익절 2pt)", strategy_type='reversion', stop_loss_pt=1.0, fixed_tp=2.0)
    res3 = run_dbb_simulation(df, "3. DBB 역추세 (고정익절 3pt)", strategy_type='reversion', stop_loss_pt=1.0, fixed_tp=3.0)
    
    results = [res1, res2, res3]
    print("\n=========================================================================")
    print(f"{'전략 구성 (더블 볼린저 밴드)':<35} | {'매매횟수':<6} | {'승률':<6} | {'누적수익(pt)':<10} | {'자본수익률(%)'}")
    print("-" * 73)
    for r in results:
        print(f"{r['name']:<35} | {r['trades']:<8} | {r['win_rate']:<7.1f}% | {r['profit_pts']:<13.2f} | {r['profit_pct']:<10.2f}%")
    print("=========================================================================")

if __name__ == "__main__":
    main()
