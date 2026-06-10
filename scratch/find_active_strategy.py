import os
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

keywords = ['AI_Trading_Data', 'copy', 'xcopy', 'robocopy']
root_dir = r'c:\Antigravity\AI_T_Agent'

for dirpath, _, filenames in os.walk(root_dir):
    if 'venv32' in dirpath or '.git' in dirpath:
        continue
    for filename in filenames:
        if filename.endswith('.py') or filename.endswith('.bat'):
            path = os.path.join(dirpath, filename)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                for i, line in enumerate(lines):
                    for kw in keywords:
                        if kw in line:
                            # print only if it looks related to config or sync
                            if 'config' in line or 'strategy' in line or 'drive' in line.lower() or 'g:' in line.lower():
                                print(f"{filename}:{i+1} -> {line.strip()}")
                                break
            except Exception as e:
                pass
print("Done!")
