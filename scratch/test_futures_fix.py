import sqlite3
import os
from datetime import datetime

db_path = "futures_data.db"
print(f"Testing database at {db_path}...")

# 1. Test database query fix
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # We will test our new _get_today_futures_open query logic using "20260608" as today's date
    today_str = "20260608"
    query_code = "10500000"
    
    cursor.execute(
        "SELECT date, open FROM futures_ohlcv WHERE code = ? AND date LIKE ? ORDER BY date ASC LIMIT 1",
        (query_code, today_str + "%")
    )
    row = cursor.fetchone()
    if row:
        print(f"SUCCESS: Found earliest bar of today: Date={row[0]}, Open={row[1]}")
    else:
        print("FAIL: No bars found for today!")
    conn.close()
except Exception as e:
    print(f"Error checking DB query: {e}")

# 2. Verify SendOrderFO parameter mapping logic
direction_map = {
    "LONG_ENTER":  (1, "2", "LONG 진입 📈"),
    "SHORT_ENTER": (1, "1", "SHORT 진입 📉"),
    "LONG_EXIT":   (2, "1", "LONG 청산 📤"),
    "SHORT_EXIT":  (2, "2", "SHORT 청산 📤"),
}

print("\nVerifying SendOrderFO parameter mappings:")
for sig, (ord_kind, slby_tp, label) in direction_map.items():
    print(f"Signal: {sig:<12} -> ord_kind: {ord_kind} (1=New, 2=Liquidate), slby_tp: {slby_tp} (1=Sell, 2=Buy), Label: {label}")
