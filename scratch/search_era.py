with open("c:\\Antigravity\\AI_T_Agent\\era\\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if "futures_db_path" in l or "futures_data.db" in l:
        print(f"Line {i+1}: {l.strip()}")
