import sqlite3
import os

unified_db = r"c:\Antigravity\AI_T_Agent\unified_data.db"
conn = sqlite3.connect(unified_db)
cur = conn.cursor()

for tname in ['daily_balance_history', 'research_reports']:
    print(f"\n--- {tname} schema ---")
    cur.execute(f"PRAGMA table_info({tname})")
    for col in cur.fetchall():
        print(col)
    
    print(f"\n--- {tname} sample data (3 rows) ---")
    cur.execute(f"SELECT * FROM {tname} LIMIT 3")
    for row in cur.fetchall():
        print(row)

conn.close()
