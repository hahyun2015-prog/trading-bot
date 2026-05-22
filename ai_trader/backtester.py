import sqlite3
import pandas as pd
import numpy as np

# strategy_engine.py에서 필요한 지표 계산 함수를 가져옵니다.
from strategy_engine import calculate_vwap, apply_rsi, check_hidden_bullish_divergence, check_vwap_pullback

def load_all_intraday_data(conn):
    """SQLite DB에서 백테스트용 3분봉 데이터가 있는 모든 종목을 불러옵니다."""
    query = "SELECT DISTINCT code FROM backtest_ohlcv"
    cursor = conn.cursor()
    cursor.execute(query)
    codes = [row[0] for row in cursor.fetchall()]
    return codes

def load_intraday_data(code, conn):
    query = f"SELECT date, open, high, low, close, volume FROM backtest_ohlcv WHERE code = '{code}' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
    return df

def run_backtest():
    print("===================================================")
    print("  AI Quant - Combo 1 (VWAP+Divergence) Backtester")
    print("===================================================")
    
    conn = sqlite3.connect("kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        print("DB에 백테스트할 3분봉 데이터가 없습니다.")
        return
        
    print(f"DB에 저장된 {len(codes)}개 종목의 과거 3분봉(약 900틱) 데이터를 대상으로 시뮬레이션을 시작합니다.\n")
    
    total_trades = 0
    winning_trades = 0
    total_profit_pct = 0.0
    
    # 목표 수익률과 손절선은 진입 시점의 ATR 기반으로 동적 산출됨 (익절: 3 ATR, 손절: 2 ATR)
    
    # 매매 수수료 및 세금 (키움증권 기준: 매수 0.015%, 매도 0.015% + 거래세 0.2% = 약 0.23%)
    # 슬리피지(호가 공백)까지 약간 감안하여 보수적으로 1회 매매당 왕복 0.25% 차감으로 설정
    FEE_AND_TAX = 0.0025
    
    for code in codes:
        df = load_intraday_data(code, conn)
        if len(df) < 50:
            continue
            
        # 전체 데이터에 대해 지표 선 계산 (속도 최적화)
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        
        in_position = False
        super_trend_mode = False
        buy_price = 0
        buy_time = None
        
        # 30번째 봉부터 끝까지 순회하며 과거 시뮬레이션
        for i in range(30, len(df)):
            current_row = df.iloc[i]
            current_time = df.index[i]
            
            if not in_position:
                # 매수 타점 스캔 (현재 봉까지의 데이터를 슬라이싱하여 엔진에 전달)
                # 엔진의 함수들은 iloc[-1]을 기준으로 판별하도록 작성되어 있으므로 df.iloc[:i+1]을 전달
                window_df = df.iloc[:i+1]
                
                # strategy_engine.py의 로직과 동일
                is_pullback = check_vwap_pullback(window_df)
                is_divergence = check_hidden_bullish_divergence(window_df)
                is_uptrend = current_row['close'] >= (current_row['ma_10'] * 0.99)
                
                if is_pullback and is_divergence and is_uptrend:
                    in_position = True
                    super_trend_mode = False
                    buy_price = current_row['close']
                    buy_time = current_time
                    
            else:
                # 매수 상태일 경우 10MA 수익 극대화 로직 적용
                profit_ratio = (current_row['close'] - buy_price) / buy_price
                sell_reason = None
                
                # 1. 고정 손절선 (-2%)
                if profit_ratio <= -0.02:
                    sell_reason = "손절 (-2%)"
                else:
                    if super_trend_mode:
                        # 2. 수익 극대화 모드 중: 10이평선 이탈 또는 +1.5% 최소 수익 보장선 이탈 시 익절
                        if current_row['close'] < current_row['ma_10']:
                            sell_reason = "10MA 하향 돌파 익절"
                        elif profit_ratio <= 0.015:
                            sell_reason = "최소수익보장(+1.5%) 익절"
                    else:
                        # 3. +3% 목표가 도달 시
                        if profit_ratio >= 0.03:
                            ma_10_is_up = current_row['ma_10'] > df.iloc[i-1]['ma_10']
                            if current_row['ma_10'] > 0 and ma_10_is_up and current_row['close'] >= current_row['ma_10']:
                                super_trend_mode = True
                                continue
                            else:
                                sell_reason = "목표가(+3%) 도달 (추세꺾임 익절)"
                                
                if sell_reason:
                    total_trades += 1
                    
                    # 실제 수익률 = 이론 수익률 - 수수료/세금
                    net_profit_ratio = profit_ratio - FEE_AND_TAX
                    profit_pct = net_profit_ratio * 100
                    total_profit_pct += profit_pct
                    
                    if net_profit_ratio > 0:
                        winning_trades += 1
                        
                    print(f"[{buy_time.strftime('%m/%d %H:%M')}] {code} 매수 -> [{current_time.strftime('%m/%d %H:%M')}] {sell_reason}: {profit_pct:+.2f}% (수수료차감)")
                    
                    in_position = False
                    super_trend_mode = False
                    
    conn.close()
    
    print("\n===================================================")
    print("                 백테스트 최종 결과")
    print("===================================================")
    print(f"총 매매 횟수 : {total_trades}회 (조건이 극도로 엄격하여 매매 빈도가 낮음)")
    if total_trades > 0:
        print(f"승률(Win Rate) : {(winning_trades / total_trades) * 100:.1f}%")
        print(f"누적 수익률 : {total_profit_pct:+.2f}%")
    else:
        print("조건을 완벽히 만족하는 A급 타점이 과거 며칠간 발생하지 않았습니다.")
    print("===================================================")

if __name__ == "__main__":
    run_backtest()
