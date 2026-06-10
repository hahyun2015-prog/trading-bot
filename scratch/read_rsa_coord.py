with open("c:\\Antigravity\\AI_T_Agent\\rsa\\rsa_coordinator.py", "r", encoding="utf-8") as f:
    lines = f.readlines()
for idx in range(140, 170):
    if idx < len(lines):
        print(f"{idx+1}: {lines[idx]}", end="")
