# -*- coding: utf-8 -*-
"""chandelier_mult 재검증 — 전체기간 vs 최근 레짐 교차검증.

배경: 2026-07-27 실거래에서 SHORT(1057.28 진입)가 최저 1034.88까지 +22.40pt 평가익을
쌓았는데, 되돌림 25.40pt(=0.3 x ATR14 84.67pt) 규칙 때문에 -3.10pt 손실로 청산됐다.
당일 주간세션 변동폭이 40.64pt였으므로 트레일링 폭이 당일 레인지의 62%에 달했다 —
일중 청산이 강제되는 전략에서 사실상 유리하게 발동할 수 없는 폭이다.

ATR14는 (라이브/백테스트 모두) 5분봉이 아니라 일봉 환산으로 계산되므로, 최근처럼
일간 변동폭이 큰 레짐에서는 dist가 급격히 커진다. mult=0.3은 전체기간 794건으로
검증된 값이지만, [[feedback_backtest_regime_overfit]]에 따라 최근 레짐에서도 같은
방향인지 확인이 필요하다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

PROD_KW = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=250_000,
    enable_reentry_filter=True,
    chandelier_hard_cap=60.0,
    min_std_error_entry=0.9,
    trim_std_outliers=1,
    trend_bar_minutes=15,
    consecutive_loss_limit=5,
)

MULTS = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

SEGMENTS = [
    ("2025-Q1", "2025-01-01", "2025-04-01"), ("2025-Q2", "2025-04-01", "2025-07-01"),
    ("2025-Q3", "2025-07-01", "2025-10-01"), ("2025-Q4", "2025-10-01", "2026-01-01"),
    ("2026-Q1", "2026-01-01", "2026-04-01"), ("2026-Q2", "2026-04-01", "2026-07-01"),
    ("2026-Q3(진행중)", "2026-07-01", "2026-10-01"),
]


def fmt(res):
    if res is None:
        return "거래 없음"
    return (f"거래{res['trades']:>4d}건 | 승률{res['win_rate']:6.2f}% | PF{res['pf']:7.2f} | "
            f"수익률{res['profit_pct']:+10.2f}% | MDD{res['mdd']:6.2f}% | 최악{res['worst_loss_pt']:+8.2f}pt")


def run_segment(df, title):
    print(f"\n{'='*112}")
    print(f"[{title}]  캔들수={len(df)}  기간={df.index.min()} ~ {df.index.max()}")
    print('='*112)
    base = None
    for m in MULTS:
        kw = dict(PROD_KW); kw['chandelier_mult'] = m
        res = run_chandelier_live_replica(df.copy(), **kw)
        if m == 0.30:
            base = res
        print(f"  mult={m:<5} {fmt(res)}{'   <= 현행' if m == 0.30 else ''}")


def main():
    df = load_futures_data('10100000')
    if df.empty:
        print("[-] 데이터 없음"); return
    run_segment(df, "전체기간")
    last = df.index.max()
    for days in (60, 30):
        seg = df[df.index >= last - pd.Timedelta(days=days)]
        if len(seg) >= 500:
            run_segment(seg, f"최근 {days}일 (최근 레짐)")
    for lb, s, e in SEGMENTS:
        seg = df[(df.index >= s) & (df.index < e)]
        if len(seg) >= 500:
            run_segment(seg, lb)


if __name__ == '__main__':
    main()
