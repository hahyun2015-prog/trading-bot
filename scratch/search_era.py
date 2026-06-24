patterns = [
    "if res == 0:",
    "res == 0"
]
with open("c:\\Antigravity\\AI_T_Agent\\era\\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    for pat in patterns:
        if pat in l:
            try:
                print(f"Line {i+1} ({pat}): {l.strip()}")
            except UnicodeEncodeError:
                print(f"Line {i+1} ({pat}): {l.strip().encode('ascii', errors='replace').decode('ascii')}")


