import os

search_str = "INSERT INTO signals"
for root, dirs, files in os.walk("c:\\Antigravity\\AI_T_Agent"):
    if "venv32" in root or "__pycache__" in root or ".git" in root:
        continue
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if search_str in content or "signals" in content:
                    # Print lines with signals
                    lines = content.splitlines()
                    for i, l in enumerate(lines):
                        if "INSERT INTO signals" in l or ("signals" in l and "futures" in file):
                            print(f"{file} Line {i+1}: {l.strip()}")
            except Exception as e:
                pass
