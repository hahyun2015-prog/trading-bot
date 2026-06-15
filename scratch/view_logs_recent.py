import os

def print_tail(title, path, num_lines=50):
    print(f"\n==================================================")
    print(f"  {title}: {path}")
    print(f"==================================================")
    if not os.path.exists(path):
        print("File does not exist.")
        return
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            tail_lines = lines[-num_lines:]
            for line in tail_lines:
                print(line.strip())
    except Exception as e:
        print(f"Error reading file: {e}")

print_tail("ERA Order Manager Log", r"c:\Antigravity\AI_T_Agent\era\era_order_manager.log", 50)
print_tail("TCA Controller Log", r"c:\Antigravity\AI_T_Agent\tca\tca_controller.log", 50)
