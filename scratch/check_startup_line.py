import os

log_path = r"era\era_order_manager.log"

with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

print("=== Log Startup / Connection Lines ===")
for i, line in enumerate(lines):
    if " CommConnect" in line or "SetServerGBCode" in line or "서버 로그인 요청" in line:
        print(f"Line {i:5d}: {line.strip()}")
