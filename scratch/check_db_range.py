import sqlite3
conn = sqlite3.connect(r'c:\Antigravity\AI_T_Agent\futures_data.db')
c = conn.cursor()
c.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM futures_ohlcv WHERE code='10500000'")
print(c.fetchone())
conn.close()
