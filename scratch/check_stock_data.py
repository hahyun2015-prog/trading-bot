import sqlite3
conn = sqlite3.connect('kiwoom_data.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("=== kiwoom_data.db 테이블 목록 ===")
for t in tables:
    print(t[0])
    
# Check dates in tables
for t in ['daily_ohlcv', 'intraday_ohlcv', 'stock_ohlcv', 'ohlcv']:
    try:
        cursor.execute(f"SELECT MAX(date) FROM {t}")
        print(f"{t} 최신 날짜:", cursor.fetchone()[0])
    except Exception as e:
        pass
conn.close()
