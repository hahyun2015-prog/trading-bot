import sqlite3
import pandas as pd
import numpy as np
import sys
import ta

def load_all_intraday_data(conn):
    query = "SELECT DISTINCT code FROM intraday_ohlcv"
    cursor = conn.cursor()
    cursor.execute(query)
    return [row[0] for row in cursor.fetchall()]

def load_intraday_data(code, conn):
    query = f"SELECT date, open, high, low, close, volume FROM intraday_ohlcv WHERE code = '{code}' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
    return df

def apply_bb_rsi(df):
    # Bollinger Bands (window=20, std=2)
    bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2.0)
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_mid'] = bb.bollinger_mavg()
    df['bb_upper'] = bb.bollinger_hband()
    
    # RSI (window=14)
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    return df

def run_bb_backtest():
    print("==========================================================")
    print("  새로운 매매기법: 볼린저밴드 역추세 (낙폭과대 바운스) 백테스트")
    print("==========================================================")
    
    conn = sqlite3.connect(r"c:\antigravity\노트븍활용\ai_trader\kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        print("DB에 데이터가 없습니다.")
        return
        
    print(f"총 {len(codes)}개 종목에 대한 시그널 연산을 시작합니다...")
    
    all_data = []
    
    for idx, code in enumerate(codes):
        sys.stdout.write(f"\r진행률: {idx+1}/{len(codes)} ({code}) 연산 중...")
        sys.stdout.flush()
        
        df = load_intraday_data(code, conn)
        if len(df) < 60:
            continue
            
        df = apply_bb_rsi(df)
        df['code'] = code
        
        is_buy_signal = np.zeros(len(df), dtype=bool)
        
        for i in range(60, len(df)):
            current_row = df.iloc[i]
            
            # 조건 1: 종가가 볼린저밴드 하단 이탈
            bb_condition = current_row['close'] < current_row['bb_lower']
            
            # 조건 2: RSI가 30 미만 (과매도)
            rsi_condition = current_row['rsi'] < 30
            
            # 조건 3: 시간대 (오전 9시 30분 ~ 14시 사이의 투매장 잡기)
            current_time = df.index[i]
            time_condition = 9 <= current_time.hour <= 14
            
            if bb_condition and rsi_condition and time_condition:
                is_buy_signal[i] = True
                
        df['is_buy_signal'] = is_buy_signal
        all_data.append(df)
        
    print("\n\n연산 완료! 백테스트를 시작합니다...\n")
    
    combined_df = pd.concat(all_data)
    combined_df.sort_index(inplace=True)
    grouped = combined_df.groupby(combined_df.index)
    
    portfolio = {}
    MAX_POSITIONS = 8
    FEE_AND_TAX = 0.0025
    SLIPPAGE_RATE = 0.002
    
    INITIAL_CAPITAL = 100000000
    current_capital = INITIAL_CAPITAL
    available_capital = INITIAL_CAPITAL
    
    total_trades = 0
    winning_trades = 0
    
    for current_time, group_df in grouped:
        codes_to_remove = []
        for code, pos in portfolio.items():
            if code in group_df['code'].values:
                row = group_df[group_df['code'] == code].iloc[0]
                current_price = row['close']
                
                profit_ratio = (current_price - pos['buy_price']) / pos['buy_price']
                sell_reason = None
                
                # 익절: 중심선(20MA) 회귀 시
                if current_price >= row['bb_mid']:
                    sell_reason = "중심선 회귀 익절"
                # 손절: -3% 하락 시 (칼손절)
                elif profit_ratio <= -0.03:
                    sell_reason = "손절 (-3%)"
                # 시간 제한: 당일 종가 부근(15:15) 무조건 청산 (오버나잇 안함)
                elif current_time.hour == 15 and current_time.minute >= 15:
                    sell_reason = "당일 청산"

                if sell_reason:
                    net_profit = profit_ratio - FEE_AND_TAX - SLIPPAGE_RATE
                    returned_amount = pos['invested'] * (1 + net_profit)
                    available_capital += returned_amount
                    current_capital += pos['invested'] * net_profit
                    
                    total_trades += 1
                    if net_profit > 0:
                        winning_trades += 1
                        
                    codes_to_remove.append(code)
                    
        for code in codes_to_remove:
            del portfolio[code]
            
        if len(portfolio) < MAX_POSITIONS:
            buy_candidates = group_df[group_df['is_buy_signal'] == True]
            for _, row in buy_candidates.iterrows():
                code = row['code']
                if code not in portfolio and len(portfolio) < MAX_POSITIONS:
                    allocation_per_slot = current_capital / MAX_POSITIONS
                    if available_capital >= allocation_per_slot * 0.99:
                        invested = min(allocation_per_slot, available_capital)
                        available_capital -= invested
                        portfolio[code] = {
                            'buy_price': row['close'],
                            'invested': invested
                        }
                        
    conn.close()
    
    final_profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    
    print("\n==========================================================")
    print("  볼린저밴드 낙폭과대(역추세) 스캘핑 백테스트 결과")
    print("==========================================================")
    print(f"매매횟수: {total_trades}")
    print(f"승률: {win_rate:.1f}%")
    print(f"누적 복리수익률: {final_profit_pct:.2f}%")
    print(f"최종 자본금: {current_capital:,.0f} 원")
    print("==========================================================")

if __name__ == "__main__":
    run_bb_backtest()
