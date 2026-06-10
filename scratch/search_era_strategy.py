with open("c:\\Antigravity\\AI_T_Agent\\era\\era_order_manager.py", "r", encoding="utf-8", errors="ignore") as f:
    content = f.read()
if "futures_strategy_engine" in content or "strategy_engine" in content:
    print("Found strategy_engine in era_order_manager.py")
else:
    print("Not found in era_order_manager.py")
