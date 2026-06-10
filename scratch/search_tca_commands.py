import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open(r'c:\Antigravity\AI_T_Agent\tca\tca_controller.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print("=== TCA Telegram commands ===")
for i, line in enumerate(lines):
    if 'cmd_text ==' in line or 'elif cmd_text' in line or 'if cmd_text' in line or 'startswith' in line:
        print(f"Line {i+1}: {line.strip()}")
