# -*- coding: utf-8 -*-
"""session_range_cap 결과가 너무 일관돼서(전 구간 무예외 개선) 실제 거래 단위로 재검증.

의심 지점: session_range_cap_min_bars(기본 6=30분) 직후, 그날 레인지가 아직 작을 때
dist가 거의 0에 가까워져 사실상 스캘핑(수익 몇 틱만 잡고 즉시 청산)이 되어 거래횟수만
부풀리는 착시가 아닌지 실제 거래 로그로 확인한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

PROD_KW = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.30, chandelier_hard_cap=60.0,
    min_std_error_entry=0.9, trim_std_outliers=1,
    trend_bar_minutes=15, consecutive_loss_limit=5,
    return_trades=True,
)


def show(res, label):
    print(f"\n{'='*100}\n[{label}]")
    if res is None:
        print("  거래 없음"); return
    print(f"  거래{res['trades']}건 | 승률{res['win_rate']:.2f}% | PF{res['pf']:.2f} | "
          f"평균익{res['avg_win_pt']:+.2f}pt | 평균손{res['avg_loss_pt']:+.2f}pt | 최악{res['worst_loss_pt']:+.2f}pt")
    holds = [(t['exit_time'] - t['entry_time']).total_seconds() / 60 for t in res['trade_log']]
    print(f"  보유시간(분) 최소/중앙값/최대: {min(holds):.0f} / {sorted(holds)[len(holds)//2]:.0f} / {max(holds):.0f}")
    n_tiny = sum(1 for h in holds if h <= 5)
    print(f"  5분 이하로 끝난 거래: {n_tiny}건 ({n_tiny/len(holds)*100:.1f}%)")
    for t in res['trade_log']:
        print(f"    {t['entry_time'].strftime('%m/%d %H:%M')} {t['direction']:<5} {t['entry_price']:8.2f} -> "
              f"{t['exit_time'].strftime('%m/%d %H:%M')} {t['exit_price']:8.2f}  "
              f"({(t['exit_time']-t['entry_time']).total_seconds()/60:.0f}분)  손익:{t['pnl_pt']:+7.2f}pt")


def main():
    df = load_futures_data('A0568000')
    last = df.index.max()
    seg = df[df.index >= last - pd.Timedelta(days=7)]

    for cap in (None, 0.5, 0.3):
        kw = dict(PROD_KW)
        if cap is not None:
            kw['session_range_cap_mult'] = cap
        res = run_chandelier_live_replica(seg.copy(), **kw)
        show(res, f"최근1주일 A0568000, session_range_cap={cap}")


if __name__ == '__main__':
    main()
