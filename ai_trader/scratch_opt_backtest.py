import sqlite3
import pandas as pd
import numpy as np
import sys

sys.path.append(r"c:\antigravity\노트븍활용\ai_trader")
from strategy_engine import calculate_vwap, apply_rsi, check_hidden_bullish_divergence, check_vwap_pullback

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

def run_portfolio_backtest():
    conn = sqlite3.connect(r"c:\antigravity\노트븍활용\ai_trader\kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    print("사전 데이터 로딩 중...")
    base_data = {}
    for idx, code in enumerate(codes):
        sys.stdout.write(f"\r진행률: {idx+1}/{len(codes)}")
        sys.stdout.flush()
        df = load_intraday_data(code, conn)
        if len(df) >= 60:
            df = calculate_vwap(df)
            df = apply_rsi(df)
            df['ma_10'] = df['close'].rolling(window=10).mean()
            df['ma_20'] = df['close'].rolling(window=20).mean()
            df['code'] = code
            base_data[code] = df
            
    print("\n데이터 로딩 완료.")
    
    configs = [
        {"name": "Base (원본 로직)", "time": False, "vol": False, "scale": False},
        {"name": "Base + 시간대 필터", "time": True, "vol": False, "scale": False},
        {"name": "Base + 폭발 거래량", "time": False, "vol": True, "scale": False},
        {"name": "Base + 분할/트레일링 익절", "time": False, "vol": False, "scale": True}
    ]
    
    results = []
    
    for cfg in configs:
        print(f"[{cfg['name']}] 시뮬레이션 중...")
        all_data = []
        for code, df in base_data.items():
            df_copy = df.copy()
            is_buy_signal = np.zeros(len(df_copy), dtype=bool)
            
            for i in range(60, len(df_copy)):
                current_time = df_copy.index[i]
                
                # 1. 시간대 필터
                if cfg['time']:
                    hour = current_time.hour
                    minute = current_time.minute
                    is_good = False
                    if (hour == 9 and minute >= 15) or hour == 10: is_good = True
                    elif hour == 14 and minute >= 30: is_good = True
                    elif hour == 15: is_good = True
                    if not is_good: continue
                    
                window_df = df_copy.iloc[:i+1]
                
                if not check_vwap_pullback(window_df): continue
                if not check_hidden_bullish_divergence(window_df): continue
                if df_copy.iloc[i]['close'] < (df_copy.iloc[i]['ma_10'] * 0.99): continue
                
                # 2. 거래량 폭발 필터
                if cfg['vol']:
                    recent_vol_max = window_df.iloc[-30:]['volume'].max()
                    prev_vol_mean = window_df.iloc[-60:-30]['volume'].mean()
                    if prev_vol_mean == 0 or recent_vol_max <= prev_vol_mean * 2:
                        continue
                        
                is_buy_signal[i] = True
                
            df_copy['is_buy_signal'] = is_buy_signal
            all_data.append(df_copy)
            
        combined_df = pd.concat(all_data)
        combined_df.sort_index(inplace=True)
        grouped = combined_df.groupby(combined_df.index)
        
        portfolio = {}
        MAX_POSITIONS = 8
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
                    if current_price > pos['max_price']: pos['max_price'] = current_price
                    profit_ratio = (current_price - pos['buy_price']) / pos['buy_price']
                    sell_reason = None
                    
                    if profit_ratio <= -0.02:
                        sell_reason = "손절"
                    else:
                        if cfg['scale']:
                            if profit_ratio >= 0.03 and not pos['scaled_out']:
                                net_profit = profit_ratio - 0.0045
                                available_capital += (pos['invested'] * 0.5) * (1 + net_profit)
                                current_capital += (pos['invested'] * 0.5) * net_profit
                                pos['invested'] = pos['invested'] * 0.5
                                pos['scaled_out'] = True
                            if pos['scaled_out']:
                                if (pos['max_price'] - current_price)/pos['max_price'] >= 0.015 or current_price < row['ma_20']:
                                    sell_reason = "트레일링 익절"
                            else:
                                if current_price < row['ma_10'] and profit_ratio > 0.01:
                                    sell_reason = "10MA 익절"
                        else:
                            if current_price < row['ma_10'] and profit_ratio > 0.01:
                                sell_reason = "10MA 익절"
                                
                    if sell_reason:
                        net_profit = profit_ratio - 0.0045
                        available_capital += pos['invested'] * (1 + net_profit)
                        current_capital += pos['invested'] * net_profit
                        total_trades += 1
                        if net_profit > 0 or pos['scaled_out']: winning_trades += 1
                        codes_to_remove.append(code)
                        
            for code in codes_to_remove: del portfolio[code]
            
            if len(portfolio) < MAX_POSITIONS:
                buy_candidates = group_df[group_df['is_buy_signal'] == True]
                for _, row in buy_candidates.iterrows():
                    code = row['code']
                    if code not in portfolio and len(portfolio) < MAX_POSITIONS:
                        alloc = current_capital / MAX_POSITIONS
                        if available_capital >= alloc * 0.99:
                            invested = min(alloc, available_capital)
                            available_capital -= invested
                            portfolio[code] = {'buy_price': row['close'], 'max_price': row['close'], 'scaled_out': False, 'invested': invested}
                            
        results.append({
            'name': cfg['name'],
            'trades': total_trades,
            'win_rate': (winning_trades/total_trades*100) if total_trades > 0 else 0,
            'profit': ((current_capital - INITIAL_CAPITAL)/INITIAL_CAPITAL)*100
        })
        
    conn.close()
    
    print("\n==========================================================")
    print(f"{'전략 구성':<25} | {'매매횟수':<8} | {'승률':<8} | {'복리수익률(%)'}")
    print("-" * 55)
    for r in results:
        print(f"{r['name']:<25} | {r['trades']:<10} | {r['win_rate']:<9.1f}% | {r['profit']:<15.2f}%")
    print("==========================================================")

if __name__ == "__main__":
    run_portfolio_backtest()
