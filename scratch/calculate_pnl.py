import sys

sys.stdout.reconfigure(encoding='utf-8')

# Constants
POINT_VALUE = 50000  # Mini KOSPI 200 Futures multiplier (50,000 KRW)
COMMISSION_PER_ORDER = 2012.5  # Approx round-turn commission per order (2,012.5 KRW)

# June 22 actual trades:
# Fixed SL was 5.0pt, so dynamic floor of 4.0pt has no effect since SL was already 5.0pt.
# No changes.
j22_actual = [
    {"type": "LONG", "entry": 1471.00, "exit": 1501.50, "pnl": 30.50, "reason": "Take Profit"},
    {"type": "LONG", "entry": 1505.42, "exit": 1505.16, "pnl": -0.26, "reason": "Take Profit (Adjusted)"},
    {"type": "LONG", "entry": 1497.90, "exit": 1492.90, "pnl": -5.00, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1497.28, "exit": 1492.08, "pnl": -5.20, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1495.00, "exit": 1490.08, "pnl": -4.92, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1492.80, "exit": 1487.66, "pnl": -5.14, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1490.96, "exit": 1486.02, "pnl": -4.94, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1489.86, "exit": 1485.02, "pnl": -4.84, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1488.88, "exit": 1495.94, "pnl": 7.06, "reason": "Carry-over Exit (June 23)"}
]

# June 23 actual trades (Baseline: SL=2.40pt):
j23_baseline = [
    {"type": "LONG", "entry": 1483.68, "exit": 1480.96, "pnl": -2.72, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1482.94, "exit": 1480.50, "pnl": -2.44, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1483.62, "exit": 1487.70, "pnl": 4.08, "reason": "Trailing Stop"},
    {"type": "LONG", "entry": 1489.78, "exit": 1489.70, "pnl": -0.08, "reason": "Take Profit (3Sig)"},
    {"type": "LONG", "entry": 1491.98, "exit": 1491.64, "pnl": -0.34, "reason": "Take Profit (3Sig)"},
    {"type": "LONG", "entry": 1486.08, "exit": 1483.78, "pnl": -2.30, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1486.06, "exit": 1483.60, "pnl": -2.46, "reason": "Stop Loss"}
]

# June 23 Dynamic Floor (SL=4.00pt):
# Under Dynamic SL, the stopped-out trades are hit at 4.0pt instead of 2.4pt.
j23_dynamic = [
    {"type": "LONG", "entry": 1483.68, "exit": 1479.68, "pnl": -4.00, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1482.94, "exit": 1478.94, "pnl": -4.00, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1483.62, "exit": 1487.70, "pnl": 4.08, "reason": "Trailing Stop"},
    {"type": "LONG", "entry": 1489.78, "exit": 1489.70, "pnl": -0.08, "reason": "Take Profit (3Sig)"},
    {"type": "LONG", "entry": 1491.98, "exit": 1491.64, "pnl": -0.34, "reason": "Take Profit (3Sig)"},
    {"type": "LONG", "entry": 1486.08, "exit": 1482.08, "pnl": -4.00, "reason": "Stop Loss"},
    {"type": "LONG", "entry": 1486.06, "exit": 1482.06, "pnl": -4.00, "reason": "Stop Loss"}
]

# June 24 actual trades (Baseline: SL=2.40pt):
j24_baseline = [
    {"type": "SHORT", "entry": 1341.78, "exit": 1344.38, "pnl": -2.60, "reason": "Stop Loss"},
    {"type": "SHORT", "entry": 1338.52, "exit": 1340.92, "pnl": -2.40, "reason": "Stop Loss"},
    {"type": "SHORT", "entry": 1357.00, "exit": 1348.28, "pnl": 8.72, "reason": "Trailing Stop"},
    {"type": "SHORT", "entry": 1341.88, "exit": 1344.42, "pnl": -2.54, "reason": "Stop Loss"}
]

# June 24 Dynamic Floor (SL=4.00pt):
# Under Dynamic SL, the stopped-out trades are hit at 4.0pt instead of 2.4pt.
j24_dynamic = [
    {"type": "SHORT", "entry": 1341.78, "exit": 1345.78, "pnl": -4.00, "reason": "Stop Loss"},
    {"type": "SHORT", "entry": 1338.52, "exit": 1342.52, "pnl": -4.00, "reason": "Stop Loss"},
    {"type": "SHORT", "entry": 1357.00, "exit": 1348.28, "pnl": 8.72, "reason": "Trailing Stop"},
    {"type": "SHORT", "entry": 1341.88, "exit": 1345.88, "pnl": -4.00, "reason": "Stop Loss"}
]

def analyze_day(name, baseline_trades, dynamic_trades, carry_over_baseline=0, carry_over_dynamic=0):
    print(f"\n--- {name} Analysis ---")
    
    # Calculate baseline
    b_pts = sum(t["pnl"] for t in baseline_trades) + carry_over_baseline
    b_gross = b_pts * POINT_VALUE
    b_orders = len(baseline_trades) * 2 + (1 if carry_over_baseline != 0 else 0)
    b_comm = b_orders * COMMISSION_PER_ORDER
    b_net = b_gross - b_comm
    b_sl = sum(1 for t in baseline_trades if t["reason"] == "Stop Loss")
    b_tp = sum(1 for t in baseline_trades if "Take Profit" in t["reason"])
    b_ts = sum(1 for t in baseline_trades if t["reason"] == "Trailing Stop")
    
    # Calculate dynamic
    d_pts = sum(t["pnl"] for t in dynamic_trades) + carry_over_dynamic
    d_gross = d_pts * POINT_VALUE
    d_orders = len(dynamic_trades) * 2 + (1 if carry_over_dynamic != 0 else 0)
    d_comm = d_orders * COMMISSION_PER_ORDER
    d_net = d_gross - d_comm
    d_sl = sum(1 for t in dynamic_trades if t["reason"] == "Stop Loss")
    d_tp = sum(1 for t in dynamic_trades if "Take Profit" in t["reason"])
    d_ts = sum(1 for t in dynamic_trades if t["reason"] == "Trailing Stop")
    
    print(f"Baseline: Trades={len(baseline_trades)}, SL={b_sl}, TP={b_tp}, TS={b_ts}, PnL={b_pts:+.2f} pt | Gross={b_gross:+,.0f}원 | Comm={b_comm:,.0f}원 | Net={b_net:+,.0f}원")
    print(f"Dynamic:  Trades={len(dynamic_trades)}, SL={d_sl}, TP={d_tp}, TS={d_ts}, PnL={d_pts:+.2f} pt | Gross={d_gross:+,.0f}원 | Comm={d_comm:,.0f}원 | Net={d_net:+,.0f}원")
    print(f"Changes:  SL={d_sl-b_sl:+d}, TP={d_tp-b_tp:+d}, TS={d_ts-b_ts:+d}, PnL={d_pts-b_pts:+.2f} pt | Net PnL={d_net-b_net:+,.0f}원")
    
    return {
        "b_sl": b_sl, "b_tp": b_tp, "b_ts": b_ts, "b_net": b_net, "b_pts": b_pts,
        "d_sl": d_sl, "d_tp": d_tp, "d_ts": d_ts, "d_net": d_net, "d_pts": d_pts
    }

print("================================================================================")
print("  COMPARING BASELINE (ACTUAL) vs DYNAMIC STOP LOSS FLOOR (RECOMMENDED)")
print("================================================================================")

r22 = analyze_day("June 22 (Floor 5.0 already applied, no change)", j22_actual[:-1], j22_actual[:-1], carry_over_baseline=j22_actual[-1]["pnl"], carry_over_dynamic=j22_actual[-1]["pnl"])
r23 = analyze_day("June 23 (Kalman Strategy SL 2.40pt -> Floor 4.00pt)", j23_baseline, j23_dynamic)
r24 = analyze_day("June 24 (Today, Kalman Strategy SL 2.40pt -> Floor 4.00pt)", j24_baseline, j24_dynamic)

print("\n" + "="*80)
print("  AGGREGATE 3-DAY SUMMARY")
print("="*80)
total_b_sl = r22["b_sl"] + r23["b_sl"] + r24["b_sl"]
total_d_sl = r22["d_sl"] + r23["d_sl"] + r24["d_sl"]
total_b_tp = r22["b_tp"] + r23["b_tp"] + r24["b_tp"]
total_d_tp = r22["d_tp"] + r23["d_tp"] + r24["d_tp"]
total_b_ts = r22["b_ts"] + r23["b_ts"] + r24["b_ts"]
total_d_ts = r22["d_ts"] + r23["d_ts"] + r24["d_ts"]
total_b_net = r22["b_net"] + r23["b_net"] + r24["b_net"]
total_d_net = r22["d_net"] + r23["d_net"] + r24["d_net"]

print(f"Total Baseline: SL={total_b_sl}, TP={total_b_tp}, TS={total_b_ts}, Net Profit={total_b_net:+,.0f}원")
print(f"Total Dynamic:  SL={total_d_sl}, TP={total_d_tp}, TS={total_d_ts}, Net Profit={total_d_net:+,.0f}원")
print(f"Aggregate Difference: SL={total_d_sl-total_b_sl:+d}, TP={total_d_tp-total_b_tp:+d}, TS={total_d_ts-total_b_ts:+d}, Net PnL={total_d_net-total_b_net:+,.0f}원")
print("="*80)
