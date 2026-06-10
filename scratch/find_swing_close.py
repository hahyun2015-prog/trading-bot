with open(r'c:\Antigravity\AI_T_Agent\era\era_order_manager.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'def ' in line and ('swing' in line.lower() or 'close' in line.lower() or 'flat' in line.lower()):
        print(f"Line {i+1}: {line.strip()}")
