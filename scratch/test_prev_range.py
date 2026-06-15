import sqlite3
import os
from datetime import datetime

futures_db_path = r"c:\Antigravity\AI_T_Agent\futures_data.db"
real_day_code = "A0566000"
futures_prefix = "105"

print("--- Test Index Futures Range Calculation ---")
conn = sqlite3.connect(futures_db_path)
conn.execute("PRAGMA journal_mode=WAL;")
cursor = conn.cursor()

for query_code in [real_day_code, futures_prefix + "00000"]:
    cursor.execute("""
        SELECT SUBSTR(date, 1, 8) as d, MAX(high) as h, MIN(low) as l
        FROM futures_ohlcv WHERE code = ?
        GROUP BY SUBSTR(date, 1, 8) ORDER BY d DESC LIMIT 3
    """, (query_code,))
    rows = cursor.fetchall()
    if len(rows) >= 1:
        break
conn.close()

print(f"Query Code: {query_code}")
print("Rows retrieved:")
for r in rows:
    print(r)

target_row = None
today_str = datetime.now().strftime("%Y%m%d")
if len(rows) >= 1:
    if rows[0][0] == today_str:
        if len(rows) >= 2:
            target_row = rows[1]
    else:
        target_row = rows[0]

if target_row:
    prev_h, prev_l = target_row[1], target_row[2]
    calc = prev_h - prev_l
    print(f"Result -> Target Date: {target_row[0]}, Range: {calc:.2f}pt (Correct: yesterday's range)")
else:
    print("No target row found!")

print("\n--- Test ISF Range Calculation ---")
fc = "A0566000" # using a code that has data in the DB
conn = sqlite3.connect(futures_db_path)
conn.execute("PRAGMA journal_mode=WAL;")
cursor = conn.cursor()
cursor.execute("""
    SELECT SUBSTR(date,1,8) as d, MAX(high) as h, MIN(low) as l
    FROM isf_ohlcv WHERE code = ?
    GROUP BY d ORDER BY d DESC LIMIT 3
""", (fc,))
rows_isf = cursor.fetchall()
conn.close()

print(f"ISF Rows retrieved for {fc}:")
for r in rows_isf:
    print(r)

target_row_isf = None
if len(rows_isf) >= 1:
    if rows_isf[0][0] == today_str:
        if len(rows_isf) >= 2:
            target_row_isf = rows_isf[1]
    else:
        target_row_isf = rows_isf[0]

if target_row_isf:
    prev_range = target_row_isf[1] - target_row_isf[2]
    print(f"Result -> Target Date: {target_row_isf[0]}, Range: {prev_range:.2f}")
else:
    print("No ISF target row found (normal if isf_ohlcv table is empty or has no code match).")
