import sqlite3
import json
import os
import re
from datetime import datetime

today_str = "2026-06-12"
print(f"=========================================")
print(f"  TRADING REPORT FOR {today_str}")
print(f"=========================================\n")

# 1. Database - Signals and Research Reports
db_path = "unified_data.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Stock Signals
    print("--- [1] Today's Stock Signals in DB ---")
    cursor.execute("SELECT id, code, name, strategy_type, price, open_price, timestamp, status FROM signals WHERE timestamp LIKE ? ORDER BY id ASC", (f"{today_str}%",))
    signals = cursor.fetchall()
    if not signals:
        print("No stock signals found for today.")
    for sig in signals:
        print(f"ID: {sig[0]} | Code: {sig[1]} | Name: {sig[2]} | Strategy: {sig[3]} | Price: {sig[4]:,} | Open Price: {sig[5]:,} | Time: {sig[6]} | Status: {sig[7]}")
    
    # Research Reports
    print("\n--- [2] Today's Research Reports in DB ---")
    cursor.execute("SELECT code, name, strategy_type, score, timestamp FROM research_reports WHERE timestamp LIKE ? ORDER BY id ASC", (f"{today_str}%",))
    reports = cursor.fetchall()
    if not reports:
        print("No research reports found for today.")
    for rep in reports:
        print(f"Code: {rep[0]} | Name: {rep[1]} | Strategy: {rep[2]} | Score: {rep[3]} | Time: {rep[4]}")
        
    # Daily Balance History
    print("\n--- [3] Today's Balance History ---")
    cursor.execute("SELECT date, stock_total, futures_total, combined_total FROM daily_balance_history WHERE date = ?", (today_str,))
    balance = cursor.fetchone()
    if balance:
        print(f"Date: {balance[0]} | Stock Total: {balance[1]:,} | Futures Total: {balance[2]:,} | Combined Total: {balance[3]:,}")
    else:
        print("No balance history entry found for today.")
        
    conn.close()
else:
    print("Database unified_data.db not found.")

# 2. Futures Signals
f_db_path = "futures_data.db"
if os.path.exists(f_db_path):
    conn = sqlite3.connect(f_db_path)
    cursor = conn.cursor()
    print("\n--- [4] Today's Futures Signals in DB ---")
    cursor.execute("SELECT id, code, signal_type, price, timestamp, status FROM signals WHERE timestamp LIKE ? ORDER BY id ASC", (f"{today_str}%",))
    f_signals = cursor.fetchall()
    if not f_signals:
        print("No futures signals found for today.")
    for sig in f_signals:
        print(f"ID: {sig[0]} | Code: {sig[1]} | Signal: {sig[2]} | Price: {sig[3]} | Time: {sig[4]} | Status: {sig[5]}")
    conn.close()
else:
    print("Database futures_data.db not found.")

# 3. System Status (tca/system_status.json)
status_path = r"tca\system_status.json"
if os.path.exists(status_path):
    print("\n--- [5] System Status (tca/system_status.json) ---")
    with open(status_path, "r", encoding="utf-8") as f:
        status = json.load(f)
    print(f"Environment: {status.get('environment')}")
    print(f"Trading Mode: {status.get('trading_mode')}")
    print(f"Stock Account: {status.get('stock_account')}")
    print(f"Futures Account: {status.get('futures_account')}")
    print(f"Total Balance: {status.get('total_balance'):,}")
    print(f"Budget Day: {status.get('budget_day'):,}")
    print(f"Budget Swing: {status.get('budget_swing'):,}")
    print(f"Daily Realized Loss: {status.get('daily_realized_loss'):,}")
    print(f"Monthly Realized Loss: {status.get('monthly_realized_loss'):,}")
    
    print("\n--- Portfolio (Stock Positions) ---")
    portfolio = status.get("portfolio", {})
    if not portfolio:
        print("No open stock positions.")
    for code, info in portfolio.items():
        print(f"  {info.get('name')}({code}) | Strat: {info.get('strategy')} | Qty: {info.get('qty')} | Buy: {info.get('buy_price'):,} | Curr: {info.get('current_price'):,} | Max: {info.get('max_price'):,} | Date: {info.get('entry_date')} | Sell Ordered: {info.get('sell_ordered')}")
        
    print("\n--- Futures Positions ---")
    f_positions = status.get("futures_positions", {})
    if not f_positions:
        print("No open futures positions.")
    for name, info in f_positions.items():
        print(f"  {name} | Type: {info.get('type')} | Qty: {info.get('qty')} | Entry Price: {info.get('price')} | Curr Price: {info.get('current_price')}")
        
    print("\n--- Futures Strategy Config ---")
    f_strat = status.get("futures_strategy", {})
    for k, v in f_strat.items():
        print(f"  {k}: {v}")
        
    print(f"\nLast Updated: {status.get('last_updated')}")
else:
    print("System status file not found.")

# 4. Logs (era/era_order_manager.log) - Check last 30 lines and lines mentioning "2026-06-12"
log_path = r"era\era_order_manager.log"
if os.path.exists(log_path):
    print("\n--- [6] Era Order Manager Log (Today's entries / Last 30 lines) ---")
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        today_lines = [line.strip() for line in lines if today_str in line or "06-12" in line]
        if today_lines:
            print(f"Found {len(today_lines)} log lines for today. Printing last 20:")
            for line in today_lines[-20:]:
                print(line)
        else:
            print("No log lines found containing the date. Printing last 20 lines of log:")
            for line in lines[-20:]:
                print(line.strip())
    except Exception as e:
        print(f"Error reading log: {e}")
