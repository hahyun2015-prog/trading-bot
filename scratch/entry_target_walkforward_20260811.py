"""진입 타점 설계 워크포워드 (2026-08-11).

entry_target_design 결과가 남긴 질문:
  · K 스윕이 비단조다 — 0.15(PF 0.98) / 0.20(1.19) / 0.30(1.04) / 0.45(0.91) / 0.60(1.64).
    현행 K=0.20이 안정된 평지가 아니라 뾰족한 봉우리 위에 있다.
  · 시초가 앵커는 전체 PF가 높지만(1.19 vs 1.11) 그 우위가 2026-Q3 한 분기에서 나온다.
    전일종가 앵커는 분기 범위가 절반이다(1.21 → 0.65).

전체 PF로는 판정할 수 없는 질문이므로, 분기마다 직전 데이터만으로 (앵커, K)를 고르고
다음 분기에 적용한다. 그리고 각 고정 설정을 같은 OOS 구간에 대 봐서, 사전에 고를 수
있었는지와 결과적으로 좋았는지를 나눈다.
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import importlib.util

_s = importlib.util.spec_from_file_location(
    "L", r"c:\Antigravity\AI_T_Agent\scratch\live_config_backtest_20260811.py")
L = importlib.util.module_from_spec(_s)
_s.loader.exec_module(L)

from bqa.kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

CUR = dict(ma_filter_period=200, sar_af_max=0.10, daily_loss_limit_pt=L.LIMIT_PT)
MIN_IS = 25


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def run(df, **over):
    kw = {**L.BASE, **CUR}
    kw.update(over)
    return run_sar_or_bb_replica(df.copy(), **kw, return_trades=True).get("trade_log", [])


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")

    grid = {}
    for anchor in ("open", "prev_close"):
        for k in (0.05, 0.10, 0.20, 0.30, 0.45, 0.60, 0.80):
            grid[(anchor, k)] = run(df, entry_anchor=anchor, breakout_k=k)
    print(f"격자 {len(grid)}조합 (앵커 2 × K 7) | IS 최소 {MIN_IS}거래")

    qs_all = sorted({q_of(t["entry_time"]) for ts in grid.values() for t in ts})
    folds = [q for q in qs_all if q >= "2026-Q1"]

    print(f"\n{'=' * 112}")
    print("[워크포워드] 분기마다 직전 데이터만으로 (앵커, K) 선택")
    print("=" * 112)
    print(f"  {'분기':10s}{'IS 선택':>22s}{'IS거래':>7s}{'IS PF':>8s}"
          f"{'OOS거래':>8s}{'OOS PF':>9s}{'OOS손익':>11s}")
    print("  " + "-" * 108)

    oos, picks = [], []
    for q in folds:
        best, bpf = None, -1.0
        for key, tr in grid.items():
            istr = [t for t in tr if q_of(t["entry_time"]) < q]
            if len(istr) < MIN_IS:
                continue
            s = L.stat(istr)
            if s and s["pf"] > bpf:
                bpf, best = s["pf"], key
        if best is None:
            print(f"  {q:10s}{'선택 불가':>22s}")
            continue
        sel = [t for t in grid[best] if q_of(t["entry_time"]) == q]
        oos.extend(sel)
        picks.append(best)
        iss, oss = L.stat([t for t in grid[best] if q_of(t["entry_time"]) < q]), L.stat(sel)
        lab = f"{best[0]} K={best[1]:.2f}"
        if oss:
            print(f"  {q:10s}{lab:>22s}{iss['n']:>7d}{iss['pf']:>8.2f}"
                  f"{oss['n']:>8d}{oss['pf']:>9.2f}{oss['pts']:>+11.1f}")
    print("  " + "-" * 108)
    s = L.stat(oos)
    if s:
        print(f"  {'OOS 합계':10s}{'':>22s}{'':>7s}{'':>8s}{s['n']:>8d}{s['pf']:>9.2f}{s['pts']:>+11.1f}")
        print(f"  선택 안정성: {picks}")

    print(f"\n{'=' * 112}")
    print("[비교] 고정 설정을 같은 OOS 구간에 — 사전에 고를 수 있었는가와 별개로")
    print("=" * 112)
    print(f"  {'설정':28s}{'거래':>7s}{'PF':>8s}{'손익':>11s}{'MDD':>10s}")
    print("  " + "-" * 108)
    for key in sorted(grid, key=lambda x: (x[0], x[1])):
        sel = [t for t in grid[key] if q_of(t["entry_time"]) in folds]
        st = L.stat(sel)
        if st:
            mark = "  ← 현행" if key == ("open", 0.20) else ""
            print(f"  {f'{key[0]} K={key[1]:.2f}':28s}{st['n']:>7d}{st['pf']:>8.2f}"
                  f"{st['pts']:>+11.1f}{st['mdd']:>10.1f}{mark}")


if __name__ == "__main__":
    main()
