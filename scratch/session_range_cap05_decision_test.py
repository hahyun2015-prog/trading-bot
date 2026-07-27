# -*- coding: utf-8 -*-
"""적용 여부 판단용 — 딱 검토 중인 후보 하나만 격리해서 확인.

baseline: chandelier_mult=0.30 (현재 라이브값, 변경 없음)
candidate: chandelier_mult=0.30 + session_range_cap_mult=0.5 (보수적 시작값, min_bars=6=30분)

앞서 돌린 스윕(0.8~0.3, mult 0.30/0.25 섞음)과 데이터·전체기간 정의는 동일하되, 이 스크립트는
"지금 라이브에 실제로 얹을 후보 단 하나"만 baseline과 나란히 비교해 판단을 돕는다.
[[feedback_backtest_regime_overfit]]에 따라 전체기간 + 최근 60일/30일 + 분기별로 교차검증하고,
마지막에 최근 1주일 거래 상세(A0568000, 미니선물 실승수)까지 포함한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

BASE_KW = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=250_000,
    enable_reentry_filter=True,
    chandelier_mult=0.30, chandelier_hard_cap=60.0,
    min_std_error_entry=0.9, trim_std_outliers=1,
    trend_bar_minutes=15, consecutive_loss_limit=5,
)
CANDIDATE_KW = dict(BASE_KW, session_range_cap_mult=0.5, session_range_cap_min_bars=6)

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
    base = run_chandelier_live_replica(df.copy(), **BASE_KW)
    cand = run_chandelier_live_replica(df.copy(), **CANDIDATE_KW)
    print(f"\n{'='*108}\n[{title}]  캔들수={len(df)}  기간={df.index.min()} ~ {df.index.max()}\n{'='*108}")
    print(f"  baseline (mult=0.30, 현행)          {fmt(base)}")
    print(f"  candidate(mult=0.30 + 캡x0.5)       {fmt(cand)}")
    if base and cand:
        print(f"  차이:  PF {cand['pf']-base['pf']:+.2f}  MDD {cand['mdd']-base['mdd']:+.2f}%p  "
              f"최악 {cand['worst_loss_pt']-base['worst_loss_pt']:+.2f}pt  거래수 {cand['trades']-base['trades']:+d}건")
    return base, cand


def main():
    df = load_futures_data('10100000')
    if df.empty:
        print("[-] 데이터 없음"); return

    regressions = []
    b, c = run_segment(df, "전체기간")
    if c and b and (c['pf'] < b['pf'] or c['mdd'] > b['mdd']):
        regressions.append("전체기간")

    last = df.index.max()
    for days in (60, 30):
        seg = df[df.index >= last - pd.Timedelta(days=days)]
        if len(seg) >= 500:
            b, c = run_segment(seg, f"최근 {days}일 (최근 레짐)")
            if c and b and (c['pf'] < b['pf'] or c['mdd'] > b['mdd']):
                regressions.append(f"최근{days}일")

    for lb, s, e in SEGMENTS:
        seg = df[(df.index >= s) & (df.index < e)]
        if len(seg) >= 500:
            b, c = run_segment(seg, lb)
            if c and b and (c['pf'] < b['pf'] or c['mdd'] > b['mdd']):
                regressions.append(lb)

    print(f"\n\n{'#'*108}")
    print(f"# 채택 기준 판정: PF 하락 또는 MDD 상승한 구간 = {regressions if regressions else '없음'}")
    print(f"{'#'*108}")

    # 최근 1주일 실거래 종목(A0568000, 미니선물 승수)으로 거래 상세까지
    print(f"\n\n{'='*108}\n[참고] 최근 1주일, 실제 라이브 종목 A0568000(미니선물, 승수 50,000원) 거래 상세\n{'='*108}")
    df2 = load_futures_data('A0568000')
    seg2 = df2[df2.index >= df2.index.max() - pd.Timedelta(days=7)]
    for name, kw in (("baseline", dict(BASE_KW, point_value=50_000, return_trades=True)),
                      ("candidate", dict(CANDIDATE_KW, point_value=50_000, return_trades=True))):
        res = run_chandelier_live_replica(seg2.copy(), **kw)
        print(f"\n-- {name} --  {fmt(res)}")
        if res:
            for t in res['trade_log']:
                force = " [강제청산]" if t['is_force'] else ""
                print(f"    {t['entry_time'].strftime('%m/%d %H:%M')} {t['direction']:<5} {t['entry_price']:8.2f} -> "
                      f"{t['exit_time'].strftime('%m/%d %H:%M')} {t['exit_price']:8.2f}  손익:{t['pnl_pt']:+7.2f}pt{force}")


if __name__ == '__main__':
    main()
