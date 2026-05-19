import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

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
        df.sort_index(inplace=True)
    return df

def run_compounding_backtest(df):
    if len(df) < 50:
        return None

    df = df.copy()
    df['date_only'] = df.index.date
    daily_stats = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily_stats['range'] = daily_stats['high'] - daily_stats['low']
    daily_stats['prev_range'] = daily_stats['range'].shift(1)
    
    df = df.join(daily_stats[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df.rename(columns={'open_day': 'day_open'}, inplace=True)
    
    POINT_VALUE = 250000 
    INITIAL_CAPITAL = 50000000
    current_capital = INITIAL_CAPITAL
    
    position = 0
    entry_price = 0.0
    contracts = 1
    
    total_trades = 0
    winning_trades = 0
    total_profit_krw = 0.0
    
    K = 0.5
    
    for i in range(len(df)):
        curr_row = df.iloc[i]
        current_time = df.index[i]
        current_price = curr_row['close']
        
        day_open = curr_row['day_open']
        prev_range = curr_row['prev_range']
        
        if pd.isna(prev_range):
            continue
            
        target_price_long = day_open + (prev_range * K)
        target_price_short = day_open - (prev_range * K)
        
        is_morning_open = False
        if current_time.hour == 8 and 45 <= current_time.minute <= 50:
            is_morning_open = True
            
        # 청산 로직 (다음날 아침 08:45)
        if position != 0:
            if is_morning_open:
                profit_pt = 0.0
                if position == 1:
                    profit_pt = current_price - entry_price
                elif position == -1:
                    profit_pt = entry_price - current_price
                    
                profit_pt -= 0.05 # 수수료/슬리피지
                
                # 복리 적용: 매매 당시의 계약 수만큼 수익/손실 발생
                trade_pnl = profit_pt * POINT_VALUE * contracts
                current_capital += trade_pnl
                total_profit_krw += trade_pnl
                
                total_trades += 1
                if trade_pnl > 0:
                    winning_trades += 1
                    
                position = 0
            continue 
            
        # 신규 진입 로직
        if position == 0 and not is_morning_open:
            if curr_row['high'] >= target_price_long:
                position = 1
                entry_price = target_price_long
                # 복리 계약 수 산정: 자본금 5천만원당 1계약 (안전 증거금 기준)
                contracts = max(1, int(current_capital // 50000000))
            elif curr_row['low'] <= target_price_short:
                position = -1
                entry_price = target_price_short
                contracts = max(1, int(current_capital // 50000000))
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    start_date = df.index[0]
    end_date = df.index[-1]
    days_diff = (end_date - start_date).days
    if days_diff == 0: days_diff = 1
    
    # 연환산 수익률 (CAGR 공식)
    cagr = ((current_capital / INITIAL_CAPITAL) ** (365 / days_diff) - 1) * 100
    
    return {
        'start_date': start_date,
        'end_date': end_date,
        'days': days_diff,
        'trades': total_trades,
        'win_rate': win_rate,
        'final_capital': current_capital,
        'profit_pct': profit_pct,
        'cagr': cagr
    }

def main():
    df = load_data()
    res = run_compounding_backtest(df)
    
    if not res:
        return
        
    print(f"데이터 기간: {res['start_date']} ~ {res['end_date']} ({res['days']}일)")
    print(f"총 매매 횟수: {res['trades']}회")
    print(f"승률: {res['win_rate']:.2f}%")
    print(f"초기 자본금: 50,000,000 원")
    print(f"최종 자본금: {res['final_capital']:,.0f} 원")
    print(f"누적 수익률(복리): {res['profit_pct']:.2f}%")
    print(f"연환산 추정 수익률(CAGR): {res['cagr']:.2f}%")

if __name__ == "__main__":
    main()
