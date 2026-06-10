import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect('unified_data.db')
df = pd.read_sql_query("SELECT code, date, open, high, low, close FROM daily_ohlcv ORDER BY code, date ASC", conn)
conn.close()

print(f"Total rows in daily_ohlcv: {len(df)}")
print(f"Min Open: {df['open'].min()}")
print(f"Min Close: {df['close'].min()}")
print(f"Rows with zero open: {len(df[df['open'] == 0])}")
print(f"Rows with zero close: {len(df[df['close'] == 0])}")

# Let's run the 5MA backtest and look at the trade list
from hybrid_swing_backtest import run_backtest_individual
trades_5 = run_backtest_individual(5)
print(f"\nTotal trades (5MA): {len(trades_5)}")
if not trades_5.empty:
    print(f"Min PnL (5MA): {trades_5['pnl_pct'].min():.2f}%")
    print(f"Max PnL (5MA): {trades_5['pnl_pct'].max():.2f}%")
    print(f"Trades with PnL <= -100%: {len(trades_5[trades_5['pnl_pct'] <= -100])}")
    print("\nWorst 5 trades (5MA):")
    print(trades_5.sort_values(by='pnl_pct').head(5)[['code', 'buy_date', 'sell_date', 'buy_price', 'sell_price', 'pnl_pct']])
