import sqlite3
import json
import os
import re
import urllib.request
from datetime import datetime

today_str = "2026-06-04"
status_path = r"tca\system_status.json"
log_path = r"era\era_order_manager.log"

# Resolution of Stock Names
stock_names = {
    "001420": "태원물산",
    "001820": "삼화콘덴서",
    "049550": "잉크테크",
    "053980": "오상자이엘",
    "131030": "옵투스제약",
    "171010": "램테크놀러지",
    "240810": "원익IPS",
    "241790": "티이엠씨씨엔에스",
    "265520": "AP시스템",
    "272290": "이녹스첨단소재",
    "285800": "진영",
    "309930": "조이웍스앤코",
    "440110": "파두"
}

def get_stock_name(code):
    if code in stock_names:
        return stock_names[code]
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('cp949', errors='ignore')
        m = re.search(r'<title>(.*?) : 네이버', html)
        if m:
            name = m.group(1).strip()
            stock_names[code] = name
            return name
    except Exception:
        pass
    return "Unknown"

# 1. Fetch Today's Signals from DB
signals = []
if os.path.exists("unified_data.db"):
    try:
        conn = sqlite3.connect("unified_data.db")
        cur = conn.cursor()
        cur.execute("SELECT id, code, name, strategy_type, price, open_price, timestamp, status FROM signals WHERE timestamp LIKE '2026-06-04%' ORDER BY id ASC")
        for r in cur.fetchall():
            sid, code, _, strat_type, price, open_price, ts, status = r
            name = get_stock_name(code)
            signals.append({
                "id": sid,
                "code": code,
                "name": name,
                "strategy": strat_type,
                "price": price,
                "open_price": open_price,
                "time": ts,
                "status": status
            })
        conn.close()
    except Exception as e:
        print("Error fetching signals:", e)

# 2. Extract Sell Orders / Auto Liquidation from Logs
sells = []
if os.path.exists(log_path):
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        
        # We search the whole log for today's date lines, but wait, the log lines don't always have dates.
        # Let's search for "자동 청산" (auto liquidation) or "매도" or "손절" or "청산"
        # We also look at when the current session started and parse events after that.
        # Let's search for lines containing "자동 청산 발동" or "매도주문" or "손절"
        for i, line in enumerate(lines):
            # Check if this line is from today or near the end
            # Let's look for "자동 청산" or "청산" or "손절" or "매도"
            if "자동 청산" in line or "û" in line or "손절" in line or "매도" in line or "û ߵ" in line or "û" in line:
                # Get surrounding lines (up to 3 before and 3 after) for context
                context = [lines[j].strip() for j in range(max(0, i-2), min(len(lines), i+3))]
                sells.append((line.strip(), context))
    except Exception as e:
        print("Error parsing logs:", e)

# 3. Read Current Holdings
current_holdings = {}
if os.path.exists(status_path):
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            status_data = json.load(f)
            current_holdings = status_data
    except Exception as e:
        print("Error loading system status:", e)

# Save Report data as JSON so we can format it easily
report = {
    "date": today_str,
    "signals": signals,
    "current_holdings": current_holdings,
    "sells": [s[0] for s in sells]
}

with open("today_report_raw.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=4)

print("Report gathered successfully.")
