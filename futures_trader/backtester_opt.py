import sqlite3
import pandas as pd
import numpy as np
import ta

def load_futures_data():
    conn = sqlite3.connect("futures_data.db")
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10100000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
    return df

def run_futures_backtest_optimized():
    df = load_futures_data()
    if df.empty:
        print("DB에 데이터가 없습니다.")
        return
        
    # 지표 계산
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_m'] = indicator_bb.bollinger_mavg()
    df['bb_h'] = indicator_bb.bollinger_hband()
    df['bb_l'] = indicator_bb.bollinger_lband()
    
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    df.dropna(inplace=True)
    
    # 다이버전스 벡터 계산 (1000배 속도 향상 핵심)
    df['low_min_5'] = df['low'].rolling(window=5).min()
    df['low_min_5_shift_5'] = df['low_min_5'].shift(5)
    df['rsi_min_5'] = df['rsi'].rolling(window=5).min()
    df['rsi_min_5_shift_5'] = df['rsi_min_5'].shift(5)
    
    df['high_max_5'] = df['high'].rolling(window=5).max()
    df['high_max_5_shift_5'] = df['high_max_5'].shift(5)
    df['rsi_max_5'] = df['rsi'].rolling(window=5).max()
    df['rsi_max_5_shift_5'] = df['rsi_max_5'].shift(5)
    
    df['bullish_div'] = (df['low_min_5'] <= df['low_min_5_shift_5']) & (df['rsi_min_5'] > df['rsi_min_5_shift_5'])
    df['bearish_div'] = (df['high_max_5'] >= df['high_max_5_shift_5']) & (df['rsi_max_5'] < df['rsi_max_5_shift_5'])
    
    def run_simulation(run_mode='24H'):
        MULTIPLIER = 250000
        SLIPPAGE_PT = 0.05
        FEE_RATE = 0.00003
        INITIAL_CAPITAL = 30000000
        STOP_LOSS_PT = 1.0
        
        current_capital = INITIAL_CAPITAL
        position = 0 
        entry_price = 0.0
        
        total_trades = 0
        winning_trades = 0
        trade_logs = []
        
        # 벡터 데이터를 리스트로 변환하여 순회 속도 추가 향상
        dates = df.index
        closes = df['close'].values
        lows = df['low'].values
        highs = df['high'].values
        bb_ms = df['bb_m'].values
        bb_hs = df['bb_h'].values
        bb_ls = df['bb_l'].values
        rsis = df['rsi'].values
        bullish_divs = df['bullish_div'].values
        bearish_divs = df['bearish_div'].values
        
        for i in range(10, len(df)):
            curr_date = dates[i]
            current_price = closes[i]
            curr_low = lows[i]
            curr_high = highs[i]
            curr_bb_m = bb_ms[i]
            curr_bb_h = bb_hs[i]
            curr_bb_l = bb_ls[i]
            curr_rsi = rsis[i]
            bullish_div = bullish_divs[i]
            bearish_div = bearish_divs[i]
            
            # 오버나이트 방어 강제 청산 시간
            force_close = False
            if curr_date.hour == 15 and curr_date.minute >= 30:
                force_close = True
            elif curr_date.hour == 4 and curr_date.minute >= 50:
                force_close = True
            
            if position != 0:
                exit_reason = None
                if position == 1:
                    if current_price <= entry_price - STOP_LOSS_PT:
                        exit_reason = "손절"
                    elif current_price >= curr_bb_m:
                        exit_reason = "익절"
                    elif force_close:
                        exit_reason = "장마감"
                        
                    if exit_reason:
                        exit_price = current_price - SLIPPAGE_PT
                        profit_pt = exit_price - entry_price
                        realized_pnl = (profit_pt * MULTIPLIER) - ((entry_price + exit_price) * MULTIPLIER * FEE_RATE)
                        current_capital += realized_pnl
                        position = 0
                        total_trades += 1
                        if realized_pnl > 0: winning_trades += 1
                        trade_logs.append({'type': 'EXIT', 'capital': current_capital})
                        
                elif position == -1:
                    if current_price >= entry_price + STOP_LOSS_PT:
                        exit_reason = "손절"
                    elif current_price <= curr_bb_m:
                        exit_reason = "익절"
                    elif force_close:
                        exit_reason = "장마감"
                        
                    if exit_reason:
                        exit_price = current_price + SLIPPAGE_PT
                        profit_pt = entry_price - exit_price
                        realized_pnl = (profit_pt * MULTIPLIER) - ((entry_price + exit_price) * MULTIPLIER * FEE_RATE)
                        current_capital += realized_pnl
                        position = 0
                        total_trades += 1
                        if realized_pnl > 0: winning_trades += 1
                        trade_logs.append({'type': 'EXIT', 'capital': current_capital})

            if position == 0 and not force_close:
                can_enter = True
                if run_mode == 'DayOnly':
                    if curr_date.hour >= 15 or curr_date.hour < 9:
                        can_enter = False
                elif run_mode == 'NightOnly':
                    if 6 <= curr_date.hour < 18:
                        can_enter = False
                
                if can_enter:
                    if curr_low <= curr_bb_l and (curr_rsi <= 30 or bullish_div):
                        position = 1
                        entry_price = current_price + SLIPPAGE_PT
                    elif curr_high >= curr_bb_h and (curr_rsi >= 70 or bearish_div):
                        position = -1
                        entry_price = current_price - SLIPPAGE_PT

        if position != 0:
            last_price = closes[-1]
            if position == 1:
                exit_price = last_price - SLIPPAGE_PT
                profit_pt = exit_price - entry_price
            else:
                exit_price = last_price + SLIPPAGE_PT
                profit_pt = entry_price - exit_price
                
            realized_pnl = (profit_pt * MULTIPLIER) - ((entry_price + exit_price) * MULTIPLIER * FEE_RATE)
            current_capital += realized_pnl
            total_trades += 1
            if realized_pnl > 0: winning_trades += 1
            trade_logs.append({'type': 'EXIT', 'capital': current_capital})

        profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        mdd = 0
        if trade_logs:
            equity_curve = [log['capital'] for log in trade_logs if log['type'] == 'EXIT']
            if equity_curve:
                peak = equity_curve[0]
                mdd = 0
                for eq in equity_curve:
                    if eq > peak:
                        peak = eq
                    dd = (peak - eq) / peak * 100
                    if dd > mdd:
                        mdd = dd

        return total_trades, win_rate, profit_pct, mdd

    results = []
    
    # 1. 주간장 전용
    trades, wr, prof, mdd = run_simulation(run_mode='DayOnly')
    results.append({'Mode': '주간장 전용 (09:00~15:00)', 'Trades': trades, 'WinRate': wr, 'Profit': prof, 'MDD': mdd})
    
    # 2. 야간장 전용
    trades, wr, prof, mdd = run_simulation(run_mode='NightOnly')
    results.append({'Mode': '야간장 전용 (18:00~04:50)', 'Trades': trades, 'WinRate': wr, 'Profit': prof, 'MDD': mdd})
    
    # 3. 주야간 통합 24시간
    trades, wr, prof, mdd = run_simulation(run_mode='24H')
    results.append({'Mode': '주야간 24시간 풀가동', 'Trades': trades, 'WinRate': wr, 'Profit': prof, 'MDD': mdd})

    print("\n=========================================================================")
    print("      국내선물 백테스트: 최적 운영 시간대 분석 (손절 1.0pt, Strict)")
    print("=========================================================================")
    print(f"{'매매 시간대':<28} | {'매매횟수':<8} | {'승률(%)':<8} | {'수익률(%)':<10} | {'MDD(%)'}")
    print("-" * 73)
    for r in results:
        print(f"{r['Mode']:<25} | {r['Trades']:<12} | {r['WinRate']:<9.1f} | {r['Profit']:<12.2f} | {r['MDD']:.2f}%")
    print("=========================================================================")

if __name__ == "__main__":
    run_futures_backtest_optimized()
