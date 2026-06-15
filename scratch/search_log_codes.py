import os

log_path = r"era\era_order_manager.log"
codes = ["000430", "241790", "255440", "383310", "040160", "042700"]

print("=== Log Search for Today's Stock Codes ===")
if os.path.exists(log_path):
    with open(log_path, "r", encoding="cp949", errors="ignore") as f:
        lines = f.readlines()
    
    for code in codes:
        print(f"\n--- Code: {code} ---")
        found = False
        for line in lines:
            if code in line:
                print(line.strip())
                found = True
        if not found:
            print("No log entries found for this code.")
else:
    print("Log file not found.")
