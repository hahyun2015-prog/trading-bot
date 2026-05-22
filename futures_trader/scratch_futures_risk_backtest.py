import sqlite3
import pandas as pd
import numpy as np
import ta
import sys
import warnings
warnings.filterwarnings("ignore")

def load_data():
    conn = sqlite3.connect("futures_data.db")
    # 주야간 데이터를 모두 불러와서 시간순으로 정렬
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.drop_duplicates(subset=['date'], keep='last', inplace=True) # 중복 시간 캔들 제거 (통합)
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
    return df

def prepare_indicators(df):
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_m'] = indicator_bb.bollinger_mavg()
    df['bb_h'] = indicator_bb.bollinger_hband()
    df['bb_l'] = indicator_bb.bollinger_lband()
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    return df

def run_simulation(df, config_name, daily_loss_limit=None, profit_target_type='bb_m', fixed_tp=0.0):
    POINT_VALUE = 250000 
    INITIAL_CAPITAL = 50000000
    
    current_capital = INITIAL_CAPITAL
    position = 0 
    entry_price = 0.0
    
    total_trades = 0
    winning_trades = 0
    total_profit_pts = 0.0
    
    STOP_LOSS_PT = 1.0
    
    daily_profit_pts = 0.0
    halted_today = False
    current_day = None

    for i in range(60, len(df)):
        curr_row = df.iloc[i]
        current_price = curr_row['close']
        current_time = df.index[i]
        
        day_str = current_time.strftime('%Y-%m-%d')
        if current_day != day_str:
            current_day = day_str
            daily_profit_pts = 0.0
            halted_today = False
            
        is_closing_time = False
        if current_time.hour == 15 and current_time.minute >= 30: is_closing_time = True
        elif current_time.hour == 4 and current_time.minute >= 50: is_closing_time = True
            
        if position != 0:
            exit_signal = False
            profit_pt = 0.0
            
            if position == 1:
                current_profit = current_price - entry_price
                if current_price <= (entry_price - STOP_LOSS_PT):
                    profit_pt = current_profit
                    exit_signal = True
                elif is_closing_time:
                    profit_pt = current_profit
                    exit_signal = True
                else:
                    if profit_target_type == 'bb_m' and current_price >= curr_row['bb_m']:
                        profit_pt = current_profit
                        exit_signal = True
                    elif profit_target_type == 'bb_opposite' and current_price >= curr_row['bb_h']:
                        profit_pt = current_profit
                        exit_signal = True
                    elif profit_target_type == 'fixed' and current_profit >= fixed_tp:
                        profit_pt = current_profit
                        exit_signal = True
                    
            elif position == -1:
                current_profit = entry_price - current_price
                if current_price >= (entry_price + STOP_LOSS_PT):
                    profit_pt = current_profit
                    exit_signal = True
                elif is_closing_time:
                    profit_pt = current_profit
                    exit_signal = True
                else:
                    if profit_target_type == 'bb_m' and current_price <= curr_row['bb_m']:
                        profit_pt = current_profit
                        exit_signal = True
                    elif profit_target_type == 'bb_opposite' and current_price <= curr_row['bb_l']:
                        profit_pt = current_profit
                        exit_signal = True
                    elif profit_target_type == 'fixed' and current_profit >= fixed_tp:
                        profit_pt = current_profit
                        exit_signal = True
                    
            if exit_signal:
                # Deduct comm/slippage roughly 0.05pt
                actual_profit = profit_pt - 0.05
                total_profit_pts += actual_profit
                daily_profit_pts += actual_profit
                
                current_capital += actual_profit * POINT_VALUE
                total_trades += 1
                if actual_profit > 0:
                    winning_trades += 1
                position = 0
                
                if daily_loss_limit is not None and daily_profit_pts <= daily_loss_limit:
                    halted_today = True
                continue
                
        if position == 0 and not is_closing_time and not halted_today:
            long_cond = curr_row['low'] <= curr_row['bb_l'] and curr_row['rsi'] <= 30
            short_cond = curr_row['high'] >= curr_row['bb_h'] and curr_row['rsi'] >= 70
            
            if long_cond:
                position = 1
                entry_price = current_price
            elif short_cond:
                position = -1
                entry_price = current_price
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    return {
        'name': config_name,
        'trades': total_trades,
        'win_rate': win_rate,
        'profit_pts': total_profit_pts,
        'profit_pct': profit_pct
    }

def main():
    print("선물 KOSPI 200 데이터 로딩 중...")
    df = load_data()
    df = prepare_indicators(df)
    
    res1 = run_simulation(df, "1. 원본 (중심선 익절)", profit_target_type='bb_m')
    res2 = run_simulation(df, "2. 반대편 밴드 도달 시 익절", profit_target_type='bb_opposite')
    res3 = run_simulation(df, "3. 고정 익절 (+2.0pt / -1.0pt)", profit_target_type='fixed', fixed_tp=2.0)
    res4 = run_simulation(df, "4. 고정 익절 (+3.0pt / -1.0pt)", profit_target_type='fixed', fixed_tp=3.0)
    
    results = [res1, res2, res3, res4]
    
    print("\n=========================================================================")
    print(f"{'전략 구성 (선물)':<35} | {'매매횟수':<6} | {'승률':<6} | {'누적수익(pt)':<10} | {'자본수익률(%)'}")
    print("-" * 73)
    for r in results:
        print(f"{r['name']:<35} | {r['trades']:<8} | {r['win_rate']:<7.1f}% | {r['profit_pts']:<13.2f} | {r['profit_pct']:<10.2f}%")
    print("=========================================================================")

if __name__ == "__main__":
    main()
