import sqlite3
conn = sqlite3.connect(r'c:\Antigravity\AI_T_Agent\unified_data.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("=== unified_data.db 테이블 목록 ===")
for t in tables:
    print(t[0])

for table in ['daily_balance_history', 'signals', 'trades', 'orders']:
    try:
        cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 5")
        rows = cursor.fetchall()
        print(f"\n=== {table} 최근 5건 ===")
        for r in rows:
            print(r)
    except Exception as e:
        print(f"{table} 조회 오류:", e)
conn.close()
