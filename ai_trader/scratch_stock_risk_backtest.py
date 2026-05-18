import sqlite3
import pandas as pd
import numpy as np
import sys
import warnings
warnings.filterwarnings("ignore")

sys.path.append(r"c:\antigravity\노트븍활용\ai_trader")
from strategy_engine import calculate_vwap, apply_rsi, check_hidden_bullish_divergence, check_vwap_pullback

def load_data(conn):
    query = "SELECT DISTINCT code FROM intraday_ohlcv"
    cursor = conn.cursor()
    cursor.execute(query)
    codes = [row[0] for row in cursor.fetchall()]
    return codes

def run_stock_simulation(use_safety=False):
    conn = sqlite3.connect(r"c:\antigravity\노트븍활용\ai_trader\kiwoom_data.db")
    codes = load_data(conn)
    
    all_data = []
    print("데이터 로딩 및 시그널 연산 중...")
    for idx, code in enumerate(codes):
        query = f"SELECT date, open, high, low, close, volume FROM intraday_ohlcv WHERE code = '{code}' ORDER BY date ASC"
        df = pd.read_sql_query(query, conn)
        if len(df) < 60: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        df['code'] = code
        
        is_buy = np.zeros(len(df), dtype=bool)
        for i in range(60, len(df)):
            window = df.iloc[:i+1]
            if not check_vwap_pullback(window): continue
            if not check_hidden_bullish_divergence(window): continue
            if df.iloc[i]['close'] < (df.iloc[i]['ma_10'] * 0.99): continue
            is_buy[i] = True
            
        df['is_buy_signal'] = is_buy
        all_data.append(df)
        
    combined_df = pd.concat(all_data)
    combined_df.sort_index(inplace=True)
    grouped = combined_df.groupby(combined_df.index)
    
    INITIAL_CAPITAL = 100000000
    current_capital = INITIAL_CAPITAL
    available_capital = INITIAL_CAPITAL
    portfolio = {}
    MAX_POS = 8
    
    total_trades = 0
    winning_trades = 0
    
    # Safety variables
    daily_realized_loss = 0.0
    system_halted = False
    current_day = None
    
    # 3 consecutive stop-loss tracking
    stop_loss_history = [] # list of timestamps when a stop loss occurred

    for current_time, group_df in grouped:
        day_str = current_time.strftime('%Y-%m-%d')
        if current_day != day_str:
            current_day = day_str
            daily_realized_loss = 0.0
            system_halted = False
            stop_loss_history = []
            
        codes_to_remove = []
        for code, pos in portfolio.items():
            if code in group_df['code'].values:
                row = group_df[group_df['code'] == code].iloc[0]
                current_price = row['close']
                profit_ratio = (current_price - pos['buy_price']) / pos['buy_price']
                sell_reason = None
                is_stop_loss = False
                
                if profit_ratio <= -0.02:
                    sell_reason = "손절"
                    is_stop_loss = True
                elif current_price < row['ma_10'] and profit_ratio > 0.01:
                    sell_reason = "익절"
                    
                if sell_reason:
                    net_profit = profit_ratio - 0.0045
                    profit_amount = pos['invested'] * net_profit
                    
                    available_capital += pos['invested'] + profit_amount
                    current_capital += profit_amount
                    total_trades += 1
                    
                    if net_profit > 0:
                        winning_trades += 1
                    else:
                        daily_realized_loss += abs(profit_amount)
                        if is_stop_loss:
                            stop_loss_history.append(current_time)
                            
                    codes_to_remove.append(code)
                    
        for code in codes_to_remove:
            del portfolio[code]
            
        # Safety Check
        if use_safety and not system_halted:
            # Condition 1: 3% daily loss limit
            if daily_realized_loss >= INITIAL_CAPITAL * 0.03:
                system_halted = True
                
            # Condition 2: 3 consecutive stop-losses within 30 minutes
            if not system_halted and len(stop_loss_history) >= 3:
                recent_stops = [t for t in stop_loss_history if (current_time - t).total_seconds() <= 1800]
                if len(recent_stops) >= 3:
                    system_halted = True
                    # If we only want to pause for 2 hours instead of whole day, we could do it here.
                    # But for simplicity, we treat it as a day halt or a 2-hour halt. 
                    # Let's do a hard halt for the day to be safe.
                    
        if len(portfolio) < MAX_POS and not system_halted:
            buy_candidates = group_df[group_df['is_buy_signal'] == True]
            for _, row in buy_candidates.iterrows():
                code = row['code']
                if code not in portfolio and len(portfolio) < MAX_POS:
                    alloc = current_capital / MAX_POS
                    if available_capital >= alloc * 0.99:
                        invested = min(alloc, available_capital)
                        available_capital -= invested
                        portfolio[code] = {'buy_price': row['close'], 'invested': invested}
                        
    conn.close()
    
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    profit_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    
    return {
        'name': "안전장치 적용 (손실 3% + 3연속 손절 셧다운)" if use_safety else "주식 원본 (안전장치 없음)",
        'trades': total_trades,
        'win_rate': win_rate,
        'profit_pct': profit_pct
    }

def main():
    print("백테스트 시뮬레이션 시작...\n")
    res1 = run_stock_simulation(use_safety=False)
    res2 = run_stock_simulation(use_safety=True)
    
    results = [res1, res2]
    
    print("\n=========================================================================")
    print(f"{'전략 구성 (주식)':<35} | {'매매횟수':<6} | {'승률':<6} | {'자본수익률(%)'}")
    print("-" * 65)
    for r in results:
        print(f"{r['name']:<35} | {r['trades']:<8} | {r['win_rate']:<7.1f}% | {r['profit_pct']:<10.2f}%")
    print("=========================================================================")

if __name__ == "__main__":
    main()
