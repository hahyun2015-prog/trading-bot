import sqlite3
import pandas as pd
import numpy as np
import sys
import os

# ai_trader 경로 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
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

def run_optimized_backtest():
    conn = sqlite3.connect("kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        print("데이터베이스에 백테스트할 데이터가 없습니다.")
        return
        
    FEE_AND_TAX = 0.0025
    
    total_trades = 0
    winning_trades = 0
    total_profit_pct = 0.0

    print("==========================================================")
    print("  Optimized Strategy Backtest (5가지 최적화 방안 모두 적용)")
    print("==========================================================")
    print("진행 중... 잠시만 기다려주세요.")

    for code in codes:
        df = load_intraday_data(code, conn)
        if len(df) < 120:
            continue
            
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        df['ma_120'] = df['close'].rolling(window=120).mean() # 시장/종목 추세(Regime) 필터용
        
        # ATR 계산 (변동성 기반 자금관리용)
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift())
        df['tr2'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        df['atr_14'] = df['tr'].rolling(window=14).mean()
        
        in_pos = False
        buy_price = 0
        super_trend_mode = False
        half_sold = False
        peak_price = 0 # 트레일링 스탑용 최고가
        position_size = 1.0 # 기본 100% 비중
        
        for i in range(120, len(df)):
            current_row = df.iloc[i]
            prev_row = df.iloc[i-1]
            
            # 시간대 정보 추출
            hour = current_row.name.hour
            minute = current_row.name.minute
            time_val = hour * 100 + minute
            
            # 3. 거래 시간대 필터 (09:00~11:00 또는 14:30~15:20 에만 진입 허용)
            is_valid_time = (900 <= time_val <= 1100) or (1430 <= time_val <= 1520)
            
            if not in_pos:
                window_df = df.iloc[:i+1]
                
                # 1. 시장/종목 상황 필터 (120 이평선 위에 있을 때만 = 상승 추세)
                is_bull_regime = current_row['close'] >= current_row['ma_120']
                
                if is_bull_regime and is_valid_time:
                    is_pullback = check_vwap_pullback(window_df)
                    is_divergence = check_hidden_bullish_divergence(window_df)
                    is_uptrend = current_row['close'] >= (current_row['ma_10'] * 0.99)
                    
                    if is_pullback and is_divergence and is_uptrend:
                        in_pos = True
                        buy_price = current_row['close']
                        peak_price = buy_price
                        super_trend_mode = False
                        half_sold = False
                        
                        # 5. 변동성 기반 자금 관리 (ATR이 높으면 비중 축소, 낮으면 비중 확대)
                        # 단순화: 변동성이 현재가의 2%를 넘으면 비중을 절반으로 줄임
                        volatility_pct = current_row['atr_14'] / current_row['close']
                        if volatility_pct > 0.02:
                            position_size = 0.5
                        else:
                            position_size = 1.0
                            
            else:
                profit_ratio = (current_row['close'] - buy_price) / buy_price
                ma_10_is_up = current_row['ma_10'] > prev_row['ma_10']
                
                # 트레일링 스탑용 최고가 갱신
                if current_row['close'] > peak_price:
                    peak_price = current_row['close']
                    
                # 최고가 대비 하락률
                drawdown_from_peak = (peak_price - current_row['close']) / peak_price
                
                exit_signal_full = False
                
                # 2. 분할 매도 (Scale-out)
                if not half_sold and profit_ratio >= 0.03:
                    # +3% 도달 시 절반(50%) 익절
                    half_net_profit = profit_ratio - FEE_AND_TAX
                    # 절반 물량이므로 비중의 절반만 수익 반영
                    total_profit_pct += (half_net_profit * 100 * position_size * 0.5)
                    half_sold = True
                    super_trend_mode = True # 나머지 절반은 10MA 트레일링
                
                # 4. 스윙/단타 공통: ATR/고점 기반 트레일링 스탑 (최고가 대비 -1.5% 하락 시 기계적 익절/손절)
                if drawdown_from_peak >= 0.015:
                    exit_signal_full = True
                    
                # 고정 손절선 (-2%)
                elif profit_ratio <= -0.02:
                    exit_signal_full = True
                    
                else:
                    if super_trend_mode:
                        # 10이평선 이탈 시 나머지 전량 익절
                        if current_row['close'] < current_row['ma_10']:
                            exit_signal_full = True
                            
                if exit_signal_full:
                    net_profit = profit_ratio - FEE_AND_TAX
                    
                    if half_sold:
                        # 이미 절반을 팔았으므로 나머지 절반 물량에 대한 수익만 반영
                        total_profit_pct += (net_profit * 100 * position_size * 0.5)
                        if net_profit > 0:
                            winning_trades += 1 # 반익 반본절 성공으로 간주
                    else:
                        # 전량 매도
                        total_profit_pct += (net_profit * 100 * position_size)
                        if net_profit > 0:
                            winning_trades += 1
                            
                    total_trades += 1
                    in_pos = False

    conn.close()
    
    print("\n==========================================================")
    print(" [최적화 완료 결과] 5대 솔루션 동시 적용 (단타 봇 기준)")
    print(" 1. 120MA 장기 추세 필터 (Regime)")
    print(" 2. +3% 절반 익절 (Scale-out) & 10MA 트레일링")
    print(" 3. 09-11시 / 14:30-15:20 핵심 시간대(Time) 한정")
    print(" 4. 고점 대비 -1.5% 트레일링 스탑")
    print(" 5. ATR 변동성 기반 진입 비중 조절")
    print("==========================================================")
    print(f"총 매매 횟수: {total_trades}회")
    if total_trades > 0:
        print(f"승률(부분익절 포함): {(winning_trades/total_trades)*100:.1f}%")
        print(f"누적 복리(단리합산) 수익률: {total_profit_pct:+.2f}%")
    print("==========================================================")

if __name__ == "__main__":
    run_optimized_backtest()
