import sqlite3
import pandas as pd
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
db_name = "futures_data.db"

if not os.path.exists(db_name):
    print("DB not found")
    sys.exit(1)

conn = sqlite3.connect(db_name)
df = pd.read_sql_query("SELECT date, open, high, low, close FROM futures_ohlcv WHERE code = 'A0567000' ORDER BY date ASC", conn)
conn.close()

df['date_day'] = df['date'].str[:8]
counts = df.groupby('date_day').size()
print("Candle counts per day for A0567000:")
print(counts.tail(10))
