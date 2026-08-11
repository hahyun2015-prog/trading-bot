"""갭 되돌림 신호 워크포워드 검증 (2026-08-11).

앞선 탐색(gap_edge_study_20260811.py)에서 처음으로 기각되지 않은 후보가 나왔다.
  갭>10pt → 시가 SHORT, 종가 청산 : 60일, 승률 56.67%, PF 1.72
  대조군(매일 SHORT) 대비 거래당 +5.60pt

오늘 다른 후보들은 전부 이 시험에서 뒤집혔으므로, 같은 잣대를 댄다.
분기마다 그 이전 데이터만으로 문턱을 고르고, 다음 분기에 적용한다.

주의할 점:
  · 문턱을 결과 보고 고르면 안 되므로 격자에서 IS PF 최대인 것을 고른다
  · 대조군(같은 구간 매일 SHORT)을 함께 계산해 순효과를 본다.
    갭 신호가 없어도 벌리는 구간이면 PF만 보고 판단할 수 없다
  · 표본이 얇아 결론이 안 날 수 있다. 그것도 결과로 적는다
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import importlib.util

import numpy as np

_s = importlib.util.spec_from_file_location(
    "G", r"c:\Antigravity\AI_T_Agent\scratch\gap_edge_study_20260811.py")
G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(G)

from bqa.kalman_backtester import load_futures_data

COST = G.COST_RT
MIN_IS = 12          # 갭 이벤트가 드물어 20은 과하다. 12일 미만이면 선택 불가로 남긴다.


def q_of(day):
    return f"{day[:4]}-Q{(int(day[4:6]) - 1) // 3 + 1}"


def trades_for(d, thr, side):
    """thr 이상 상승갭이면 side 방향으로 시가 진입, 종가 청산."""
    sel = d[d["gap_pt"] > thr] if thr > 0 else d[d["gap_pt"] < thr]
    return [(day, r["day_ret_pt"] * side - COST) for day, r in sel.iterrows()]


def stat(rows):
    if not rows:
        return None
    p = np.array([x[1] for x in rows])
    w, l = p[p > 0], p[p <= 0]
    gl = -l.sum()
    return dict(n=len(p), wr=len(w) / len(p) * 100,
                pf=(w.sum() / gl) if gl > 0 else float("inf"),
                pts=p.sum(), avg=p.mean())


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    d = G.daily_frame(df)
    print(f"자료 {len(d)}거래일 | 왕복비용 {COST:.2f}pt | IS 최소 {MIN_IS}일")

    # 격자: 상승갭 되돌림(SHORT) + 하락갭 연속(SHORT)
    grid = {}
    for thr in (5, 8, 10, 15, 20):
        grid[("상승갭>%d SHORT" % thr, thr)] = trades_for(d, thr, -1)
    for thr in (-5, -8, -10, -15):
        grid[("하락갭<%d SHORT" % thr, thr)] = trades_for(d, thr, -1)
    print(f"격자 {len(grid)}조합 (상승갭 되돌림 5 + 하락갭 연속 4)")

    days = sorted(d.index)
    quarters = sorted({q_of(x) for x in days})
    folds = [q for q in quarters if q >= "2026-Q1"]

    print(f"\n{'=' * 110}")
    print("[워크포워드] 분기마다 직전 데이터만으로 문턱 선택")
    print("=" * 110)
    print(f"  {'분기':10s}{'IS 선택':>20s}{'IS일수':>7s}{'IS PF':>8s}"
          f"{'OOS일수':>8s}{'OOS PF':>9s}{'OOS손익':>11s}{'대조군':>11s}{'순효과':>10s}")
    print("  " + "-" * 106)

    oos, picks = [], []
    for q in folds:
        best, bpf = None, -1.0
        for key, rows in grid.items():
            istr = [r for r in rows if q_of(r[0]) < q]
            if len(istr) < MIN_IS:
                continue
            s = stat(istr)
            if s and s["pf"] > bpf:
                bpf, best = s["pf"], key
        if best is None:
            print(f"  {q:10s}{'선택 불가 (IS 표본 부족)':>20s}")
            continue
        sel = [r for r in grid[best] if q_of(r[0]) == q]
        oos.extend(sel)
        picks.append(best[0])
        iss = stat([r for r in grid[best] if q_of(r[0]) < q])
        oss = stat(sel)
        # 같은 분기 대조군: 매일 SHORT
        ctl = stat([(day, r["day_ret_pt"] * -1 - COST) for day, r in d.iterrows() if q_of(day) == q])
        if oss:
            net = oss["avg"] - ctl["avg"]
            print(f"  {q:10s}{best[0]:>20s}{iss['n']:>7d}{iss['pf']:>8.2f}"
                  f"{oss['n']:>8d}{oss['pf']:>9.2f}{oss['pts']:>+11.1f}"
                  f"{ctl['avg']:>+10.2f}pt{net:>+9.2f}pt")
        else:
            print(f"  {q:10s}{best[0]:>20s}{iss['n']:>7d}{iss['pf']:>8.2f}"
                  f"{0:>8d}{'-':>9s}{0.0:>+11.1f}{ctl['avg']:>+10.2f}pt{'-':>10s}")

    print("  " + "-" * 106)
    s = stat(oos)
    if s:
        ctl_all = stat([(day, r["day_ret_pt"] * -1 - COST) for day, r in d.iterrows()
                        if q_of(day) in folds])
        print(f"  {'OOS 합계':10s}{'':>20s}{'':>7s}{'':>8s}{s['n']:>8d}{s['pf']:>9.2f}"
              f"{s['pts']:>+11.1f}{ctl_all['avg']:>+10.2f}pt{s['avg']-ctl_all['avg']:>+9.2f}pt")
        print(f"  선택 안정성: {picks}")
        print(f"  거래당 평균 {s['avg']:+.2f}pt | 승률 {s['wr']:.2f}%")

    # 고정 문턱을 같은 구간에 적용했다면
    print(f"\n  [비교] 고정 문턱을 같은 OOS 구간에")
    for label, key in (("갭>10 SHORT", ("상승갭>10 SHORT", 10)),
                       ("갭>15 SHORT", ("상승갭>15 SHORT", 15)),
                       ("갭>20 SHORT", ("상승갭>20 SHORT", 20))):
        sel = [r for r in grid[key] if q_of(r[0]) in folds]
        s2 = stat(sel)
        if s2:
            print(f"    {label:14s} {s2['n']:>3d}일 승률{s2['wr']:>6.2f}% PF{s2['pf']:>6.2f} "
                  f"손익{s2['pts']:>+8.1f}pt 거래당{s2['avg']:>+6.2f}pt")


if __name__ == "__main__":
    main()
