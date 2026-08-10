"""손실이 전략 탓인가 국면 탓인가 — 분기별 분해 (2026-08-11).

앞선 결과(전체기간, realistic_gap_fill=True, 10500000):
    샹들리에 배포본           PF 0.31  자본 -1.03억
    샹들리에 이익보전만 해제    PF 0.58  자본 -2,868만
    샹들리에 되돌린 조합        PF 0.70  자본 +1,659만
    SAR B2(돌파+게이트)      PF 0.84  자본 +3,691만
    → 전부 PF < 1.0

    최근 구간만 보면 PF가 1을 넘기도 한다. 그렇다면 손실이 특정 국면에 몰려 있고
    나머지 구간은 쓸 만하다는 뜻일 수 있다. 분기별로 갈라 확인한다.
    (변동성 자체가 구간마다 크게 다르므로 ATR 중앙값도 함께 본다)
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

COST = dict(commission_rate=0.00003,
            slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
            realistic_gap_fill=True)

CH_BASE = dict(
    Q=0.00005, R=1.0, mult=0.6, margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True, chandelier_hard_cap=60.0,
    session_range_cap_min_bars=6, kf_window=40, std_window=20,
    min_std_error_entry=1.5, trim_std_outliers=1, trend_bar_minutes=15,
    consecutive_loss_limit=5, dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True, profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0, profit_lock_be_move_trigger_pt=4.0,
    profit_lock_be_stage_buffer_pt=0.0, entry_end_hour=15, entry_end_minute=0,
    margin_rate=0.20,
)

CANDIDATES = [
    ("샹들리에 배포본", "ch", dict(atr_cutoff=0.5, chandelier_mult=0.3, session_range_cap_mult=0.5,
                              profit_lock_enabled=True, hard_stop_enabled=True, hard_stop_se_mult=1.5)),
    ("샹들리에 이익보전해제", "ch", dict(atr_cutoff=0.5, chandelier_mult=0.3, session_range_cap_mult=0.5,
                                profit_lock_enabled=False, hard_stop_enabled=False)),
    ("샹들리에 되돌림", "ch", dict(atr_cutoff=20.0, chandelier_mult=0.8, session_range_cap_mult=None,
                             profit_lock_enabled=False, hard_stop_enabled=False)),
    ("SAR B2", "sar", dict(atr_cutoff=15.0, min_std_error_entry=1.5,
                           entry_target_mode="breakout", breakout_k=0.2)),
]

SAR_BASE = dict(strategy="sar", Q=0.00005, R=1.0, mult=0.6, reentry_k=0.25,
                point_value=50_000, trim_std_outliers=1,
                entry_end_hour=15, entry_end_minute=0)


def run(kind, cfg, df):
    if kind == "ch":
        return run_chandelier_live_replica(df.copy(), **{**CH_BASE, **COST, **cfg})
    return run_sar_or_bb_replica(df.copy(), **{**SAR_BASE, **COST, **cfg})


def quarter(day):
    y, m = int(day[:4]), int(day[4:6])
    return f"{y}-Q{(m - 1) // 3 + 1}"


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    df = df.copy()
    df["q"] = df["date_day"].map(quarter)
    quarters = sorted(df["q"].unique())

    # 구간별 변동성 지표 — 일중 레인지 중앙값
    print("자료: 10500000 | realistic_gap_fill=True | 초기자본 5,000만\n")
    print(f"{'분기':10s} {'거래일':>5s} {'일중레인지중앙값':>14s}   " +
          "  ".join(f"{n[:14]:>16s}" for n, _, _ in CANDIDATES))
    print("-" * 128)

    totals = {n: [] for n, _, _ in CANDIDATES}
    for q in quarters:
        sub = df[df["q"] == q]
        ndays = sub["date_day"].nunique()
        if ndays < 10:
            continue
        rng = sub.groupby("date_day").apply(lambda g: g["high"].max() - g["low"].min())
        cells = []
        for name, kind, cfg in CANDIDATES:
            r = run(kind, cfg, sub)
            if r is None:
                cells.append(f"{'거래없음':>16s}")
                continue
            totals[name].append(r["pf"])
            cells.append(f"{r['pf']:>7.2f}({r['trades']:>4d})")
        print(f"{q:10s} {ndays:>5d} {np.median(rng):>13.1f}pt   " + "  ".join(f"{c:>16s}" for c in cells))

    print("-" * 128)
    print(f"{'PF>1 분기수':10s} {'':>5s} {'':>14s}   " +
          "  ".join(f"{sum(1 for p in totals[n] if p > 1.0):>10d}/{len(totals[n]):<5d}"
                    for n, _, _ in CANDIDATES))


if __name__ == "__main__":
    main()
