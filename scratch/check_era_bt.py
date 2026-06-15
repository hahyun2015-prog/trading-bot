# -*- coding: utf-8 -*-
import sqlite3
import os
import sys
import pandas as pd
import numpy as np

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

from bqa.era_current_backtest import load_futures_data, era_current_strategy

db_path = os.path.join(workspace_root, "futures_data.db")
df = load_futures_data(db_path)

r = era_current_strategy(df, 0.3)
print(f"Trades count: {r['trades']}")
if r['trade_log']:
    print("Trade Log:")
    for t in r['trade_log'][:10]:
        print(t)
else:
    print("No trade log entries.")

print("\nChecking if we have any 08:45-08:55 candles:")
count = 0
for t in df.index:
    if t.hour == 8 and 45 <= t.minute <= 55:
        count += 1
        if count <= 10:
            print(t)
print(f"Total morning exit candles in df: {count}")
