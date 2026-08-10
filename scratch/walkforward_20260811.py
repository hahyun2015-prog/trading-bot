"""워크포워드 검증 — 변동성 게이트 결과가 후행편향인가 (2026-08-11).

문제:
    2026-08-11 검증에서 5분봉 SMA 50/150 · ATR×3.0 · 변동성게이트 6pt가
    PF 2.72 / MDD 2.97%를 냈다. 그런데 게이트를 3pt 이상 올리면 **거래가 전부
    2026-Q1 이후**로 몰린다. 즉 "2025년을 잘라낸 것"과 결과가 구별되지 않는다.

    파라미터(SMA쌍·ATR배수·게이트)를 전체기간을 보고 골랐으므로, 그 성적은
    "미래를 알고 고른" 값일 수 있다. 실제로 쓸 수 있는 값이었는지 확인한다.

방법:
    분기 단위 앵커드 워크포워드.
      각 분기 Q에 대해
        IS  = Q 시작 이전의 모든 거래   → 여기서만 파라미터를 고른다
        OOS = Q 구간의 거래             → 고른 파라미터를 그대로 적용해 기록
      모든 분기의 OOS를 이어붙인 것이 **실제로 얻었을 성적**이다.

    조합마다 전 구간을 한 번씩만 돌리고 결과를 시점으로 자른다. 전략이 인과적
    (미래참조 없음)이므로, 연속 실행 후 구간을 자르는 것과 구간마다 다시 도는 것이
    같은 결과를 준다. 진입 시각이 속한 구간에 그 거래를 귀속시킨다.

    IS 표본이 너무 적으면 고를 근거가 없으므로 최소 거래수를 요구하고,
    미달이면 그 분기는 '선택 불가'로 남긴다 — 이것도 결과의 일부다.
"""
import sys
import importlib.util

sys.path.insert(0, r"c:\Antigravity\해외주식\src")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

_s = importlib.util.spec_from_file_location(
    "v", r"c:\Antigravity\AI_T_Agent\scratch\volatility_gate_20260811.py")
v = importlib.util.module_from_spec(_s)
_s.loader.exec_module(v)

SMA_PAIRS = [(20, 50), (20, 100), (50, 150), (50, 200)]
ATR_MULTS = [2.0, 3.0, 4.0]
GATES = [0.0, 2.0, 3.0, 4.0, 6.0, 8.0]
MIN_IS_TRADES = 10          # 이보다 적으면 파라미터를 고를 근거가 없다고 본다


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def q_start(q):
    y, qq = q.split("-Q")
    return (int(y), (int(qq) - 1) * 3 + 1)


def before(q, ts):
    """ts가 분기 q 시작 이전인가."""
    y, m = q_start(q)
    return (ts.year, ts.month) < (y, m)


def within(q, ts):
    y, m = q_start(q)
    return (ts.year, ts.month) in ((y, m), (y, m + 1), (y, m + 2))


def main(tf=5):
    df = v.load_frame()
    out = v.resample(df, tf)
    bars = v.to_bars(out)
    close_t = v.last_bar_time(out)

    print(f"자료: 10500000 {tf}분봉 {len(bars):,}봉 | 강제청산 {close_t}")
    print(f"격자: SMA {len(SMA_PAIRS)}쌍 × ATR배수 {len(ATR_MULTS)} × 게이트 {len(GATES)} "
          f"= {len(SMA_PAIRS)*len(ATR_MULTS)*len(GATES)}조합")
    print(f"IS 최소 거래수 {MIN_IS_TRADES}건\n")

    # 조합별로 전 구간 한 번씩만 실행
    runs = {}
    for f, sl in SMA_PAIRS:
        for am in ATR_MULTS:
            for g in GATES:
                runs[(f, sl, am, g)] = v.run(bars, close_t, f, sl, am, g)

    quarters = sorted({q_of(t.entry_time) for ts in runs.values() for t in ts})
    folds = [q for q in quarters if q >= "2025-Q4"]

    print("=" * 122)
    print("[워크포워드] 각 분기는 그 이전 데이터만으로 고른 파라미터로 매매한다")
    print("=" * 122)
    print(f"  {'분기':10s}{'IS선택 파라미터':>26s}{'IS거래':>7s}{'IS PF':>8s}"
          f"{'OOS거래':>8s}{'OOS승률':>9s}{'OOS PF':>9s}{'OOS손익(pt)':>13s}")
    print("  " + "-" * 118)

    oos_all = []
    picks = []
    for q in folds:
        best, best_pf = None, -1.0
        for key, trades in runs.items():
            is_tr = [t for t in trades if before(q, t.entry_time)]
            if len(is_tr) < MIN_IS_TRADES:
                continue
            st = v.stats(is_tr)
            if st and st["pf"] > best_pf:
                best_pf, best = st["pf"], (key, st)
        if best is None:
            print(f"  {q:10s}{'선택 불가 (IS 표본 부족)':>26s}")
            continue
        key, is_st = best
        f, sl, am, g = key
        oos = [t for t in runs[key] if within(q, t.entry_time)]
        oos_all.extend(oos)
        picks.append((q, key))
        os_st = v.stats(oos)
        pts = sum(t.pnl for t in oos) / v.POINT_VALUE if oos else 0.0
        label = f"SMA{f}/{sl} ×{am} 게이트{g:.0f}"
        if os_st:
            print(f"  {q:10s}{label:>26s}{is_st['trades']:>7d}{is_st['pf']:>8.2f}"
                  f"{os_st['trades']:>8d}{os_st['win_rate']:>8.2f}%{os_st['pf']:>9.2f}{pts:>+13.1f}")
        else:
            print(f"  {q:10s}{label:>26s}{is_st['trades']:>7d}{is_st['pf']:>8.2f}"
                  f"{0:>8d}{'-':>9s}{'-':>9s}{0.0:>+13.1f}")

    print("  " + "-" * 118)
    st = v.stats(oos_all)
    if st:
        print(f"  {'OOS 합계':10s}{'':>26s}{'':>7s}{'':>8s}"
              f"{st['trades']:>8d}{st['win_rate']:>8.2f}%{st['pf']:>9.2f}"
              f"{sum(t.pnl for t in oos_all)/v.POINT_VALUE:>+13.1f}")
        print(f"  {'':10s}MDD {st['mdd']:.2f}% | 최종자본 {st['final']:,.0f}")

    # 비교군: 전체기간을 보고 고른 최적(후행) 조합을 같은 OOS 구간에 적용
    print(f"\n{'=' * 122}")
    print("[비교] 후행 최적 조합을 같은 구간에 적용하면")
    print("=" * 122)
    hind, hind_pf = None, -1.0
    for key, trades in runs.items():
        s2 = v.stats(trades)
        if s2 and s2["trades"] >= 15 and s2["pf"] > hind_pf:
            hind_pf, hind = s2["pf"], key
    f, sl, am, g = hind
    same_span = [t for t in runs[hind] if any(within(q, t.entry_time) for q in folds)]
    hs = v.stats(same_span)
    print(f"  후행 최적: SMA{f}/{sl} ×{am} 게이트{g:.0f} (전체기간 PF {hind_pf:.2f})")
    if hs:
        print(f"  같은 OOS 구간 성적: 거래{hs['trades']:>4d} 승률{hs['win_rate']:6.2f}% "
              f"PF{hs['pf']:6.2f} MDD{hs['mdd']:6.2f}%")
    print(f"\n  선택된 파라미터의 안정성:")
    for q, key in picks:
        print(f"    {q}  SMA{key[0]}/{key[1]} ×{key[2]} 게이트{key[3]:.0f}")


if __name__ == "__main__":
    main(5)
