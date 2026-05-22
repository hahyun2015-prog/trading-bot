import sqlite3
import pandas as pd
import numpy as np
import sys
import os

def load_intraday_data(conn):
    query = "SELECT code, date, open, high, low, close, volume FROM intraday_ohlcv ORDER BY code, date ASC"
    df = pd.read_sql_query(query, conn)
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['date'], inplace=True)
    return df

def run_proxy_backtest():
    conn = sqlite3.connect("kiwoom_data.db")
    
    # Check if table exists
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='intraday_ohlcv'")
    if not cursor.fetchone():
        print("Data table 'intraday_ohlcv' not found.")
        return
        
    df = load_intraday_data(conn)
    conn.close()
    
    if df.empty:
        print("No data available for backtesting.")
        return

    print("==========================================================")
    print(" Strategy 3 & 4 Proxy Backtest Results")
    print("==========================================================")
    
    codes = df['code'].unique()
    
    # ---------------------------------------------------------
    # Strategy 3: V-Shape Mean Reversion (투매 줍기)
    # Proxy: Intraday drop > 4% within a short period, buy and hold for bounce.
    # ---------------------------------------------------------
    strat3_trades = 0
    strat3_wins = 0
    strat3_profit = 0.0
    
    for code in codes:
        cdf = df[df['code'] == code].copy()
        if len(cdf) < 20: continue
        
        # Calculate RSI 14
        delta = cdf['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        cdf['rsi'] = 100 - (100 / (1 + rs))
        
        in_pos = False
        buy_price = 0
        
        for i in range(20, len(cdf)):
            row = cdf.iloc[i]
            if not in_pos:
                # Sudden drop proxy: close is 4% lower than highest close of last 10 periods, AND RSI < 25
                recent_high = cdf['close'].iloc[i-10:i].max()
                if row['close'] < recent_high * 0.96 and row['rsi'] < 25:
                    in_pos = True
                    buy_price = row['close']
            else:
                profit = (row['close'] - buy_price) / buy_price
                # Exit on 3% profit or 2% loss or end of day (proxy by holding max 10 periods)
                if profit >= 0.03 or profit <= -0.02:
                    strat3_trades += 1
                    strat3_profit += (profit - 0.0025)
                    if profit > 0: strat3_wins += 1
                    in_pos = False

    print("\n[Strategy 3: V-Shape Mean Reversion (낙폭과대 투매 줍기)]")
    print(f"- 매매 횟수: {strat3_trades}회")
    if strat3_trades > 0:
        print(f"- 승률: {(strat3_wins/strat3_trades)*100:.1f}%")
        print(f"- 누적 수익률: {strat3_profit*100:+.2f}%")
        
    # ---------------------------------------------------------
    # Strategy 4: Pre-market Gap Fade (시가 갭 하락 양봉 매매)
    # Proxy: Today's open is < Yesterday's close * 0.98. Buy open, sell close.
    # ---------------------------------------------------------
    # To do this, we need to extract daily bars from the intraday data
    df['date_only'] = df['date'].dt.date
    daily_df = df.groupby(['code', 'date_only']).agg({
        'open': 'first',
        'close': 'last'
    }).reset_index()
    
    strat4_trades = 0
    strat4_wins = 0
    strat4_profit = 0.0
    
    for code in codes:
        cdf = daily_df[daily_df['code'] == code].copy()
        cdf['prev_close'] = cdf['close'].shift(1)
        
        for i in range(1, len(cdf)):
            row = cdf.iloc[i]
            if pd.isna(row['prev_close']): continue
            
            # Gap down between -2% and -5%
            gap_pct = (row['open'] - row['prev_close']) / row['prev_close']
            if -0.05 <= gap_pct <= -0.02:
                # Buy open, sell close
                profit = (row['close'] - row['open']) / row['open']
                strat4_trades += 1
                strat4_profit += (profit - 0.0025)
                if profit > 0: strat4_wins += 1
                
    print("\n[Strategy 4: Pre-market Gap Fade (시가 갭 하락 양봉 매매)]")
    print(f"- 매매 횟수: {strat4_trades}회")
    if strat4_trades > 0:
        print(f"- 승률: {(strat4_wins/strat4_trades)*100:.1f}%")
        print(f"- 누적 수익률: {strat4_profit*100:+.2f}%")
        
    # ---------------------------------------------------------
    # Combination: Strat 3 OR Strat 4 (Portfolio Diversification)
    # ---------------------------------------------------------
    print("\n[Combination Analysis: Strategy 3 + Strategy 4 포트폴리오 분산]")
    total_trades = strat3_trades + strat4_trades
    if total_trades > 0:
        total_wins = strat3_wins + strat4_wins
        total_profit = strat3_profit + strat4_profit
        print(f"- 통합 매매 횟수: {total_trades}회")
        print(f"- 통합 승률: {(total_wins/total_trades)*100:.1f}%")
        print(f"- 통합 누적 수익률: {total_profit*100:+.2f}%")

if __name__ == "__main__":
    run_proxy_backtest()
