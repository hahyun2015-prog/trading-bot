import sqlite3
conn = sqlite3.connect(r"c:\Antigravity\AI_T_Agent\futures_data.db")
cursor = conn.cursor()

print("=== A0566000 가격 샘플 ===")
cursor.execute("SELECT date, open, high, low, close FROM futures_ohlcv WHERE code='A0566000' ORDER BY date DESC LIMIT 10")
for r in cursor.fetchall():
    print(r)

print("\n=== A0566000 일별 집계 ===")
cursor.execute("""
    SELECT SUBSTR(date, 1, 8) as d, MAX(high) as h, MIN(low) as l, COUNT(*)
    FROM futures_ohlcv WHERE code = 'A0566000'
    GROUP BY SUBSTR(date, 1, 8) ORDER BY d DESC LIMIT 5
""")
for r in cursor.fetchall():
    print(r)

conn.close()
