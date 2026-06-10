import sqlite3
import pandas as pd
import numpy as np
import os

def run_backtest_individual(exit_ma_period):
    conn = sqlite3.connect('unified_data.db')
    query = "SELECT code, date, open, high, low, close, volume FROM daily_ohlcv ORDER BY code, date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])

    all_trades = []
    codes = df['code'].unique()

    for code in codes:
        stock_df = df[df['code'] == code].copy().reset_index(drop=True)
        stock_df['ma_20'] = stock_df['close'].rolling(20).mean()
        stock_df['exit_ma'] = stock_df['close'].rolling(exit_ma_period).mean()
        
        if len(stock_df) < 30:
            continue
            
        in_position = False
        buy_price = 0.0
        buy_date = None
        
        for i in range(1, len(stock_df) - 1):
            row = stock_df.iloc[i]
            prev_row = stock_df.iloc[i-1]
            next_row = stock_df.iloc[i+1]
            
            if not in_position:
                if prev_row['close'] <= prev_row['ma_20'] and row['close'] > row['ma_20']:
                    buy_price = next_row['open'] if next_row['open'] > 0 else row['close']
                    buy_date = next_row['date']
                    in_position = True
            else:
                if prev_row['close'] >= prev_row['exit_ma'] and row['close'] < row['exit_ma']:
                    sell_price = next_row['open'] if next_row['open'] > 0 else row['close']
                    sell_date = next_row['date']
                    
                    pnl_pct = ((sell_price / buy_price) - 1.0) * 100
                    pnl_pct_net = pnl_pct - 0.20 # round trip trading cost (slippage + tax)
                    
                    all_trades.append({
                        'code': code,
                        'buy_date': buy_date,
                        'sell_date': sell_date,
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                        'pnl_pct': pnl_pct_net,
                        'duration': (sell_date - buy_date).days
                    })
                    in_position = False

    trades_df = pd.DataFrame(all_trades)
    return trades_df

def run_backtest_hybrid():
    conn = sqlite3.connect('unified_data.db')
    query = "SELECT code, date, open, high, low, close, volume FROM daily_ohlcv ORDER BY code, date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])

    all_trades = []
    codes = df['code'].unique()

    for code in codes:
        stock_df = df[df['code'] == code].copy().reset_index(drop=True)
        stock_df['ma_20'] = stock_df['close'].rolling(20).mean()
        stock_df['ma_5'] = stock_df['close'].rolling(5).mean()
        stock_df['ma_10'] = stock_df['close'].rolling(10).mean()
        
        if len(stock_df) < 30:
            continue
            
        # position state: 0 = None, 1 = Full, 2 = Half Sold
        pos_state = 0
        buy_price = 0.0
        buy_date = None
        half_sell_price = 0.0
        half_sell_date = None
        
        for i in range(1, len(stock_df) - 1):
            row = stock_df.iloc[i]
            prev_row = stock_df.iloc[i-1]
            next_row = stock_df.iloc[i+1]
            
            # Entry Signal
            if pos_state == 0:
                if prev_row['close'] <= prev_row['ma_20'] and row['close'] > row['ma_20']:
                    buy_price = next_row['open'] if next_row['open'] > 0 else row['close']
                    buy_date = next_row['date']
                    pos_state = 1 # Full Position
            
            # Exit logic when holding full position
            elif pos_state == 1:
                # 1. 10MA breach: Liquidate entire position immediately
                if prev_row['close'] >= prev_row['ma_10'] and row['close'] < row['ma_10']:
                    sell_price = next_row['open'] if next_row['open'] > 0 else row['close']
                    sell_date = next_row['date']
                    
                    pnl_pct = ((sell_price / buy_price) - 1.0) * 100
                    pnl_pct_net = pnl_pct - 0.20
                    
                    all_trades.append({
                        'code': code,
                        'buy_date': buy_date,
                        'sell_date': sell_date,
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                        'pnl_pct': pnl_pct_net,
                        'duration': (sell_date - buy_date).days,
                        'type': 'FULL_10MA_EXIT'
                    })
                    pos_state = 0
                
                # 2. 5MA breach: Sell 50%
                elif prev_row['close'] >= prev_row['ma_5'] and row['close'] < row['ma_5']:
                    half_sell_price = next_row['open'] if next_row['open'] > 0 else row['close']
                    half_sell_date = next_row['date']
                    pos_state = 2 # Remaining Half Position
                    
            # Exit logic when holding remaining half position
            elif pos_state == 2:
                # 10MA breach: Liquidate remaining position
                if prev_row['close'] >= prev_row['ma_10'] and row['close'] < row['ma_10']:
                    sell_price = next_row['open'] if next_row['open'] > 0 else row['close']
                    sell_date = next_row['date']
                    
                    # Net returns: average of first half (5MA) and second half (10MA)
                    pnl_1 = ((half_sell_price / buy_price) - 1.0) * 100
                    pnl_2 = ((sell_price / buy_price) - 1.0) * 100
                    pnl_combined = 0.5 * pnl_1 + 0.5 * pnl_2
                    pnl_pct_net = pnl_combined - 0.20
                    
                    all_trades.append({
                        'code': code,
                        'buy_date': buy_date,
                        'sell_date': sell_date,
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                        'pnl_pct': pnl_pct_net,
                        'duration': (sell_date - buy_date).days,
                        'type': 'HYBRID_EXIT'
                    })
                    pos_state = 0

    trades_df = pd.DataFrame(all_trades)
    return trades_df

def compute_metrics(trades_df):
    if trades_df.empty:
        return {}
    
    total_trades = len(trades_df)
    win_trades = trades_df[trades_df['pnl_pct'] > 0]
    win_rate = (len(win_trades) / total_trades) * 100
    avg_pnl = trades_df['pnl_pct'].mean()
    total_pnl = trades_df['pnl_pct'].sum()
    avg_duration = trades_df['duration'].mean()

    # MDD calculation on equal-weighted equity curve
    trades_df = trades_df.sort_values(by='buy_date').reset_index(drop=True)
    equity = [100.0]
    for pnl in trades_df['pnl_pct']:
        equity.append(equity[-1] * (1 + pnl / 100.0))
    equity = np.array(equity)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity - peaks) / peaks * 100
    mdd = drawdowns.min()

    # Final equity value from 100 initial capital
    final_equity = equity[-1]
    net_return = final_equity - 100.0

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'avg_pnl': avg_pnl,
        'total_pnl': total_pnl,
        'mdd': mdd,
        'avg_duration': avg_duration,
        'net_return': net_return
    }

if __name__ == '__main__':
    print("=" * 60)
    print("  AMATS Swing Strategy Backtest COMPARISON  ")
    print("  (Data: 22 major stocks, ~400 days per stock) ")
    print("=" * 60)

    trades_5 = run_backtest_individual(5)
    trades_10 = run_backtest_individual(10)
    trades_hybrid = run_backtest_hybrid()

    m_5 = compute_metrics(trades_5)
    m_10 = compute_metrics(trades_10)
    m_hybrid = compute_metrics(trades_hybrid)

    print("\n--------------------------------------------------------------")
    print(f"{'Metric':<22} | {'5MA Exit':>10} | {'10MA Exit':>10} | {'Hybrid Exit':>12}")
    print("--------------------------------------------------------------")
    print(f"{'Total Trades':<22} | {m_5['total_trades']:>10} | {m_10['total_trades']:>10} | {m_hybrid['total_trades']:>12}")
    print(f"{'Win Rate':<22} | {m_5['win_rate']:>9.2f}% | {m_10['win_rate']:>9.2f}% | {m_hybrid['win_rate']:>11.2f}%")
    print(f"{'Avg PnL per Trade':<22} | {m_5['avg_pnl']:>+9.2f}% | {m_10['avg_pnl']:>+9.2f}% | {m_hybrid['avg_pnl']:>+11.2f}%")
    print(f"{'Total Cumulative PnL':<22} | {m_5['total_pnl']:>+9.2f}% | {m_10['total_pnl']:>+9.2f}% | {m_hybrid['total_pnl']:>+11.2f}%")
    print(f"{'Compounded Return':<22} | {m_5['net_return']:>+9.2f}% | {m_10['net_return']:>+9.2f}% | {m_hybrid['net_return']:>+11.2f}%")
    print(f"{'Simulation MDD':<22} | {m_5['mdd']:>9.2f}% | {m_10['mdd']:>9.2f}% | {m_hybrid['mdd']:>11.2f}%")
    print(f"{'Avg Holding Days':<22} | {m_5['avg_duration']:>9.1f}d | {m_10['avg_duration']:>9.1f}d | {m_hybrid['avg_duration']:>10.1f}d")
    print("--------------------------------------------------------------")
