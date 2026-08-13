"""SAR 트레일링 폭 최적화 (2026-08-14).

왜 여기를 보나:
    2026-08-13 실거래 손실 -19.42pt는 손절(약 70pt)이 아니라 SAR 역전 청산에서 났다.
    손절폭은 거의 닿지 않는다 — 백테스트 202거래에서도 최악이 -67.4pt로 손절선 근처는
    한 번뿐이다. 즉 **단일 손실의 크기를 실제로 정하는 것은 손절폭이 아니라 트레일링 폭**이다.
    손실캡을 만지기 전에 여기부터 재는 게 순서다.

축은 셋. 한 번에 하나씩만 움직인다 — 격자 최댓값이 워크포워드에서 뒤집히는 것을
이 프로젝트에서 반복해서 봤다.

    sar_af_max   0.10   가속 상한. 클수록 트레일이 빨리 조여져 일찍 나간다.
    sar_af_step  0.02   가속 증가폭.
    sar_init_mult 1.0   진입 시 SAR 시작거리(ATR 배수). 클수록 초기 여유가 크다.

라이브 정합 확인: 진입 1019.36 / SAR 945.43 → 거리 73.93pt = 그날 ATR 73.93 × 1.0.
백테스터 sar_sl_mult=1.0, sar_init_mult=None(= sar_sl_mult 사용)과 같다.

판정은 오늘까지 쓴 기준 그대로 — 전체 성적이 아니라 단조성·분기 일관성·워크포워드.
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
PV = L.POINT_VALUE
DEPOSIT = 13_967_550                       # 2026-08-14 실계좌 예수금
PRICE = 1032.48
BUFFER_PT = (DEPOSIT - PRICE * PV * 0.132) / PV   # 마진콜까지 여유

BASE = dict(L.BASE)
BASE["daily_loss_limit_pt"] = None         # 08-13 검토로 비활성 확정
BASE.update(slip_entry_pt=SLIP, slip_exit_sl_pt=SLIP,
            slip_exit_normal_pt=SLIP, slip_exit_force_pt=SLIP)

CUR = dict(sar_af_max=0.10, sar_af_step=0.02, sar_init_mult=1.0)


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def run(df, **over):
    kw = dict(BASE)
    kw.update(CUR)
    kw.update(over)
    return run_sar_or_bb_replica(df.copy(), **kw, return_trades=True).get("trade_log", [])


def show(lab, tr, mark=""):
    s = L.stat(tr)
    if s is None:
        print(f"  {lab:22s} 거래 없음")
        return None
    qs = {}
    for t in tr:
        qs.setdefault(q_of(t["entry_time"]), []).append(t)
    good = sum(1 for q in qs if L.stat(qs[q])["pf"] > 1.0)
    rng = (max(L.stat(qs[q])["pf"] for q in qs) - min(L.stat(qs[q])["pf"] for q in qs))
    alive = "생존" if s["mdd"] < BUFFER_PT else "마진콜"
    print(f"  {lab:22s}{s['n']:>5d}{s['wr']:>8.2f}%{s['pf']:>7.2f}{s['pts']:>+10.1f}"
          f"{s['mdd']:>9.1f}{s['worst']:>+8.1f}{alive:>7s}  분기{good}/{len(qs)} 폭{rng:.2f}{mark}")
    return s


def header():
    print(f"  {'구성':22s}{'거래':>5s}{'승률':>9s}{'PF':>7s}{'손익':>10s}"
          f"{'MDD':>9s}{'최악':>8s}{'마진':>7s}  일관성")
    print("  " + "-" * 104)


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    days = sorted(df["date_day"].unique())
    print(f"자료 {len(days)}거래일 | 슬리피지 편도 {SLIP}pt | 1계약 | 일일한도 없음")
    print(f"예수금 {DEPOSIT:,}원 | 마진콜 여유 {BUFFER_PT:.1f}pt — MDD가 이를 넘으면 계좌 유지 불가")

    print(f"\n{'=' * 112}")
    print("[기준] 현 배포본")
    print("=" * 112)
    header()
    base = run(df)
    show("현행 af0.10/step0.02/init1.0", base, "  ← 현재")

    print(f"\n{'=' * 112}")
    print("[①] sar_af_max — 가속 상한 (작을수록 트레일이 느슨해 오래 들고 간다)")
    print("=" * 112)
    header()
    for v in (0.04, 0.06, 0.08, 0.10, 0.15, 0.20):
        show(f"af_max={v:.2f}", run(df, sar_af_max=v), "  ← 현재" if v == 0.10 else "")

    print(f"\n{'=' * 112}")
    print("[②] sar_init_mult — 진입 시 SAR 시작거리 (ATR 배수)")
    print("=" * 112)
    header()
    for v in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        show(f"init_mult={v:.2f}", run(df, sar_init_mult=v), "  ← 현재" if v == 1.0 else "")

    print(f"\n{'=' * 112}")
    print("[③] sar_af_step — 가속 증가폭")
    print("=" * 112)
    header()
    for v in (0.005, 0.01, 0.02, 0.04):
        show(f"af_step={v:.3f}", run(df, sar_af_step=v), "  ← 현재" if v == 0.02 else "")

    # ── 워크포워드 ────────────────────────────────────────────────
    print(f"\n{'=' * 112}")
    print("[검증] 워크포워드 — 분기마다 직전 데이터로 고른다")
    print("=" * 112)
    grid = {}
    for a in (0.04, 0.06, 0.08, 0.10, 0.15, 0.20):
        grid[("af_max", a)] = run(df, sar_af_max=a)
    for m in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        grid[("init_mult", m)] = run(df, sar_init_mult=m)
    print(f"  격자 {len(grid)}조합 (축별 독립, 조합 최적화는 하지 않는다)")

    qs_all = sorted({q_of(t["entry_time"]) for ts in grid.values() for t in ts})
    folds = [q for q in qs_all if q >= "2026-Q1"]
    MIN_IS = 25
    print(f"\n  {'분기':10s}{'IS 선택':>20s}{'IS거래':>7s}{'IS PF':>8s}"
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
            print(f"  {q:10s}{'표본 부족':>20s}")
            continue
        sel = [t for t in grid[best] if q_of(t["entry_time"]) == q]
        oos.extend(sel)
        picks.append(f"{best[0]}={best[1]}")
        iss, oss = L.stat([t for t in grid[best] if q_of(t["entry_time"]) < q]), L.stat(sel)
        print(f"  {q:10s}{f'{best[0]}={best[1]}':>20s}{iss['n']:>7d}{iss['pf']:>8.2f}"
              f"{oss['n']:>8d}{oss['pf']:>9.2f}{oss['pts']:>+11.1f}")
    s = L.stat(oos)
    print("  " + "-" * 108)
    if s:
        print(f"  {'OOS 합계':10s}{'':>20s}{'':>7s}{'':>8s}{s['n']:>8d}{s['pf']:>9.2f}{s['pts']:>+11.1f}")
        print(f"  선택 안정성: {picks}")
    print(f"\n  [비교] 같은 OOS 구간에 고정 적용")
    for key in (("af_max", 0.10), ("af_max", 0.06), ("init_mult", 1.0), ("init_mult", 1.5)):
        sel = [t for t in grid[key] if q_of(t["entry_time"]) in folds]
        st = L.stat(sel)
        if st:
            mark = "  ← 현행" if key in (("af_max", 0.10), ("init_mult", 1.0)) else ""
            print(f"    {f'{key[0]}={key[1]}':16s} 거래{st['n']:>4d} PF{st['pf']:>6.2f} "
                  f"손익{st['pts']:>+8.1f}pt MDD{st['mdd']:>7.1f}pt 최악{st['worst']:>+7.1f}pt{mark}")


if __name__ == "__main__":
    main()
