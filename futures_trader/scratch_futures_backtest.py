import sqlite3
import pandas as pd
import numpy as np
import ta
import sys
import warnings
warnings.filterwarnings("ignore")

def load_data():
    conn = sqlite3.connect("futures_data.db")
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10100000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        # Sort index to be safe
        df.sort_index(inplace=True)
    return df

def prepare_indicators(df):
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_m'] = indicator_bb.bollinger_mavg()
    df['bb_h'] = indicator_bb.bollinger_hband()
    df['bb_l'] = indicator_bb.bollinger_lband()
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    df['ma_60'] = df['close'].rolling(window=60).mean()
    return df

def run_simulation(df, config_name, use_trend_filter=False, use_scaling_out=False):
    # Futures point value multiplier (e.g., 1 point = 250,000 KRW)
    POINT_VALUE = 250000 
    INITIAL_CAPITAL = 50000000 # 5천만원 시작
    CONTRACTS = 1 # 1계약 고정
    
    current_capital = INITIAL_CAPITAL
    position = 0 # 1: Long, -1: Short
    entry_price = 0.0
    scaled_out = False
    
    total_trades = 0
    winning_trades = 0
    total_profit_pts = 0.0
    
    STOP_LOSS_PT = 1.0

    for i in range(60, len(df)):
        curr_row = df.iloc[i]
        current_price = curr_row['close']
        current_time = df.index[i]
        
        is_closing_time = False
        if current_time.hour == 15 and current_time.minute >= 30: is_closing_time = True
        elif current_time.hour == 4 and current_time.minute >= 50: is_closing_time = True
            
        # 청산 로직
        if position != 0:
            exit_signal = False
            profit_pt = 0.0
            
            if position == 1:
                # 롱 청산
                current_profit = current_price - entry_price
                
                # 손절
                stop_level = entry_price if scaled_out else (entry_price - STOP_LOSS_PT)
                if current_price <= stop_level:
                    profit_pt = current_profit
                    exit_signal = True
                    
                # 1차 익절 (중심선)
                elif current_price >= curr_row['bb_m'] and not scaled_out:
                    if use_scaling_out:
                        # 50% 절반 익절로 간주 (1계약이므로 실제로는 0.5계약 익절 효과)
                        total_profit_pts += current_profit * 0.5
                        scaled_out = True
                    else:
                        profit_pt = current_profit
                        exit_signal = True
                        
                # 2차 익절 (상단선) - 최적화 로직 전용
                elif use_scaling_out and scaled_out and current_price >= curr_row['bb_h']:
                    profit_pt = current_profit * 0.5 # 남은 절반 물량 청산
                    exit_signal = True
                    
                elif is_closing_time:
                    profit_pt = current_profit * (0.5 if scaled_out else 1.0)
                    exit_signal = True
                    
            elif position == -1:
                # 숏 청산
                current_profit = entry_price - current_price
                
                # 손절
                stop_level = entry_price if scaled_out else (entry_price + STOP_LOSS_PT)
                if current_price >= stop_level:
                    profit_pt = current_profit
                    exit_signal = True
                    
                # 1차 익절 (중심선)
                elif current_price <= curr_row['bb_m'] and not scaled_out:
                    if use_scaling_out:
                        total_profit_pts += current_profit * 0.5
                        scaled_out = True
                    else:
                        profit_pt = current_profit
                        exit_signal = True
                        
                # 2차 익절 (하단선) - 최적화 로직 전용
                elif use_scaling_out and scaled_out and current_price <= curr_row['bb_l']:
                    profit_pt = current_profit * 0.5 # 남은 절반 물량 청산
                    exit_signal = True
                    
                elif is_closing_time:
                    profit_pt = current_profit * (0.5 if scaled_out else 1.0)
                    exit_signal = True
                    
            if exit_signal:
                total_profit_pts += profit_pt
                # 수수료/슬리피지 대략 0.05pt 차감
                total_profit_pts -= 0.05
                current_capital += (profit_pt - 0.05) * POINT_VALUE
                total_trades += 1
                if profit_pt > 0 or scaled_out:
                    winning_trades += 1
                position = 0
                continue
                
        # 진입 로직
        if position == 0 and not is_closing_time:
            # 추세 필터
            trend_up = curr_row['ma_60'] > df.iloc[i-5]['ma_60'] if use_trend_filter else True
            trend_down = curr_row['ma_60'] < df.iloc[i-5]['ma_60'] if use_trend_filter else True
            
            # 다이버전스 간소화 (RSI 기준만 적용)
            long_cond = curr_row['low'] <= curr_row['bb_l'] and curr_row['rsi'] <= 30
            short_cond = curr_row['high'] >= curr_row['bb_h'] and curr_row['rsi'] >= 70
            
            if long_cond and trend_up:
                position = 1
                entry_price = current_price
                scaled_out = False
            elif short_cond and trend_down:
                position = -1
                entry_price = current_price
                scaled_out = False
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    return {
        'name': config_name,
        'trades': total_trades,
        'win_rate': win_rate,
        'profit_pts': total_profit_pts,
        'final_capital': current_capital,
        'profit_pct': profit_pct
    }

def main():
    print("선물 KOSPI 200 데이터 로딩 중...")
    df = load_data()
    if len(df) < 60:
        print("데이터 부족")
        return
        
    df = prepare_indicators(df)
    
    print("백테스트 시뮬레이션 시작...\n")
    
    res1 = run_simulation(df, "1. 원본 (현재 봇 로직)", use_trend_filter=False, use_scaling_out=False)
    res2 = run_simulation(df, "2. 최적화 조합 (추세필터 + 분할/반대밴드 익절)", use_trend_filter=True, use_scaling_out=True)
    
    results = [res1, res2]
    
    print("=========================================================================")
    print(f"{'전략 구성':<40} | {'매매횟수':<6} | {'승률':<6} | {'누적수익(pt)':<10} | {'자본수익률(%)'}")
    print("-" * 73)
    for r in results:
        print(f"{r['name']:<40} | {r['trades']:<8} | {r['win_rate']:<7.1f}% | {r['profit_pts']:<13.2f} | {r['profit_pct']:<10.2f}%")
    print("=========================================================================")

if __name__ == "__main__":
    main()
