with open("c:\\Antigravity\\AI_T_Agent\\era\\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if "signals" in l:
        print(f"Line {i+1}: {l.strip()}")
