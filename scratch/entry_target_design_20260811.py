"""진입 타점 설계 재검토 (2026-08-11).

현재 타점:
    target_long  = 시초가 + 전일Range × 0.2
    target_short = 시초가 - 전일Range × 0.2
    하루 종일 고정.

이 공식은 구버전 변동성돌파 전략에서 그대로 물려받았고, K 말고는 한 번도
따져 본 적이 없다. 설계 요소는 셋이다.

  ① 계수 K        0.2      — 스윕했으나 워크포워드 실패 (여기서 재확인만)
  ② 폭의 기준     전일Range — 미검증. 하루치 노이즈를 통째로 받는다.
                             칼만 ATR·std_error는 평활돼 있어 더 안정적일 수 있다.
  ③ 앵커          시초가    — 미검증. 갭이 타점을 통째로 밀어 올린다.
                             평균 |갭| 10.81pt(1.31%)로 작지 않다.

②③은 비교가 공정하려면 '타점까지의 거리'를 맞춰야 한다. 기준을 바꾸면 스케일이
달라지므로, 각 기준의 중앙값으로 K를 환산해 현행과 같은 거리에서 비교한다.

기각 기준은 오늘 내내 쓴 것과 같다 — 전체 PF가 아니라 분기 일관성, 그리고
유망하면 워크포워드.
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import importlib.util

import numpy as np
import pandas as pd

_s = importlib.util.spec_from_file_location(
    "L", r"c:\Antigravity\AI_T_Agent\scratch\live_config_backtest_20260811.py")
L = importlib.util.module_from_spec(_s)
_s.loader.exec_module(L)

from bqa.kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

CUR = dict(ma_filter_period=200, sar_af_max=0.10, daily_loss_limit_pt=L.LIMIT_PT)
BASE_K = 0.2


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def run(df, **over):
    kw = {**L.BASE, **CUR}
    kw.update(over)
    return run_sar_or_bb_replica(df.copy(), **kw, return_trades=True).get("trade_log", [])


def show(label, tr, mark=""):
    s = L.stat(tr)
    if s is None:
        print(f"  {label:32s} 거래 없음")
        return None
    qs = {}
    for t in tr:
        qs.setdefault(q_of(t["entry_time"]), []).append(t)
    good = sum(1 for q in qs if L.stat(qs[q])["pf"] > 1.0)
    print(f"  {label:32s} 거래{s['n']:>4d} 승률{s['wr']:>6.2f}% PF{s['pf']:>6.2f} "
          f"손익{s['pts']:>+8.1f}pt MDD{s['mdd']:>7.1f}pt 분기{good}/{len(qs)}{mark}")
    return s


def magnitudes(df):
    """전일Range / 칼만ATR / std_error 의 전형적 크기 — K 환산용."""
    d = df.groupby("date_day").agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    rng = (d["h"] - d["l"]).shift().dropna()
    pc = d["c"].shift()
    tr = pd.concat([d["h"] - d["l"], (d["h"] - pc).abs(), (d["l"] - pc).abs()], axis=1).max(axis=1)
    kf, P, Q, R, path = None, 1.0, 0.002, 0.2, []
    for v in tr.values:
        if kf is None:
            kf = v
        else:
            P += Q
            K = P / (P + R)
            kf = kf + K * (v - kf)
            P = (1 - K) * P
        path.append(kf)
    atr = pd.Series(path[:-1])
    gap = (d["c"].shift() - d["h"].shift() * 0).dropna()  # placeholder, 갭은 별도 계산
    return float(rng.median()), float(atr.median())


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    med_rng, med_atr = magnitudes(df)
    base = run(df)

    print(f"전일Range 중앙값 {med_rng:.2f}pt | 칼만ATR 중앙값 {med_atr:.2f}pt")
    print(f"현행 타점 거리 = 전일Range × {BASE_K} = 약 {med_rng * BASE_K:.2f}pt (편도)")
    print()

    print("=" * 118)
    print("[①] 계수 K — 재확인")
    print("=" * 118)
    for k in (0.05, 0.10, 0.15, 0.20, 0.30, 0.45, 0.60, 0.80):
        show(f"K={k:.2f}  (거리 약 {med_rng * k:5.1f}pt)", run(df, breakout_k=k),
             "  ← 현행" if abs(k - BASE_K) < 1e-9 else "")

    print(f"\n{'=' * 118}")
    print("[②] 폭의 기준 — 같은 거리로 환산해 비교")
    print("=" * 118)
    k_atr = BASE_K * med_rng / med_atr
    show("전일Range × 0.20 (현행)", base, "  ← 현행")
    show(f"칼만ATR × {k_atr:.3f} (같은 거리)", run(df, entry_width_basis="atr", breakout_k=k_atr))
    print("  칼만ATR 기준 K 스윕:")
    for m in (0.5, 0.75, 1.0, 1.5, 2.0):
        show(f"  칼만ATR × {k_atr * m:.3f}", run(df, entry_width_basis="atr", breakout_k=k_atr * m))
    print("  std_error 기준 K 스윕 (std_error는 진입필터에도 쓰는 값):")
    for k in (1.0, 2.0, 3.0, 4.0, 6.0):
        show(f"  std_error × {k:.1f}", run(df, entry_width_basis="std_error", breakout_k=k))

    print(f"\n{'=' * 118}")
    print("[③] 앵커 — 갭을 쫓아갈 것인가, 갭을 신호로 볼 것인가")
    print("=" * 118)
    show("시초가 기준 (현행)", base, "  ← 현행")
    show("전일종가 기준", run(df, entry_anchor="prev_close"))
    show("중간 기준 (갭 절반 반영)", run(df, entry_anchor="mid"))
    print("  전일종가 앵커에서 K 스윕 (갭이 이미 타점을 넘는 날이 늘어난다):")
    for k in (0.20, 0.30, 0.45, 0.60, 0.80):
        show(f"  전일종가 K={k:.2f}", run(df, entry_anchor="prev_close", breakout_k=k))

    print(f"\n{'=' * 118}")
    print("[④] 분기별 — 유망 후보만")
    print("=" * 118)
    cands = {"현행": base,
             "전일종가 앵커": run(df, entry_anchor="prev_close"),
             "중간 앵커": run(df, entry_anchor="mid"),
             f"칼만ATR 폭": run(df, entry_width_basis="atr", breakout_k=k_atr)}
    qs_all = sorted({q_of(t["entry_time"]) for ts in cands.values() for t in ts})
    print(f"  {'구성':18s}" + "".join(f"{q:>13s}" for q in qs_all))
    print("  " + "-" * (18 + 13 * len(qs_all)))
    for name, tr in cands.items():
        row = f"  {name:18s}"
        for q in qs_all:
            s = L.stat([t for t in tr if q_of(t["entry_time"]) == q])
            row += "{:>13s}".format("-" if s is None else "{:.2f}(n{})".format(s["pf"], s["n"]))
        print(row)


if __name__ == "__main__":
    main()
