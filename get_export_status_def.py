with open(r"era\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "def export_status" in line:
        print(f"Line {i+1}: {line.strip()}")
        # print 50 lines after
        for j in range(i+1, min(len(lines), i+50)):
            print(f"  {j+1}: {lines[j].rstrip()}")
        break
