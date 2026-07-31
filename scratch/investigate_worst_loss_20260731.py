import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica
from scratch.final_live_config_backtest_20260731 import FINAL_KW, BASELINE_KW

df = load_futures_data('10100000')
print(f"[데이터] {len(df)}봉 {df.index[0]} ~ {df.index[-1]}")

for label, kw in [("BASELINE", BASELINE_KW), ("FINAL", FINAL_KW)]:
    kw2 = dict(kw)
    kw2['return_trades'] = True
    res = run_chandelier_live_replica(df.copy(), **kw2)
    trades = res['trade_log']
    losers = sorted(trades, key=lambda t: t['pnl_pt'])[:5]
    print(f"\n=== {label}: 최악손실 top5 (raw pnl_pt, 계약수 나누기 전) ===")
    for t in losers:
        print(f"  {t['entry_time']} {t['direction']} 진입{t['entry_price']:.2f} -> {t['exit_time']} 청산{t['exit_price']:.2f} "
              f"pnl={t['pnl_pt']:+.2f}pt force={t['is_force']} 계약={t['contracts']}")
