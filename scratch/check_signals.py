import sqlite3

def check_today_records(db_path, table_name):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table_name} WHERE timestamp >= '2026-06-05'")
        rows = cursor.fetchall()
        print(f"\n=== {db_path} : {table_name} (2026-06-05) ===")
        if not rows:
            print("데이터 없음.")
        for r in rows:
            print(r)
    except Exception as e:
        print(f"Error {table_name}: {e}")
    conn.close()

check_today_records(r'c:\Antigravity\AI_T_Agent\unified_data.db', 'signals')
check_today_records(r'c:\Antigravity\AI_T_Agent\unified_data.db', 'research_reports')
check_today_records(r'c:\Antigravity\AI_T_Agent\futures_data.db', 'signals')
