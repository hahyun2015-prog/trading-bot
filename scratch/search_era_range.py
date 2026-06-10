with open("c:\\Antigravity\\AI_T_Agent\\era\\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
found = False
for i, l in enumerate(lines):
    if "def get_prev_range" in l or "def _get_today_futures_open" in l:
        found = True
        start = i
        break
if found:
    for idx in range(start - 5, start + 80):
        if idx < len(lines):
            safe_line = lines[idx].encode('cp949', errors='replace').decode('cp949')
            print(f"{idx+1}: {safe_line}", end="")
else:
    print("get_prev_range not found")
