import os

search_str = "분석 대상"
for root, dirs, files in os.walk("c:\\Antigravity\\AI_T_Agent"):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                if search_str in content:
                    print(f"Found in {path}")
                    # Print matching lines
                    lines = content.splitlines()
                    for i, l in enumerate(lines):
                        if search_str in l:
                            print(f"  Line {i+1}: {l}")
            except Exception as e:
                pass
