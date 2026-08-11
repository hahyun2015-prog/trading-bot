"""오버나잇 갭에 엣지가 있는가 — 신호 발굴 (2026-08-11).

왜 이걸 보나:
    오늘까지 기각된 후보 12종 이상은 전부 "기존 돌파+추세추종 틀 안의 변형"이었다.
    파라미터를 바꾸는 한 워크포워드에서 뒤집히는 패턴이 반복됐다. 틀 밖을 봐야 한다.

    이 시장에는 측정된 특이점이 하나 있다 — 오버나잇 갭이 비정상적으로 크다.
      평균 |갭| 3.21%, 중앙값 2.53%, 최대 15.33%  (A0568000 88일)
    그리고 야간 종가가 익일 시가를 잘 예측한다는 것도 확인했다(15일 중 11일).
    그런데 갭 자체를 매매 신호로 쓴 적은 한 번도 없다.

    현 시스템은 갭을 **무시**한다. 시초가에서 ±K×전일Range로 타점을 잡을 뿐,
    간밤에 얼마나 벌어졌는지는 진입 판단에 안 들어간다.

무엇을 재나:
    1. 갭 크기·방향이 당일 수익률을 예측하는가 (연속 vs 되돌림)
    2. 갭 구간별로 기존 전략 성적이 갈리는가 → 필터로 쓸 수 있는가
    3. 갭 자체를 진입 신호로 쓰면 (연속/역방향 단독 전략)

    엣지가 없으면 없다고 적고 끝낸다. 없는 것을 만들어내지 않는다.
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
COST_RT = 0.25 * 2 + 0.03 / 50_000 * 985 * 50_000 / 50_000   # 왕복 슬리피지 + 수수료(pt 환산)


def daily_frame(df):
    d = df.groupby("date_day").agg(o=("open", "first"), h=("high", "max"),
                                   l=("low", "min"), c=("close", "last"))
    d["prev_c"] = d["c"].shift()
    d["gap_pt"] = d["o"] - d["prev_c"]
    d["gap_pct"] = d["gap_pt"] / d["prev_c"] * 100
    d["day_ret_pt"] = d["c"] - d["o"]          # 시가→종가, 당일 매매로 잡을 수 있는 폭
    d["range_pt"] = d["h"] - d["l"]
    return d.dropna()


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    d = daily_frame(df)
    print(f"자료 10500000 | {len(d)}거래일 | 왕복 비용 약 {COST_RT:.2f}pt")
    print(f"갭 분포: 평균|갭| {d['gap_pt'].abs().mean():.2f}pt ({d['gap_pct'].abs().mean():.2f}%) "
          f"중앙값 {d['gap_pt'].abs().median():.2f}pt 최대 {d['gap_pt'].abs().max():.2f}pt")

    # ── 1. 갭이 당일 방향을 예측하는가 ────────────────────────────
    print(f"\n{'=' * 104}")
    print("[1] 갭 크기별 당일 수익(시가→종가) — 연속이면 갭과 같은 부호, 되돌림이면 반대")
    print("=" * 104)
    bins = [(-1e9, -20), (-20, -10), (-10, -3), (-3, 3), (3, 10), (10, 20), (20, 1e9)]
    print(f"  {'갭 구간(pt)':>16s}{'일수':>6s}{'평균 당일수익':>14s}{'중앙값':>10s}"
          f"{'같은부호 비율':>14s}{'|평균|>비용':>12s}")
    print("  " + "-" * 100)
    for lo, hi in bins:
        sub = d[(d["gap_pt"] > lo) & (d["gap_pt"] <= hi)]
        if len(sub) < 5:
            continue
        same = ((sub["gap_pt"] > 0) == (sub["day_ret_pt"] > 0)).mean() * 100
        m = sub["day_ret_pt"].mean()
        lab = f"{lo:.0f}~{hi:.0f}" if abs(lo) < 1e8 and abs(hi) < 1e8 else (f"<{hi:.0f}" if abs(lo) > 1e8 else f">{lo:.0f}")
        print(f"  {lab:>16s}{len(sub):>6d}{m:>+13.2f}pt{sub['day_ret_pt'].median():>+9.2f}"
              f"{same:>13.1f}%{'예' if abs(m) > COST_RT else '아니오':>12s}")

    # 상관
    r = np.corrcoef(d["gap_pt"], d["day_ret_pt"])[0, 1]
    print(f"\n  갭 vs 당일수익 상관계수: {r:+.3f}  "
          f"({'연속 경향' if r > 0.1 else '되돌림 경향' if r < -0.1 else '무상관'})")

    # ── 2. 갭 구간별 기존 전략 성적 ───────────────────────────────
    print(f"\n{'=' * 104}")
    print("[2] 갭 구간별 현 배포본 성적 — 필터로 쓸 수 있는가")
    print("=" * 104)
    tr = run_sar_or_bb_replica(df.copy(), **L.BASE, **CUR, return_trades=True).get("trade_log", [])
    gap_of = {k: v for k, v in zip(d.index, d["gap_pt"])}
    groups = {"큰 하락갭 (<-10)": lambda g: g < -10,
              "작은 하락갭 (-10~-3)": lambda g: -10 <= g < -3,
              "평탄 (-3~3)": lambda g: -3 <= g <= 3,
              "작은 상승갭 (3~10)": lambda g: 3 < g <= 10,
              "큰 상승갭 (>10)": lambda g: g > 10}
    print(f"  {'구간':22s}{'거래':>6s}{'승률':>9s}{'PF':>8s}{'손익(pt)':>11s}")
    print("  " + "-" * 100)
    for name, fn in groups.items():
        sel = [t for t in tr if fn(gap_of.get(t["entry_time"].strftime("%Y%m%d"), 0.0))]
        s = L.stat(sel)
        if s:
            print(f"  {name:22s}{s['n']:>6d}{s['wr']:>8.2f}%{s['pf']:>8.2f}{s['pts']:>+11.1f}")
        else:
            print(f"  {name:22s}{'거래 없음':>20s}")

    # ── 3. 갭 자체를 신호로 ───────────────────────────────────────
    print(f"\n{'=' * 104}")
    print("[3] 갭 단독 전략 — 시가 진입, 종가 청산 (비용 차감)")
    print("=" * 104)
    print(f"  {'규칙':32s}{'거래':>6s}{'승률':>9s}{'PF':>8s}{'손익(pt)':>11s}{'평균/거래':>11s}")
    print("  " + "-" * 100)
    rules = [
        ("갭>3 → LONG (연속)", lambda g: 1 if g > 3 else 0),
        ("갭<-3 → SHORT (연속)", lambda g: -1 if g < -3 else 0),
        ("갭>3 → SHORT (되돌림)", lambda g: -1 if g > 3 else 0),
        ("갭<-3 → LONG (되돌림)", lambda g: 1 if g < -3 else 0),
        ("갭>10 → LONG (큰갭 연속)", lambda g: 1 if g > 10 else 0),
        ("갭>10 → SHORT (큰갭 되돌림)", lambda g: -1 if g > 10 else 0),
        ("갭<-10 → SHORT (큰갭 연속)", lambda g: -1 if g < -10 else 0),
        ("갭<-10 → LONG (큰갭 되돌림)", lambda g: 1 if g < -10 else 0),
    ]
    for name, fn in rules:
        pos = d["gap_pt"].map(fn)
        sel = d[pos != 0]
        if len(sel) < 10:
            print(f"  {name:32s}{len(sel):>6d}  표본 부족")
            continue
        pnl = sel["day_ret_pt"] * pos[pos != 0] - COST_RT
        w, l = pnl[pnl > 0], pnl[pnl <= 0]
        pf = w.sum() / (-l.sum()) if len(l) and l.sum() < 0 else float("inf")
        print(f"  {name:32s}{len(sel):>6d}{len(w)/len(pnl)*100:>8.2f}%{pf:>8.2f}"
              f"{pnl.sum():>+11.1f}{pnl.mean():>+10.2f}pt")


if __name__ == "__main__":
    main()
