import sys
sys.stdout.reconfigure(encoding='utf-8')
with open('era/era_order_manager.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print("--- BLOCK 1 ---")
for i in range(2507, 2517):
    print(f"{i+1}: {repr(lines[i])}")

print("\n--- BLOCK 2 ---")
for i in range(2536, 2542):
    print(f"{i+1}: {repr(lines[i])}")
