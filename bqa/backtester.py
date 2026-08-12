import sqlite3
import os
import sys
import pandas as pd
import numpy as np

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

# 지표 계산 단일 소스 (마이그레이션 4번, 2026-08-12). 이전에는 `ta` 패키지를 썼다.
# 동작 무변경이 목표이므로 ta의 비표준 시맨틱을 그대로 재현하는 ta_* 변형을 쓴다:
#   - 볼린저: ta는 모표준편차(ddof=0). 라이브(era_order_manager)의 ddof=1과 다르다.
#   - RSI:    ta는 Wilder RSI가 아니다(0 시드 EWM). wilder_rsi와 최대 26.15 차이.
# 이 차이는 이번에 고치지 않는다 — 발견 목록 N1/N2 참조.
import indicators as I

def load_futures_data(db_path):
    conn = sqlite3.connect(db_path)
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10100000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
    return df

def run_futures_backtest():
    print("==========================================================")
    print("  국내선물 백테스터 - 볼린저 밴드 & RSI 다이버전스 전략")
    print("==========================================================")
    
    db_path = os.path.join(workspace_root, "futures_data.db")
    df = load_futures_data(db_path)
    if df.empty:
        print("DB에 데이터가 없습니다.")
        return
        
    print(f"데이터 로드 완료: {len(df)}개 캔들 ({df.index[0]} ~ {df.index[-1]})\n")
    
    # 지표 계산
    _closes = df['close'].to_numpy(dtype=float)
    _bb_m, _bb_h, _bb_l = I.bollinger_series(_closes, 20, 2.0, ddof=0)  # ddof=0 = ta 시맨틱
    df['bb_m'] = _bb_m
    df['bb_h'] = _bb_h
    df['bb_l'] = _bb_l

    df['rsi'] = I.ta_rsi_series(_closes, 14)
    df.dropna(inplace=True)
    
    df['rsi_min_5'] = df['rsi'].rolling(window=5).min()
    df['rsi_max_5'] = df['rsi'].rolling(window=5).max()
    df['low_min_5'] = df['low'].rolling(window=5).min()
    df['high_max_5'] = df['high'].rolling(window=5).max()
    
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
        
        for i in range(10, len(df)):
            curr_row = df.iloc[i]
            
            bullish_div = False
            bearish_div = False
            
            if i >= 10:
                prev_5_rsi_min = df['rsi'].iloc[i-10:i-5].min()
                curr_5_rsi_min = df['rsi'].iloc[i-5:i].min()
                prev_5_low_min = df['low'].iloc[i-10:i-5].min()
                curr_5_low_min = df['low'].iloc[i-5:i].min()
                
                if curr_5_low_min <= prev_5_low_min and curr_5_rsi_min > prev_5_rsi_min:
                    bullish_div = True
                    
                prev_5_rsi_max = df['rsi'].iloc[i-10:i-5].max()
                curr_5_rsi_max = df['rsi'].iloc[i-5:i].max()
                prev_5_high_max = df['high'].iloc[i-10:i-5].max()
                curr_5_high_max = df['high'].iloc[i-5:i].max()
                
                if curr_5_high_max >= prev_5_high_max and curr_5_rsi_max < prev_5_rsi_max:
                    bearish_div = True
                    
            current_price = curr_row['close']
            current_time = df.index[i]
            
            force_close = False
            if current_time.hour == 15 and current_time.minute >= 30:
                force_close = True
            elif current_time.hour == 4 and current_time.minute >= 50:
                force_close = True
            
            if position != 0:
                exit_reason = None
                if position == 1:
                    if current_price <= entry_price - STOP_LOSS_PT:
                        exit_reason = "손절"
                    elif current_price >= curr_row['bb_m']:
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
                    elif current_price <= curr_row['bb_m']:
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
                    if current_time.hour >= 15 or current_time.hour < 9:
                        can_enter = False
                elif run_mode == 'NightOnly':
                    if 6 <= current_time.hour < 18:
                        can_enter = False
                
                if can_enter:
                    if curr_row['low'] <= curr_row['bb_l'] and (curr_row['rsi'] <= 30 or bullish_div):
                        position = 1
                        entry_price = current_price + SLIPPAGE_PT
                    elif curr_row['high'] >= curr_row['bb_h'] and (curr_row['rsi'] >= 70 or bearish_div):
                        position = -1
                        entry_price = current_price - SLIPPAGE_PT

        if position != 0:
            last_price = df.iloc[-1]['close']
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

    print("운영 시간대에 따른 백테스트 시뮬레이션을 진행합니다...")
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
    run_futures_backtest()
