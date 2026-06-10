with open(r'c:\Antigravity\AI_T_Agent\era\era_order_manager.log', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print(f"Total lines in log: {len(lines)}")
print("=== lines with 2026-06-05 ===")
count = 0
for i, line in enumerate(lines):
    if '2026-06-05' in line or '06-05' in line or '06/05' in line:
        print(f"Line {i}: {line.strip()}")
        count += 1
        if count >= 30:
            print("... (too many matches, showing first 30)")
            break
if count == 0:
    print("No matches for 2026-06-05 or 06-05 in logs.")
    # Show last 20 lines of log
    print("\n=== Last 20 lines of log ===")
    for line in lines[-20:]:
        print(line.strip())
