import sqlite3
import pandas as pd
import numpy as np
import sys

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

def run_orb_backtest():
    print("==========================================================")
    print("  새로운 매매기법: ORB (오전 장초반 고점 돌파) 백테스트")
    print("==========================================================")
    
    conn = sqlite3.connect(r"c:\antigravity\노트븍활용\ai_trader\kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        return
        
    all_data = []
    
    for idx, code in enumerate(codes):
        sys.stdout.write(f"\r진행률: {idx+1}/{len(codes)} ({code}) 연산 중...")
        sys.stdout.flush()
        
        df = load_intraday_data(code, conn)
        if len(df) < 60:
            continue
            
        df['date_only'] = df.index.date
        df['code'] = code
        is_buy_signal = np.zeros(len(df), dtype=bool)
        
        for date, group in df.groupby('date_only'):
            # 09:00 ~ 09:30 데이터 추출
            morning_range = group[(group.index.time >= pd.to_datetime("09:00").time()) & 
                                  (group.index.time <= pd.to_datetime("09:30").time())]
            
            if len(morning_range) == 0:
                continue
                
            # 시가총액/유동성 필터 대신 일단 고가 구하기
            orb_high = morning_range['high'].max()
            
            # 09:30 ~ 11:30 사이 돌파 매매
            for i in group.index:
                if pd.to_datetime("09:30").time() < i.time() <= pd.to_datetime("11:30").time():
                    idx_loc = df.index.get_loc(i)
                    current_close = df.iloc[idx_loc]['close']
                    
                    # 10분 이평선 계산 (직접)
                    if idx_loc >= 10:
                        ma_10 = df.iloc[idx_loc-10:idx_loc]['close'].mean()
                    else:
                        ma_10 = current_close
                        
                    # ORB 돌파 + 추세(MA10 위) + 거래량 증가
                    vol_condition = True
                    if idx_loc >= 5:
                        recent_vol = df.iloc[idx_loc-5:idx_loc]['volume'].mean()
                        if recent_vol < 1000: # 거래량 매우 적으면 패스
                            vol_condition = False
                            
                    if current_close > orb_high and current_close > ma_10 and vol_condition:
                        # 당일 이미 샀으면 신호 무시 (하루 한 종목 1번만)
                        is_buy_signal[idx_loc] = True
                        break # 하루에 한 번만 신호 발생
                        
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
                
                if current_price > pos['max_price']:
                    pos['max_price'] = current_price
                    
                profit_ratio = (current_price - pos['buy_price']) / pos['buy_price']
                sell_reason = None
                
                # 손절: -2%
                if profit_ratio <= -0.02:
                    sell_reason = "손절 (-2%)"
                # 트레일링 스탑: 최고점 대비 1.5% 하락
                elif pos['max_price'] > pos['buy_price'] * 1.02 and current_price < pos['max_price'] * 0.985:
                    sell_reason = "트레일링 익절"
                # 고정 익절: +4%
                elif profit_ratio >= 0.04:
                    sell_reason = "고정 익절 (+4%)"
                # 시간 청산
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
                            'max_price': row['close'],
                            'invested': invested
                        }
                        
    conn.close()
    
    final_profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    
    print("\n==========================================================")
    print("  ORB (오전 장초반 고점 돌파) 모멘텀 스캘핑 백테스트 결과")
    print("==========================================================")
    print(f"매매횟수: {total_trades}")
    print(f"승률: {win_rate:.1f}%")
    print(f"누적 복리수익률: {final_profit_pct:.2f}%")
    print(f"최종 자본금: {current_capital:,.0f} 원")
    print("==========================================================")

if __name__ == "__main__":
    run_orb_backtest()
