"""동일 조건 재비교 + 워크포워드 (2026-08-11).

배경:
    2026-08-11 슬리피지 실측(평균 −0.004pt, 6,767건)으로 비용 가정을 왕복 2.0pt에서
    편도 0.25pt로 내렸다. 그러자 샹들리에(이익보전 해제)가 전체 PF 1.17로 올라섰다.
    그런데 SAR·해외봇 수치는 옛 가정으로 잰 것이라 나란히 놓을 수 없었다.

    여기서 두 가지를 한다.
      (1) 후보 전부를 **같은 비용·같은 체결판정·같은 자료**로 다시 잰다
      (2) 앞선 후보들에 워크포워드를 걸어 후행편향을 제거한다

    (2)가 중요한 이유 — 2026-08-11 워크포워드에서 전체기간 PF 2.72짜리 조합이
    OOS로는 0.77이었다. 전체기간 성적만으로는 아무것도 결정할 수 없다.

공통 조건:
    자료      10500000 (미니 연속) 384거래일
    체결판정  realistic_gap_fill=True
    비용      슬리피지 편도 0.25pt 일괄 + 수수료 0.0030%
    자본      초기 5,000만, 동적 사이징(샹들리에/SAR) · 1계약 고정(해외봇)

    ※ 해외봇만 사이즈 규칙이 다르다. PF·승률은 사이즈에 무관하므로 그 지표로 비교하고,
      자본은 같은 축으로 읽지 않는다.
"""
import sys
import importlib.util

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.path.insert(0, r"c:\Antigravity\해외주식\src")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

_s = importlib.util.spec_from_file_location(
    "v", r"c:\Antigravity\AI_T_Agent\scratch\volatility_gate_20260811.py")
v = importlib.util.module_from_spec(_s)
_s.loader.exec_module(v)
v.SLIPPAGE_PT = 0.25            # 실측 반영분으로 통일

SLIP = dict(slip_entry_pt=0.25, slip_exit_sl_pt=0.25,
            slip_exit_normal_pt=0.25, slip_exit_force_pt=0.25)
COST = dict(commission_rate=0.00003, realistic_gap_fill=True, **SLIP)

CH = dict(Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5, margin_cap=0.30, reentry_k=0.25,
          point_value=50_000, enable_reentry_filter=True, chandelier_mult=0.3,
          chandelier_hard_cap=60.0, session_range_cap_mult=0.5, session_range_cap_min_bars=6,
          kf_window=40, std_window=20, min_std_error_entry=1.5, trim_std_outliers=1,
          trend_bar_minutes=15, consecutive_loss_limit=5, dynamic_sizing=True, max_contracts=15,
          regime_filter_enabled=True, profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
          profit_lock_be_buffer_pt=1.0, profit_lock_be_move_trigger_pt=4.0,
          profit_lock_be_stage_buffer_pt=0.0, entry_end_hour=15, entry_end_minute=0,
          hard_stop_se_mult=1.5, margin_rate=0.20)

SAR = dict(strategy="sar", Q=0.00005, R=1.0, mult=0.6, reentry_k=0.25, point_value=50_000,
           trim_std_outliers=1, entry_end_hour=15, entry_end_minute=0, atr_cutoff=15.0,
           min_std_error_entry=1.5)


def ch_trades(df, **over):
    r = run_chandelier_live_replica(df.copy(), **{**CH, **COST, **over}, return_trades=True)
    return r.get("trade_log", []) if r else []


def sar_trades(df, **over):
    r = run_sar_or_bb_replica(df.copy(), **{**SAR, **COST, **over}, return_trades=True)
    return (r or {}).get("trade_log", [])


def st_from(trades, key="pnl_pt"):
    if not trades:
        return None
    p = np.array([t[key] for t in trades], dtype=float)
    w, l = p[p > 0], p[p <= 0]
    gl = -l.sum()
    return dict(n=len(p), wr=len(w) / len(p) * 100,
                pf=(w.sum() / gl) if gl > 0 else float("inf"), pts=p.sum())


def q_of(dt):
    return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"


CANDIDATES = [
    ("샹들리에 배포본(종전)", lambda d: ch_trades(d, profit_lock_enabled=True, hard_stop_enabled=True)),
    ("샹들리에 이익보전해제", lambda d: ch_trades(d, profit_lock_enabled=False, hard_stop_enabled=False)),
    ("SAR B2 (돌파+게이트)", lambda d: sar_trades(d, entry_target_mode="breakout", breakout_k=0.2)),
    ("SAR A (칼만밴드)", lambda d: sar_trades(d, entry_target_mode="kalman_band")),
]


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    days = sorted(df["date_day"].unique())
    print(f"자료 10500000 {len(df):,}봉 / {len(days)}거래일 | 슬리피지 편도 0.25pt | 정직한 갭체결\n")

    print("=" * 116)
    print("[동일 조건 재비교] 전체기간 / 분기별 PF>1 개수")
    print("=" * 116)
    print(f"  {'후보':24s}{'거래':>7s}{'승률':>9s}{'PF':>8s}{'손익(pt)':>12s}{'분기 PF>1':>12s}   분기별")
    print("  " + "-" * 112)

    store = {}
    for name, fn in CANDIDATES:
        tr = fn(df)
        store[name] = tr
        s = st_from(tr)
        if not s:
            print(f"  {name:24s} 거래 없음")
            continue
        qs = {}
        for t in tr:
            qs.setdefault(q_of(t["entry_time"]), []).append(t)
        good = sum(1 for q in qs if (st_from(qs[q]) or {}).get("pf", 0) > 1.0)
        detail = " ".join(f"{q[2:]}:{st_from(qs[q])['pf']:.2f}" for q in sorted(qs))
        print(f"  {name:24s}{s['n']:>7d}{s['wr']:>8.2f}%{s['pf']:>8.2f}{s['pts']:>+12.1f}"
              f"{f'{good}/{len(qs)}':>12s}   {detail}")

    # ── 워크포워드 ────────────────────────────────────────────────
    print(f"\n{'=' * 116}")
    print("[워크포워드] 각 분기를 그 이전 데이터만으로 고른 설정으로 매매")
    print("=" * 116)

    # 후보군 = 샹들리에 이익보전 on/off × chandelier_mult × atr_cutoff
    grid = {}
    for pl in (True, False):
        for cm in (0.3, 0.8, 1.2):
            for ac in (0.5, 10.0, 20.0):
                key = (pl, cm, ac)
                grid[key] = ch_trades(df, profit_lock_enabled=pl, hard_stop_enabled=pl,
                                      chandelier_mult=cm, atr_cutoff=ac)
    print(f"  격자 {len(grid)}조합 (이익보전 on/off × 트레일 3 × atr컷 3)")

    quarters = sorted({q_of(t["entry_time"]) for ts in grid.values() for t in ts})
    folds = [q for q in quarters if q >= "2025-Q4"]
    MIN_IS = 20

    print(f"\n  {'분기':10s}{'IS 선택':>28s}{'IS거래':>7s}{'IS PF':>8s}{'OOS거래':>8s}{'OOS PF':>9s}{'OOS손익':>11s}")
    print("  " + "-" * 112)
    oos = []
    for q in folds:
        best, bpf = None, -1.0
        for key, trades in grid.items():
            is_tr = [t for t in trades if q_of(t["entry_time"]) < q]
            if len(is_tr) < MIN_IS:
                continue
            s = st_from(is_tr)
            if s and s["pf"] > bpf:
                bpf, best = s["pf"], key
        if best is None:
            print(f"  {q:10s}{'선택 불가':>28s}")
            continue
        pl, cm, ac = best
        sel = [t for t in grid[best] if q_of(t["entry_time"]) == q]
        oos.extend(sel)
        iss, oss = st_from([t for t in grid[best] if q_of(t["entry_time"]) < q]), st_from(sel)
        lab = f"보전{'ON' if pl else 'OFF'} 트레일{cm} atr{ac:.0f}"
        if oss:
            print(f"  {q:10s}{lab:>28s}{iss['n']:>7d}{iss['pf']:>8.2f}"
                  f"{oss['n']:>8d}{oss['pf']:>9.2f}{oss['pts']:>+11.1f}")
        else:
            print(f"  {q:10s}{lab:>28s}{iss['n']:>7d}{iss['pf']:>8.2f}{0:>8d}{'-':>9s}{0.0:>+11.1f}")

    print("  " + "-" * 112)
    s = st_from(oos)
    if s:
        print(f"  {'OOS 합계':10s}{'':>28s}{'':>7s}{'':>8s}{s['n']:>8d}{s['pf']:>9.2f}{s['pts']:>+11.1f}")
        print(f"  → 승률 {s['wr']:.2f}%")

    # 비교: 고정 설정(현재 config)을 같은 구간에 그대로 적용
    print(f"\n  [참고] 고정 설정을 같은 OOS 구간에 적용했다면")
    for name in ("샹들리에 배포본(종전)", "샹들리에 이익보전해제"):
        sel = [t for t in store[name] if q_of(t["entry_time"]) in folds]
        s2 = st_from(sel)
        if s2:
            print(f"    {name:24s} 거래{s2['n']:>5d} 승률{s2['wr']:6.2f}% PF{s2['pf']:6.2f} 손익{s2['pts']:+9.1f}pt")


if __name__ == "__main__":
    main()
