import os

log_path = r"era\era_order_manager.log"
if os.path.exists(log_path):
    print("=== Last 100 Lines of era_order_manager.log ===")
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    for l in lines[-100:]:
        print(l.strip())
else:
    print("File not found.")
