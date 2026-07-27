# -*- coding: utf-8 -*-
"""트레일링 폭(dist)에 '오늘 세션 레인지' 상한을 추가하는 안 검증 (2026-07-27).

배경: chandelier의 dist = mult*ATR14는 ATR14가 "전일까지의 일봉"이라 지연 지표다.
어제 이전 변동성이 컸으면 오늘 실제 흐름과 무관하게 dist가 크게 유지되어(2026-07-27
실거래: dist 25.40pt가 당일 레인지 40.64pt의 62%), 큰 평가익을 반납하고 청산되는
사례가 나온다.

이 스크립트는 dist를 "오늘 지금까지의 세션 레인지 x session_range_cap_mult"로 추가
상한하는 안을, chandelier_mult=0.30(현행)과 0.25(검토중) 각각에 대해 전체기간+최근
레짐+분기별로 검증한다. [[project_futures_entry_filter_diminishing_returns]] 원칙에
따라 검증 없이 실전 반영하지 않는다.
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

CAP_MULTS = [None, 0.8, 0.6, 0.5, 0.4, 0.3]

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


def run_segment(df, title, base_mult):
    print(f"\n{'='*112}")
    print(f"[{title}]  chandelier_mult={base_mult}  캔들수={len(df)}  기간={df.index.min()} ~ {df.index.max()}")
    print('='*112)
    for cap in CAP_MULTS:
        kw = dict(PROD_KW); kw['chandelier_mult'] = base_mult
        if cap is not None:
            kw['session_range_cap_mult'] = cap
        res = run_chandelier_live_replica(df.copy(), **kw)
        label = "제한없음(현행)" if cap is None else f"세션레인지x{cap}"
        print(f"  {label:<20} {fmt(res)}")


def main():
    df = load_futures_data('10100000')
    if df.empty:
        print("[-] 데이터 없음"); return

    last = df.index.max()
    for base_mult in (0.30, 0.25):
        run_segment(df, "전체기간", base_mult)
        for days in (60, 30):
            seg = df[df.index >= last - pd.Timedelta(days=days)]
            if len(seg) >= 500:
                run_segment(seg, f"최근 {days}일 (최근 레짐)", base_mult)
        for lb, s, e in SEGMENTS:
            seg = df[(df.index >= s) & (df.index < e)]
            if len(seg) >= 500:
                run_segment(seg, lb, base_mult)


if __name__ == '__main__':
    main()
