import sys

sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')

with open(r"era\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "research_reports" in line or "score" in line.lower() or "rsa" in line.lower():
        # print 5 lines before and 5 lines after
        print(f"Line {i+1}: {line.strip()}")
        start = max(0, i - 8)
        end = min(len(lines), i + 8)
        print("Context:")
        for j in range(start, end):
            prefix = "-> " if j == i else "   "
            print(f"{prefix}{j+1}: {lines[j].rstrip()}")
        print("-" * 50)
