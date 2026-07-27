# -*- coding: utf-8 -*-
"""진입 종료시각(entry cutoff) 스윕 — 샹들리에 엔진 기준.

배경: 2026-07-24에 "장마감 전 무조건 청산"(15:35~15:45)이 도입된 뒤로, 장 후반 진입은
트레일링이 작동할 시간 자체가 없어 강제청산으로 끝날 확률이 높아진다. 실제로 2026-07-27
실거래에서 14:39 LONG 진입 -> 15:44 장마감 강제청산(-0.10pt)이 발생했다.
[[project_futures_entry_filter_diminishing_returns]] 메모가 "미시도 후보"로 남겨둔
'시간대 필터(장마감 근접 구간만)'에 해당하므로, 실전 반영 전 백테스트로 먼저 검증한다.

[[feedback_backtest_regime_overfit]]에 따라 전체기간 + 최근 레짐(60일/30일) 교차검증한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

# 실전 config.json futures_settings 기준 (chandelier_quarterly_test.py와 동일)
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

CUTOFFS = [None, (15, 0), (14, 30), (14, 0), (13, 30), (13, 0), (12, 0)]


def fmt(res):
    if res is None:
        return "거래 없음"
    return (f"거래{res['trades']:>4d}건 | 승률{res['win_rate']:6.2f}% | PF{res['pf']:7.2f} | "
            f"수익률{res['profit_pct']:+10.2f}% | MDD{res['mdd']:6.2f}% | 최악{res['worst_loss_pt']:+8.2f}pt")


def label_of(co):
    return "제한없음(현행)" if co is None else f"{co[0]:02d}:{co[1]:02d} 이후 진입금지"


def run_segment(df, title):
    print(f"\n{'='*112}")
    print(f"[{title}]  캔들수={len(df)}  기간={df.index.min()} ~ {df.index.max()}")
    print('='*112)
    base = None
    for co in CUTOFFS:
        kw = dict(PROD_KW)
        if co is not None:
            kw['entry_end_hour'], kw['entry_end_minute'] = co
        res = run_chandelier_live_replica(df.copy(), **kw)
        if co is None:
            base = res
        tag = ""
        if base and res and co is not None:
            d_pf = res['pf'] - base['pf']
            d_mdd = res['mdd'] - base['mdd']
            tag = f"   (PF {d_pf:+.2f}, MDD {d_mdd:+.2f}%p)"
        print(f"  {label_of(co):<22} {fmt(res)}{tag}")


def main():
    df = load_futures_data('10100000')
    if df.empty:
        print("[-] 데이터 없음")
        return

    run_segment(df, "전체기간")

    last = df.index.max()
    for days in (60, 30):
        seg = df[df.index >= last - pd.Timedelta(days=days)]
        if len(seg) < 500:
            print(f"\n[최근 {days}일] 데이터 부족({len(seg)}봉) — 건너뜀")
            continue
        run_segment(seg, f"최근 {days}일 (최근 레짐)")

    # 분기별 — 채택 기준(모든 구간에서 악화 없음) 확인용
    SEGMENTS = [
        ("2025-Q1", "2025-01-01", "2025-04-01"), ("2025-Q2", "2025-04-01", "2025-07-01"),
        ("2025-Q3", "2025-07-01", "2025-10-01"), ("2025-Q4", "2025-10-01", "2026-01-01"),
        ("2026-Q1", "2026-01-01", "2026-04-01"), ("2026-Q2", "2026-04-01", "2026-07-01"),
        ("2026-Q3(진행중)", "2026-07-01", "2026-10-01"),
    ]
    for lb, s, e in SEGMENTS:
        seg = df[(df.index >= s) & (df.index < e)]
        if len(seg) < 500:
            continue
        run_segment(seg, lb)


if __name__ == '__main__':
    main()
