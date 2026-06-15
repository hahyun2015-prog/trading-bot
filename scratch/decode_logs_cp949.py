import os

log_path = r"era\era_order_manager.log"
if os.path.exists(log_path):
    print("=== Last 150 Lines of era_order_manager.log (Decoded as CP949) ===")
    try:
        with open(log_path, "r", encoding="cp949", errors="ignore") as f:
            lines = f.readlines()
        for l in lines[-150:]:
            print(l.strip())
    except Exception as e:
        print(f"Error: {e}")
else:
    print("File not found.")
