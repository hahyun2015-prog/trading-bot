"""새 메커니즘 탐색 — 진입 문턱·시간대·추세 강도 (2026-08-11).

이미 기각된 것은 다시 보지 않는다:
  08-09: 로렌치안KNN, IB+VWAP, SMC FVG, 볼린저 6종, KAMA 5종, 하이킨아시,
         UT Bot, Donchian, ADX필터, 개장범위 브레이크아웃
  08-11: BB 평균회귀 재설계(48변형), 다일보유, 시간축 확대(15/30/60분),
         손절 조이기·캡, 방향 틸트

여기서 보는 것은 세 가지 미탐색 축이다.

  A. 진입 문턱 K — target = 시초가 ± 전일Range × K. K=0.2는 변동성돌파 시절
     값을 그대로 물려받았고, 정직한 비용·현 구성에서 재검한 적이 없다.
     오늘 반복 확인된 "느슨할수록 낫다"가 여기에도 적용되는지.

  B. 시간대 — 진입 가능 구간(09:10~15:00)을 한 번도 쪼개 본 적이 없다.
     개장 직후 노이즈와 오후 추세가 다른 성격이라면 갈라야 한다.

  C. 추세 강도 — MA200 방향필터는 오늘 유일하게 작동한 개선인데 판정이 이진이다.
     이평선에 붙어 횡보하는 국면과 확실한 추세를 기울기로 구분한다.

세 축 모두 현 배포본 위에 하나씩만 얹어 순효과를 본다. 조합 최적화는 하지 않는다 —
오늘 격자 최댓값이 워크포워드에서 뒤집히는 것을 이미 두 번 봤다.
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import importlib.util

import numpy as np

_s = importlib.util.spec_from_file_location(
    "L", r"c:\Antigravity\AI_T_Agent\scratch\live_config_backtest_20260811.py")
L = importlib.util.module_from_spec(_s)
_s.loader.exec_module(L)

from bqa.kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

CUR = dict(ma_filter_period=200, sar_af_max=0.10, daily_loss_limit_pt=L.LIMIT_PT)
BASE_K = float(L.CFG.get("best_k", 0.2))


def run(df, **over):
    kw = {**L.BASE, **CUR}
    kw.update(over)
    return run_sar_or_bb_replica(df.copy(), **kw, return_trades=True).get("trade_log", [])


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def show(label, tr, mark=""):
    s = L.stat(tr)
    if s is None:
        print(f"  {label:28s} 거래 없음")
        return None
    qs = {}
    for t in tr:
        qs.setdefault(q_of(t["entry_time"]), []).append(t)
    good = sum(1 for q in qs if L.stat(qs[q])["pf"] > 1.0)
    print(f"  {label:28s} 거래{s['n']:>4d} 승률{s['wr']:>6.2f}% PF{s['pf']:>6.2f} "
          f"손익{s['pts']:>+8.1f}pt MDD{s['mdd']:>7.1f}pt 분기{good}/{len(qs)}{mark}")
    return s


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    base = run(df)
    b = L.stat(base)
    print(f"기준선(현 배포본): 거래 {b['n']} PF {b['pf']:.2f} 손익 {b['pts']:+.1f}pt MDD {b['mdd']:.1f}pt")

    print(f"\n{'=' * 112}")
    print(f"[A] 진입 문턱 K — 현재 {BASE_K} (전일Range 대비)")
    print("=" * 112)
    for k in (0.10, 0.15, 0.20, 0.30, 0.40, 0.60):
        show(f"K={k:.2f}", run(df, breakout_k=k), "  ← 현재" if abs(k - BASE_K) < 1e-9 else "")

    print(f"\n{'=' * 112}")
    print("[B] 진입 시간대 — 현재 09:10~15:00")
    print("=" * 112)
    windows = [("09:10~15:00 (현행)", 9, 10, 15, 0),
               ("10:00~15:00", 10, 0, 15, 0),
               ("11:00~15:00", 11, 0, 15, 0),
               ("09:10~12:00 (오전만)", 9, 10, 12, 0),
               ("12:00~15:00 (오후만)", 12, 0, 15, 0),
               ("09:10~14:00", 9, 10, 14, 0)]
    for name, sh, sm, eh, em in windows:
        show(name, run(df, entry_start_hour=sh, entry_start_minute=sm,
                       entry_end_hour=eh, entry_end_minute=em))

    print(f"\n{'=' * 112}")
    print("[C] 추세 강도 — MA200 기울기 문턱 (pt/봉, 40봉 기준)")
    print("=" * 112)
    show("기울기 무시 (현행)", base, "  ← 현재")
    for sl in (0.005, 0.01, 0.02, 0.04, 0.08):
        show(f"기울기 >= {sl:.3f}", run(df, ma_slope_min=sl))

    # 유망한 축만 워크포워드로 검증
    print(f"\n{'=' * 112}")
    print("[검증] 유망 후보 워크포워드 — 분기별로 직전 데이터만 보고 고른다")
    print("=" * 112)
    grid = {}
    for k in (0.15, 0.20, 0.30, 0.40):
        for sl in (None, 0.01, 0.02, 0.04):
            grid[(k, sl)] = run(df, breakout_k=k, ma_slope_min=sl)
    print(f"  격자 {len(grid)}조합 (K 4 × 기울기 4)")

    qs_all = sorted({q_of(t["entry_time"]) for ts in grid.values() for t in ts})
    folds = [q for q in qs_all if q >= "2026-Q1"]
    MIN_IS = 20
    oos, picks = [], []
    print(f"\n  {'분기':10s}{'IS 선택':>22s}{'IS거래':>7s}{'IS PF':>8s}{'OOS거래':>8s}{'OOS PF':>9s}{'OOS손익':>11s}")
    print("  " + "-" * 108)
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
        k, sl = best
        sel = [t for t in grid[best] if q_of(t["entry_time"]) == q]
        oos.extend(sel)
        picks.append(best)
        iss, oss = L.stat([t for t in grid[best] if q_of(t["entry_time"]) < q]), L.stat(sel)
        lab = f"K{k:.2f} 기울기{sl if sl else '무시'}"
        print(f"  {q:10s}{lab:>22s}{iss['n']:>7d}{iss['pf']:>8.2f}"
              f"{oss['n']:>8d}{oss['pf']:>9.2f}{oss['pts']:>+11.1f}")
    s = L.stat(oos)
    print("  " + "-" * 108)
    if s:
        print(f"  {'OOS 합계':10s}{'':>22s}{'':>7s}{'':>8s}{s['n']:>8d}{s['pf']:>9.2f}{s['pts']:>+11.1f}")
        print(f"  선택 안정성: K={[p[0] for p in picks]} 기울기={[p[1] for p in picks]}")
    base_oos = [t for t in base if q_of(t["entry_time"]) in folds]
    bs = L.stat(base_oos)
    print(f"\n  [비교] 현 배포본 고정을 같은 구간에: 거래{bs['n']} PF{bs['pf']:.2f} 손익{bs['pts']:+.1f}pt")


if __name__ == "__main__":
    main()
