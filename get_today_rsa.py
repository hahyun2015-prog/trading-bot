import sqlite3
import os

db_path = "unified_data.db"
print("=== Today's RSA Reports in DB ===")
if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # Find column names
        cur.execute("PRAGMA table_info(research_reports)")
        cols = [c[1] for c in cur.fetchall()]
        print("Columns:", cols)
        
        # Query reports from today
        cur.execute("SELECT code, name, strategy_type, score, timestamp FROM research_reports WHERE timestamp LIKE '2026-06-04%' ORDER BY id ASC")
        rows = cur.fetchall()
        print(f"Total reports found for today: {len(rows)}")
        for r in rows:
            print(f"Code: {r[0]} | Name: {r[1]} | Type: {r[2]} | Score: {r[3]} | Time: {r[4]}")
            
        # Also let's check if all executed signals had an RSA report
        print("\n=== Checking executed signals vs RSA scores ===")
        cur.execute("SELECT DISTINCT code, name, status FROM signals WHERE timestamp LIKE '2026-06-04%'")
        signals = cur.fetchall()
        for code, name, status in signals:
            cur.execute("SELECT score FROM research_reports WHERE code = ? AND timestamp LIKE '2026-06-04%' ORDER BY id DESC LIMIT 1", (code,))
            score_row = cur.fetchone()
            score_val = score_row[0] if score_row else "No report today"
            print(f"Signal: {name}({code}) | Status: {status} | Today's RSA Score: {score_val}")
            
        conn.close()
    except Exception as e:
        print("Error:", e)
else:
    print("Database not found")
