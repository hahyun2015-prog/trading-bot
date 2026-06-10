with open("c:\\Antigravity\\AI_T_Agent\\era\\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
found = False
for i, l in enumerate(lines):
    if "def _process_futures_tick" in l:
        found = True
        start = i
        break
if found:
    for idx in range(start, start + 120):
        if idx < len(lines):
            # Safe print using errors='replace' or encode/decode
            safe_line = lines[idx].encode('cp949', errors='replace').decode('cp949')
            print(f"{idx+1}: {safe_line}", end="")
else:
    print("Method _process_futures_tick not found")
