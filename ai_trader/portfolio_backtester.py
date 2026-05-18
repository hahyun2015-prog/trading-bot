import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
import sys

# 기존 모듈 재사용
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

def run_portfolio_backtest():
    print("==========================================================")
    print("  Portfolio Backtester: Combo 1 (Max 5 Positions)")
    print("==========================================================")
    
    conn = sqlite3.connect("kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        print("DB에 데이터가 없습니다.")
        return
        
    print(f"총 {len(codes)}개 종목에 대한 시그널 사전 연산을 시작합니다. (최대 1~2분 소요될 수 있습니다.)")
    
    all_data = []
    
    # 1. 시그널 사전 연산
    for idx, code in enumerate(codes):
        sys.stdout.write(f"\r진행률: {idx+1}/{len(codes)} ({code}) 연산 중...")
        sys.stdout.flush()
        
        df = load_intraday_data(code, conn)
        if len(df) < 60:
            continue
            
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        df['ma_20'] = df['close'].rolling(window=20).mean()
        df['ma_40'] = df['close'].rolling(window=40).mean()
        df['ma_60'] = df['close'].rolling(window=60).mean()
        df['code'] = code
        
        is_buy_signal = np.zeros(len(df), dtype=bool)
        
        for i in range(60, len(df)):
            current_row = df.iloc[i]
            window_df = df.iloc[:i+1]
            
            is_pullback = check_vwap_pullback(window_df)
            is_divergence = check_hidden_bullish_divergence(window_df)
            is_uptrend = current_row['close'] >= (current_row['ma_10'] * 0.99)
            
            if is_pullback and is_divergence and is_uptrend:
                is_buy_signal[i] = True
                
        df['is_buy_signal'] = is_buy_signal
        all_data.append(df)
        
    print("\n\n모든 종목 시그널 연산 완료! MA 변수별 시계열 포트폴리오 백테스트를 시작합니다...\n")
    
    combined_df = pd.concat(all_data)
    combined_df.sort_index(inplace=True)
    grouped = combined_df.groupby(combined_df.index)
    
    def run_simulation(grouped, exit_ma_col):
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
                    current_ma_10 = row['ma_10']
                    exit_ma_val = row[exit_ma_col]
                    
                    profit_ratio = (current_price - pos['buy_price']) / pos['buy_price']
                    sell_reason = None
                    
                    if profit_ratio <= -0.02:
                        sell_reason = "손절 (-2%)"
                    else:
                        if pos['super_trend_mode']:
                            if current_price < exit_ma_val:
                                sell_reason = f"{exit_ma_col.upper()} 하향 돌파 익절"
                            elif profit_ratio <= 0.015:
                                sell_reason = "최소수익보장(+1.5%) 익절"
                        else:
                            if profit_ratio >= 0.03:
                                if current_ma_10 > pos['prev_ma_10'] and current_price >= current_ma_10:
                                    portfolio[code]['super_trend_mode'] = True
                                else:
                                    sell_reason = "목표가(+3%) 도달 (추세 꺾임 익절)"
                                    
                    if sell_reason:
                        net_profit = profit_ratio - FEE_AND_TAX - SLIPPAGE_RATE
                        
                        returned_amount = pos['invested'] * (1 + net_profit)
                        available_capital += returned_amount
                        current_capital += pos['invested'] * net_profit
                        
                        total_trades += 1
                        if net_profit > 0:
                            winning_trades += 1
                        
                        codes_to_remove.append(code)
                    else:
                        portfolio[code]['prev_ma_10'] = current_ma_10
                        
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
                                'prev_ma_10': row['ma_10'],
                                'super_trend_mode': False,
                                'buy_time': current_time,
                                'invested': invested
                            }
                            
        conn.close()
        
        final_profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        
        return {
            'ma_type': exit_ma_col.upper(),
            'trades': total_trades,
            'win_rate': win_rate,
            'final_profit_pct': final_profit_pct,
            'final_capital': current_capital
        }
        
    results = []
    for ma in ['ma_10', 'ma_20', 'ma_40', 'ma_60']:
        print(f"[*] {ma.upper()} 기반 익절 로직 시뮬레이션 중...")
        res = run_simulation(grouped, ma)
        results.append(res)
        
    print("\n==========================================================")
    print("      다중 이평선(MA) 기반 트레일링스탑 익절 성과 비교 (1개월)")
    print("==========================================================")
    print(f"{'MA 라인':<10} | {'매매횟수':<8} | {'승률':<8} | {'복리수익률(%)':<10} | {'최종자본금'}")
    print("-" * 58)
    for r in results:
        print(f"{r['ma_type']:<10} | {r['trades']:<12} | {r['win_rate']:<9.1f}% | {r['final_profit_pct']:<15.2f}% | {r['final_capital']:,.0f} 원")
    print("==========================================================")

if __name__ == "__main__":
    run_portfolio_backtest()
