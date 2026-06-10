import sqlite3
db_path = "c:\\Antigravity\\AI_T_Agent\\unified_data.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT * FROM research_reports")
rows = cursor.fetchall()
for r in rows:
    print(r)
conn.close()
