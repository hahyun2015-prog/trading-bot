import os
import time

era_dir = r"c:\Antigravity\AI_T_Agent\era"
files = [f for f in os.listdir(era_dir) if "era_order_manager" in f]

print("=== File Modtimes in era/ ===")
for f in files:
    path = os.path.join(era_dir, f)
    mtime = os.path.getmtime(path)
    print(f"{f}: {time.ctime(mtime)} (Size: {os.path.getsize(path)} bytes)")

print("\nChecking if daily reset time is 09:00 in other backups:")
for f in files:
    path = os.path.join(era_dir, f)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as file:
            content = file.read()
        if "now.hour == 9 and now.minute == 0" in content:
            print(f"  - {f}: Has 'now.hour == 9 and now.minute == 0'")
        else:
            print(f"  - {f}: Does NOT have it")
    except Exception as e:
        print(f"  - {f}: Error reading ({e})")
