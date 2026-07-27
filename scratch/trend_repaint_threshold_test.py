# -*- coding: utf-8 -*-
"""추세필터 '전환 잦음' 대응안 2종 검증 — 샹들리에 엔진 기준.

2026-07-27 실거래에서 관측된 문제:
  - 하루 83회 갱신 중 76회(92%)가 DOWN, 장 후반 40분간 6회 전환.
  - 원인 A(리페인팅): 5분마다 재계산하는데 추세는 15분봉으로 판정하므로, 최신 15분 버킷이
    아직 안 찬 상태(83회 중 56회=67%)로 비교 대상 마지막 값이 계속 바뀐다.
  - 원인 B(임계값): 느린칼만 정상상태 게인 K=0.0311에서 slope=K*(z-x)이므로, 임계값
    0.01pt는 '현재 종가가 느린평균에서 0.32pt만 벗어나도 방향 확정'을 뜻해 사실상 sign(z-x).

대응안:
  1) trend_completed_bars_only=True — 완성된 15분봉만으로 판정(리페인팅 제거)
  2) trend_slope_threshold 상향 — 노이즈 구간을 NEUTRAL로 흡수

[[project_futures_entry_filter_diminishing_returns]]에 따라 검증 없이 실전 반영하지 않는다.
[[feedback_backtest_regime_overfit]]에 따라 전체기간 + 최근 레짐 + 분기별 교차검증한다.
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
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    min_std_error_entry=0.9,
    trim_std_outliers=1,
    trend_bar_minutes=15,
    consecutive_loss_limit=5,
)

VARIANTS = [
    ("baseline(현행)",              dict()),
    ("리페인팅제거(완성봉만)",       dict(trend_completed_bars_only=True)),
    ("임계값 0.10",                 dict(trend_slope_threshold=0.10)),
    ("임계값 0.30",                 dict(trend_slope_threshold=0.30)),
    ("임계값 0.50",                 dict(trend_slope_threshold=0.50)),
    ("리페인팅제거+임계값0.30",      dict(trend_completed_bars_only=True, trend_slope_threshold=0.30)),
]

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
    print(f"\n{'='*118}")
    print(f"[{title}]  캔들수={len(df)}  기간={df.index.min()} ~ {df.index.max()}")
    print('='*118)
    base = None
    for name, extra in VARIANTS:
        kw = dict(PROD_KW); kw.update(extra)
        res = run_chandelier_live_replica(df.copy(), **kw)
        tag = ""
        if base is None:
            base = res
        elif base and res:
            tag = (f"   (PF {res['pf']-base['pf']:+.2f}, MDD {res['mdd']-base['mdd']:+.2f}%p, "
                   f"최악 {res['worst_loss_pt']-base['worst_loss_pt']:+.2f}pt)")
        print(f"  {name:<24} {fmt(res)}{tag}")


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
