"""일일 손실 한도 최적화 (2026-08-13).

배경: 현행 10%(29.9pt)가 세 지표를 모두 악화시키는 것을 확인했다.
  한도 없음  PF 1.28 / +314.0pt / MDD 158.7pt
  한도 29.9  PF 1.21 / +241.9pt / MDD 213.5pt   ← 낙폭이 오히려 커짐
차단된 5건이 전부 이익 거래였고 합계가 +72.1pt로 손익 차이와 정확히 일치했다.

하루 평균 0.52건짜리 저빈도 전략이라 '나쁜 날' 판정 표본이 1~2건뿐이고,
첫 거래가 손실이면 그날이 차단되는데 그건 승률 52%의 절반일 뿐이다.

여기서 보는 것:
  ① 한도를 넓게 훑어 안정 구간이 있는지 (봉우리 하나면 표본 우연)
  ② 마진콜 생존 여부 — 낙폭이 예수금 여유(162.8pt)를 넘으면 계좌가 안 남는다
  ③ 워크포워드 — 분기마다 직전 데이터로 고른 한도가 다음 분기에 통하는지

오늘 내내 쓴 기각 기준을 그대로 적용한다. 전체 성적이 아니라 분기 일관성과
워크포워드로 판정하고, 비단조면 표본 우연으로 본다.
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import importlib.util

import numpy as np

_s = importlib.util.spec_from_file_location(
    "L", r"c:\Antigravity\AI_T_Agent\scratch\live_config_backtest_20260813.py")
L = importlib.util.module_from_spec(_s)
_s.loader.exec_module(L)

from bqa.kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

SLIP = 0.080
DEPOSIT = L.DEPOSIT
PV = L.POINT_VALUE
MARGIN_BUFFER_PT = 162.8      # 예수금 - 유지증거금 (현재가 1032.48pt 기준)

# 한도 후보: pt와 예수금 대비 비율을 함께 본다
GRID = [None, 10.0, 15.0, 20.0, 25.0, 29.9, 40.0, 50.0, 60.0, 80.0, 100.0]


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def run(df, limit):
    kw = dict(L.BASE)
    kw["daily_loss_limit_pt"] = limit
    kw.update(slip_entry_pt=SLIP, slip_exit_sl_pt=SLIP,
              slip_exit_normal_pt=SLIP, slip_exit_force_pt=SLIP)
    return run_sar_or_bb_replica(df.copy(), **kw, return_trades=True).get("trade_log", [])


def label(lim):
    if lim is None:
        return "없음"
    return f"{lim:.1f}pt ({lim * PV / DEPOSIT * 100:.0f}%)"


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    days = sorted(df["date_day"].unique())
    print(f"자료 {len(days)}거래일 | 슬리피지 편도 {SLIP}pt(실측) | 1계약")
    print(f"마진콜 여유 {MARGIN_BUFFER_PT:.1f}pt — 낙폭이 이를 넘으면 계좌 유지 불가")

    print(f"\n{'=' * 106}")
    print("[①] 한도 스윕")
    print("=" * 106)
    print(f"  {'한도':16s}{'거래':>6s}{'차단':>6s}{'승률':>9s}{'PF':>8s}"
          f"{'손익(pt)':>11s}{'MDD(pt)':>10s}{'마진':>8s}  분기별 PF")
    print("  " + "-" * 102)

    base_n = len(run(df, None))
    store = {}
    for lim in GRID:
        tr = run(df, lim)
        store[lim] = tr
        s = L.stat(tr)
        qs = {}
        for t in tr:
            qs.setdefault(q_of(t["entry_time"]), []).append(t)
        detail = " ".join(f"{q[2:]}:{L.stat(qs[q])['pf']:.2f}" for q in sorted(qs))
        alive = "생존" if s["mdd"] < MARGIN_BUFFER_PT else "마진콜"
        print(f"  {label(lim):16s}{s['n']:>6d}{base_n - s['n']:>6d}{s['wr']:>8.2f}%"
              f"{s['pf']:>8.2f}{s['pts']:>+11.1f}{s['mdd']:>10.1f}{alive:>8s}  {detail}")

    print(f"\n{'=' * 106}")
    print("[②] 분기 일관성 — PF>1 분기 수")
    print("=" * 106)
    for lim in GRID:
        qs = {}
        for t in store[lim]:
            qs.setdefault(q_of(t["entry_time"]), []).append(t)
        good = sum(1 for q in qs if L.stat(qs[q])["pf"] > 1.0)
        rng = max(L.stat(qs[q])["pf"] for q in qs) - min(L.stat(qs[q])["pf"] for q in qs)
        print(f"  {label(lim):16s} PF>1 {good}/{len(qs)} | 분기 PF 범위 {rng:.2f}")

    print(f"\n{'=' * 106}")
    print("[③] 워크포워드 — 분기마다 직전 데이터로 한도 선택")
    print("=" * 106)
    qs_all = sorted({q_of(t["entry_time"]) for ts in store.values() for t in ts})
    folds = [q for q in qs_all if q >= "2026-Q1"]
    MIN_IS = 20
    print(f"  {'분기':10s}{'IS 선택':>16s}{'IS거래':>7s}{'IS PF':>8s}"
          f"{'OOS거래':>8s}{'OOS PF':>9s}{'OOS손익':>11s}")
    print("  " + "-" * 102)
    oos, picks = [], []
    for q in folds:
        best, bpf = "미선택", -1.0
        for lim in GRID:
            istr = [t for t in store[lim] if q_of(t["entry_time"]) < q]
            if len(istr) < MIN_IS:
                continue
            s = L.stat(istr)
            if s and s["pf"] > bpf:
                bpf, best = s["pf"], lim
        if best == "미선택":
            print(f"  {q:10s}{'표본 부족':>16s}")
            continue
        sel = [t for t in store[best] if q_of(t["entry_time"]) == q]
        oos.extend(sel)
        picks.append(label(best))
        iss, oss = L.stat([t for t in store[best] if q_of(t["entry_time"]) < q]), L.stat(sel)
        print(f"  {q:10s}{label(best):>16s}{iss['n']:>7d}{iss['pf']:>8.2f}"
              f"{oss['n']:>8d}{oss['pf']:>9.2f}{oss['pts']:>+11.1f}")
    s = L.stat(oos)
    print("  " + "-" * 102)
    if s:
        print(f"  {'OOS 합계':10s}{'':>16s}{'':>7s}{'':>8s}{s['n']:>8d}{s['pf']:>9.2f}{s['pts']:>+11.1f}")
        print(f"  선택 안정성: {picks}")
    print(f"\n  [비교] 같은 OOS 구간에 고정 적용")
    for lim in (None, 29.9, 50.0):
        sel = [t for t in store[lim] if q_of(t["entry_time"]) in folds]
        st = L.stat(sel)
        if st:
            print(f"    {label(lim):16s} 거래{st['n']:>4d} PF{st['pf']:>6.2f} "
                  f"손익{st['pts']:>+8.1f}pt MDD{st['mdd']:>7.1f}pt")


if __name__ == "__main__":
    main()
