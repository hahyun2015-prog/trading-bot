import sqlite3
import pandas as pd
import numpy as np
from strategy_engine import calculate_vwap, apply_rsi, check_hidden_bullish_divergence, check_vwap_pullback

def load_all_intraday_data(conn):
    query = "SELECT DISTINCT code FROM intraday_ohlcv"
    cursor = conn.cursor()
    cursor.execute(query)
    codes = [row[0] for row in cursor.fetchall()]
    return codes

def load_intraday_data(code, conn):
    query = f"SELECT date, open, high, low, close, volume FROM intraday_ohlcv WHERE code = '{code}' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
    return df

def run_backtest():
    conn = sqlite3.connect("kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        return
        
    FEE_AND_TAX = 0.0025
    
    total_trades = 0
    winning_trades = 0
    total_profit_pct = 0.0

    for code in codes:
        df = load_intraday_data(code, conn)
        if len(df) < 60:
            continue
            
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        
        in_pos = False
        buy_price = 0
        super_trend_mode = False
        
        for i in range(50, len(df)):
            current_row = df.iloc[i]
            prev_row = df.iloc[i-1]
            
            if not in_pos:
                window_df = df.iloc[:i+1]
                is_pullback = check_vwap_pullback(window_df)
                is_divergence = check_hidden_bullish_divergence(window_df)
                is_uptrend = current_row['close'] >= (current_row['ma_10'] * 0.99)
                
                if is_pullback and is_divergence and is_uptrend:
                    in_pos = True
                    buy_price = current_row['close']
                    super_trend_mode = False
            else:
                profit_ratio = (current_row['close'] - buy_price) / buy_price
                ma_10_is_up = current_row['ma_10'] > prev_row['ma_10']
                
                exit_signal = False
                
                # 1. 고정 손절선 (-2%)
                if profit_ratio <= -0.02:
                    exit_signal = True
                else:
                    if super_trend_mode:
                        # 2. 수익 극대화 모드 중: 10이평선 이탈 또는 +1.5% 마지노선 이탈 시 익절
                        if current_row['close'] < current_row['ma_10'] or profit_ratio <= 0.015:
                            exit_signal = True
                    else:
                        # 3. +3% 목표가 도달 시
                        if profit_ratio >= 0.03:
                            # 10선이 상향 중이고 종가가 10선 위에 있으면 익절 보류(수익 극대화 진입)
                            if ma_10_is_up and current_row['close'] >= current_row['ma_10']:
                                super_trend_mode = True
                            else:
                                exit_signal = True # 즉시 +3% 익절
                                
                if exit_signal:
                    net_profit = profit_ratio - FEE_AND_TAX
                    total_profit_pct += (net_profit * 100)
                    total_trades += 1
                    if net_profit > 0:
                        winning_trades += 1
                    in_pos = False

    conn.close()
    
    print("===================================")
    print(" [최종 테스트] 10MA 수익극대화 + 1.5% 최소 보장")
    print("===================================")
    print(f"매매횟수: {total_trades}회")
    if total_trades > 0:
        print(f"승률: {(winning_trades/total_trades)*100:.1f}%")
        print(f"누적 수익률: {total_profit_pct:+.2f}%")

if __name__ == "__main__":
    run_backtest()
