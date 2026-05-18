import sqlite3
import pandas as pd
import numpy as np

# 기존 모듈 임포트
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

def run_both_backtests():
    conn = sqlite3.connect("kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        print("No data.")
        return
        
    FEE_AND_TAX = 0.0025
    
    # 결과 저장 변수
    res_A_trades = 0
    res_A_wins = 0
    res_A_profit = 0.0
    
    res_B_trades = 0
    res_B_wins = 0
    res_B_profit = 0.0

    for code in codes:
        df = load_intraday_data(code, conn)
        if len(df) < 60:
            continue
            
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        df['ma_50'] = df['close'].rolling(window=50).mean()
        
        # A안 상태
        in_pos_A = False
        buy_price_A = 0
        
        # B안 상태
        in_pos_B = False
        buy_price_B = 0
        target_B = 0
        stop_B = 0
        
        for i in range(50, len(df)):
            current_row = df.iloc[i]
            current_time = df.index[i]
            
            # 진입 타점 판별 (공통)
            window_df = df.iloc[:i+1]
            is_pullback = check_vwap_pullback(window_df)
            is_divergence = check_hidden_bullish_divergence(window_df)
            is_uptrend = current_row['close'] >= (current_row['ma_10'] * 0.99)
            
            signal_fired = is_pullback and is_divergence and is_uptrend
            
            # A안 (50선 데드크로스)
            if not in_pos_A:
                if signal_fired:
                    in_pos_A = True
                    buy_price_A = current_row['close']
            else:
                # 50선을 종가상 하향 이탈하면 매도 (또는 최소한의 하드스탑 -5% 안전장치)
                if current_row['close'] < current_row['ma_50'] or current_row['close'] <= buy_price_A * 0.95:
                    profit_ratio = (current_row['close'] - buy_price_A) / buy_price_A
                    net_profit = profit_ratio - FEE_AND_TAX
                    res_A_profit += (net_profit * 100)
                    res_A_trades += 1
                    if net_profit > 0:
                        res_A_wins += 1
                    in_pos_A = False
                    
            # B안 (전고점 익절 / 박스하단 손절)
            if not in_pos_B:
                if signal_fired:
                    # 당일 전고점 찾기
                    today_df = window_df[window_df.index.date == current_time.date()]
                    day_high = today_df['high'].max()
                    # 박스 하단 (최근 15봉 최저점 지지선)
                    box_low = window_df.iloc[-15:]['low'].min()
                    
                    # 전고점이 현재가보다 낮거나 같으면(이미 신고가 갱신중이면) 3% 고정 익절 설정
                    if day_high <= current_row['close']:
                        target_B = current_row['close'] * 1.03
                    else:
                        target_B = day_high
                        
                    stop_B = box_low
                    # 만약 지지선이 현재가와 너무 가깝거나 멀면 보정
                    if stop_B >= current_row['close'] * 0.995: 
                        stop_B = current_row['close'] * 0.99
                        
                    buy_price_B = current_row['close']
                    in_pos_B = True
            else:
                # 목표가 도달 또는 지지선 이탈 시 청산
                if current_row['close'] >= target_B or current_row['close'] <= stop_B:
                    profit_ratio = (current_row['close'] - buy_price_B) / buy_price_B
                    net_profit = profit_ratio - FEE_AND_TAX
                    res_B_profit += (net_profit * 100)
                    res_B_trades += 1
                    if net_profit > 0:
                        res_B_wins += 1
                    in_pos_B = False

    conn.close()
    
    print("===================================")
    print(" [A안] 50MA 데드크로스 기반 추세추종")
    print("===================================")
    print(f"매매횟수: {res_A_trades}회")
    if res_A_trades > 0:
        print(f"승률: {(res_A_wins/res_A_trades)*100:.1f}%")
        print(f"누적 수익률: {res_A_profit:+.2f}%")
        
    print("\n===================================")
    print(" [B안] 전고점 익절 / 지지선 손절 박스권매매")
    print("===================================")
    print(f"매매횟수: {res_B_trades}회")
    if res_B_trades > 0:
        print(f"승률: {(res_B_wins/res_B_trades)*100:.1f}%")
        print(f"누적 수익률: {res_B_profit:+.2f}%")

if __name__ == "__main__":
    run_both_backtests()
