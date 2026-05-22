import sqlite3
import pandas as pd
import numpy as np
import json
import os
import sys
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

# 결과 저장 경로
RESULTS_FILE = os.path.join(workspace_root, "config", "active_strategy.json")

def load_data(db_path):
    try:
        conn = sqlite3.connect(db_path)
        query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10100000' ORDER BY date ASC"
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if not df.empty:
            raw_dates = df['date'].copy()
            df['date'] = pd.to_datetime(raw_dates, format='%Y%m%d%H%M%S', errors='coerce')
            if df['date'].isnull().all():
                # 포맷 미매칭 시 원본 값으로 재시도
                df['date'] = pd.to_datetime(raw_dates, errors='coerce')
            df.set_index('date', inplace=True)
            df.sort_index(inplace=True)
        return df
    except Exception as e:
        print(f"데이터 로드 에러: {e}")
        return pd.DataFrame()

def run_backtest_with_k(df, K):
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
            
        if position != 0:
            if is_morning_open:
                profit_pt = current_price - entry_price if position == 1 else entry_price - current_price
                profit_pt -= 0.05
                trade_pnl = profit_pt * POINT_VALUE * contracts
                current_capital += trade_pnl
                total_trades += 1
                if trade_pnl > 0:
                    winning_trades += 1
                position = 0
            continue 
            
        if position == 0 and not is_morning_open:
            if curr_row['high'] >= target_price_long:
                position = 1
                entry_price = target_price_long
                contracts = max(1, int(current_capital // 50000000))
            elif curr_row['low'] <= target_price_short:
                position = -1
                entry_price = target_price_short
                contracts = max(1, int(current_capital // 50000000))
                
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    days_diff = (df.index[-1] - df.index[0]).days
    days_diff = 1 if days_diff == 0 else days_diff
    cagr = ((current_capital / INITIAL_CAPITAL) ** (365 / days_diff) - 1) * 100 if current_capital > 0 else 0
    
    return {
        'K': K,
        'trades': total_trades,
        'win_rate': round(win_rate, 2),
        'cagr': round(cagr, 2)
    }

def optimize():
    print(f"[{datetime.now()}] AI 팩토리 파라미터 최적화 루프 시작...")
    db_path = os.path.join(workspace_root, "futures_data.db")
    df = load_data(db_path)
    if df.empty:
        print("데이터가 없습니다. 대기...")
        return
        
    results = []
    k_values = np.arange(0.1, 1.05, 0.05)
    
    for k in k_values:
        k = round(k, 2)
        res = run_backtest_with_k(df, k)
        if res and res['trades'] > 10:
            results.append(res)
            
    results.sort(key=lambda x: x['cagr'], reverse=True)
    top_results = results[:3]
    
    print("=== 최적화 결과 Top 3 ===")
    for r in top_results:
        print(f"K={r['K']}: CAGR={r['cagr']}%, 승률={r['win_rate']}%")
        
    out_data = {
        'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'top_strategies': top_results,
        'best_k': top_results[0]['K'] if top_results else 0.5
    }
    
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=4)
    print("결과가 성공적으로 저장되었습니다.")

if __name__ == "__main__":
    optimize()
