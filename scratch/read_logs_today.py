import os

today_str = "2026-06-12"
log_files = [
    r"era\era_order_manager.log",
    r"tca\tca_controller.log",
    r"era\era_crash.log"
]

print("=== Today's Log Excerpts ===")
for filepath in log_files:
    if os.path.exists(filepath):
        print(f"\n--- {filepath} ---")
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            
            # Find lines containing today's date
            today_lines = []
            for line in lines:
                if today_str in line or "06-12" in line or "260612" in line:
                    today_lines.append(line.strip())
            
            if today_lines:
                print(f"Found {len(today_lines)} lines for today. Printing last 30 lines:")
                for l in today_lines[-30:]:
                    print(l)
            else:
                print("No lines matching today's date. Printing last 10 lines of the file:")
                for l in lines[-10:]:
                    print(l.strip())
        except Exception as e:
            print(f"Error reading {filepath}: {e}")
    else:
        print(f"{filepath} does not exist.")
