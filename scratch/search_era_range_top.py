with open("c:\\Antigravity\\AI_T_Agent\\era\\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
for idx in range(1740, 1795):
    if idx < len(lines):
        safe_line = lines[idx].encode('cp949', errors='replace').decode('cp949')
        print(f"{idx+1}: {safe_line}", end="")
