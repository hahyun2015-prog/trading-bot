"""역발상 검증 — 지는 전략을 뒤집으면 이기는가 (2026-08-11).

사용자 제안: 성적이 최악인 전략을 역으로 적용해 보자.

먼저 짚어야 할 산수:
    대칭 규칙(항상 진입, 방향만 반대)에서는
        원래 순손익 =  총수익 - 비용
        반전 순손익 = -총수익 - 비용
    비용은 양방향 모두 낸다. 따라서 반전이 이득이려면 총수익 < -비용,
    즉 비용을 빼기 전부터 크게 잃고 있어야 한다. PF 0.9로 아슬하게 지는
    전략을 뒤집으면 여전히 진다.

    그리고 갭 실험(gap_edge_study)의 표는 이미 양방향을 다 담고 있었다.
    최악이던 '갭>10 LONG'(PF 0.53)의 반전이 정확히 '갭>10 SHORT'(PF 1.72)이고,
    그건 워크포워드에서 기각됐다. 대칭 규칙 쪽은 이미 답이 나와 있다.

여기서 보는 것은 대칭이 아닌 쪽이다:
    배포본은 SAR 트레일링 청산이라 손익이 오른쪽으로 치우쳐 있다
    (작은 손실 다수 + 큰 이익 소수). 진입 방향만 뒤집고 트레일링을 그대로 두면
    -손익이 나오는 게 아니라 전혀 다른 전략이 된다. 이건 안 돌려 봤다.

    ① 진입 방향 반전 (reverse_entry)
    ② 이평선 필터 반전 — 역추세에서만 진입 (ma_filter_invert)
    ③ 둘 다
    ④ 저변동일만 진입 — '저변동일 스킵' 제안의 반대

기각 기준은 오늘 내내 쓴 것과 같다: 전체 PF가 아니라 분기 일관성으로 본다.
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

_g = importlib.util.spec_from_file_location(
    "S", r"c:\Antigravity\AI_T_Agent\scratch\sizing_direction_experiments_20260811.py")
S = importlib.util.module_from_spec(_g)
_g.loader.exec_module(S)

from bqa.kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

CUR = dict(ma_filter_period=200, sar_af_max=0.10, daily_loss_limit_pt=L.LIMIT_PT)


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def run(df, **over):
    kw = {**L.BASE, **CUR}
    kw.update(over)
    return run_sar_or_bb_replica(df.copy(), **kw, return_trades=True).get("trade_log", [])


def show(label, tr, mark=""):
    s = L.stat(tr)
    if s is None:
        print(f"  {label:30s} 거래 없음")
        return None
    qs = {}
    for t in tr:
        qs.setdefault(q_of(t["entry_time"]), []).append(t)
    good = sum(1 for q in qs if L.stat(qs[q])["pf"] > 1.0)
    print(f"  {label:30s} 거래{s['n']:>4d} 승률{s['wr']:>6.2f}% PF{s['pf']:>6.2f} "
          f"손익{s['pts']:>+8.1f}pt MDD{s['mdd']:>7.1f}pt 분기{good}/{len(qs)}{mark}")
    return s


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")

    print("=" * 118)
    print("[1] 배포본 진입 방향 반전 — 손절이 붙어 있으므로 -손익이 되지 않는다")
    print("=" * 118)
    base = run(df)
    show("현행 (기준)", base, "  ← 배포본")
    rev = show("① 진입 방향 반전", run(df, reverse_entry=True))
    b = L.stat(base)
    if rev:
        print()
        print(f"  대칭이라면 반전 손익은 {-b['pts']:+.1f}pt 근처여야 한다 → 실제 {rev['pts']:+.1f}pt")
        print(f"  차이 {rev['pts'] - (-b['pts']):+.1f}pt — 트레일링 청산이 비대칭을 만든 몫")

    print(f"\n{'=' * 118}")
    print("[2] 이평선 필터 반전 — 추세를 거슬러서만 진입")
    print("=" * 118)
    show("현행 (추세 순방향)", base, "  ← 배포본")
    show("② 필터 반전 (역추세만)", run(df, ma_filter_invert=True))
    show("③ 진입반전 + 필터반전", run(df, reverse_entry=True, ma_filter_invert=True))
    show("참고: 필터 없음 (정방향)", run(df, ma_filter_period=None))
    show("참고: 필터 없음 + 진입반전", run(df, ma_filter_period=None, reverse_entry=True))

    print(f"\n{'=' * 118}")
    print("[3] 저변동일만 진입 — '저변동일 스킵' 제안의 반대")
    print("=" * 118)
    atr_of = S.prior_day_atr(df)
    vol_hi = S.causal_median_flag(sorted(atr_of.keys()), atr_of)
    hi = [t for t in base if vol_hi.get(t["entry_time"].strftime("%Y%m%d"), True)]
    lo = [t for t in base if not vol_hi.get(t["entry_time"].strftime("%Y%m%d"), True)]
    show("고변동일만 (스킵안)", hi)
    show("④ 저변동일만 (반대)", lo)

    # 반전이 유망하면 분기별로 확인
    print(f"\n{'=' * 118}")
    print("[4] 분기별 — 반전이 실재하면 분기마다 유지돼야 한다")
    print("=" * 118)
    cands = {"현행": base, "① 진입반전": run(df, reverse_entry=True),
             "② 필터반전": run(df, ma_filter_invert=True)}
    qs_all = sorted({q_of(t["entry_time"]) for ts in cands.values() for t in ts})
    print(f"  {'구성':16s}" + "".join(f"{q:>12s}" for q in qs_all))
    print("  " + "-" * (16 + 12 * len(qs_all)))
    for name, tr in cands.items():
        row = f"  {name:16s}"
        for q in qs_all:
            sel = [t for t in tr if q_of(t["entry_time"]) == q]
            s = L.stat(sel)
            cell = "-" if s is None else "{:.2f}(n{})".format(s["pf"], s["n"])
            row += "{:>12s}".format(cell)
        print(row)


if __name__ == "__main__":
    main()
