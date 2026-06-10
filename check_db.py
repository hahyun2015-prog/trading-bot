import sqlite3
conn = sqlite3.connect(r'c:\Antigravity\AI_T_Agent\futures_data.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("=== 테이블 목록 ===")
for t in tables:
    print(t[0])
try:
    cursor.execute("SELECT SUBSTR(date,1,8) as d, code, COUNT(*) FROM futures_ohlcv GROUP BY SUBSTR(date,1,8), code ORDER BY d DESC LIMIT 10")
    rows = cursor.fetchall()
    print("\n=== futures_ohlcv 최근 데이터 (날짜별) ===")
    for r in rows:
        print(r)
except Exception as e:
    print("futures_ohlcv 오류:", e)
conn.close()
