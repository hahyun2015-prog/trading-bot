import sqlite3
import os

db_path = "c:\\Antigravity\\AI_T_Agent\\unified_data.db"
print(f"Checking database: {db_path}")

if not os.path.exists(db_path):
    print("Database file does not exist!")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get list of tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print("Tables in database:", tables)

for table in tables:
    print(f"\n--- Table: {table} ---")
    try:
        # Get column info
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [col[1] for col in cursor.fetchall()]
        print("Columns:", cols)
        
        # Get count
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print("Row count:", count)
        
        # Get last 5 rows
        cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 5")
        rows = cursor.fetchall()
        for r in rows:
            print(r)
    except Exception as e:
        print("Error reading table:", e)

conn.close()
