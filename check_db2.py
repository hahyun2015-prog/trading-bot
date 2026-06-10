import sqlite3
conn = sqlite3.connect(r"c:\Antigravity\AI_T_Agent\futures_data.db")
cursor = conn.cursor()

# 코드별 최신 날짜 확인
cursor.execute("SELECT code, MAX(SUBSTR(date,1,8)) as latest, MIN(SUBSTR(date,1,8)) as oldest, COUNT(*) as cnt FROM futures_ohlcv GROUP BY code")
rows = cursor.fetchall()
print("=== 코드별 데이터 범위 ===")
for r in rows:
    print(f"  code={r[0]}, 최신={r[1]}, 최오래={r[2]}, 건수={r[3]}")

# signals 테이블 최근 내역
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in cursor.fetchall()]
if "signals" in tables:
    cursor.execute("SELECT * FROM signals ORDER BY rowid DESC LIMIT 10")
    rows = cursor.fetchall()
    print("\n=== signals 최근 10건 ===")
    for r in rows:
        print(r)

conn.close()
