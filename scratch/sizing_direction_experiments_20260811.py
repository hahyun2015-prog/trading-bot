"""수익률 개선 지렛대 탐색 — 사이징·방향·컷오프 (2026-08-11).

배경:
    현 배포본(SAR B2 + MA200 + af0.10 + 일일한도)은 PF 1.19, 15계약 연환산 +10.2%다.
    오늘 검증에서 기각된 방향은 다시 보지 않는다:
      · 손절 조이기/캡        워크포워드 미지지, 캡은 갭 때문에 목표 달성 불가
      · BB 평균회귀           48개 변형 전부 PF<1
      · 다일보유              이월분 PF 0.94, 갭 위험만 추가
      · 시간축 확대           전 시간축 분기 2/7 동일
      · atr_cutoff 인상       워크포워드 미지지

    남은 미탐색 지렛대:
      (1) 변동성 연동 사이징 — 수익이 고변동 분기에 몰려 있는데(26-Q2 1.13, 26-Q3 1.88
          vs 25-Q4 0.67) 계약수는 항상 상한 15로 고정이다. 약한 국면에서 절반으로
          줄이면 같은 신호로 손익 분포가 개선되는지.
      (2) 방향 비대칭 — 전 구성에서 SHORT PF(1.34~1.72)가 LONG(0.86~1.44)보다 높았다.
          LONG을 절반/제로로 줄이면 어떻게 되는지. 단, 이는 표본 기간이 하락 우위였던
          레짐 효과일 수 있어 채택 기준을 높게 잡아야 한다.
      (3) 15:00 진입컷오프 재검 — 유령체결 시절 채택된 설정이다. 정직한 비용에서
          다시 재면 대가가 달라졌을 수 있다. (단, 채택의 핵심 근거였던 2026-06-11
          거래정지 갭은 비용 모델과 무관한 실화라는 점은 유지된다.)

방법론 — 후행편향 회피:
    사이징 문턱은 결과를 보고 고르지 않는다. 각 거래일의 "전일까지 관측된
    거래가능일(ATR>=cutoff) ATR의 확장 중앙값"과 당일 ATR(전일 기준 칼만 TR —
    백테스터가 진입 판정에 쓰는 바로 그 값)을 비교한다. 과거 데이터만 쓰므로 인과적이다.
    표본 10일 미만이면 중립(1.0배).

    가중은 축소만 한다(1.0 vs 0.5). 증거금 상한 때문에 현 예수금에서 15계약 초과는
    불가능하므로, "좋을 때 2배"가 아니라 "나쁠 때 절반"이 실행 가능한 형태다.
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


def prior_day_atr(df):
    """백테스터 atr_map과 동일: 일봉 TR을 칼만(Q=0.002,R=0.2) 평활, 전일 값을 당일에 부여."""
    daily = df.groupby("date_day").agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    pc = daily["c"].shift()
    tr = pd.concat([daily["h"] - daily["l"], (daily["h"] - pc).abs(), (daily["l"] - pc).abs()],
                   axis=1).max(axis=1)
    kf, P, Q, R = None, 1.0, 0.002, 0.2
    path = []
    for v in tr.values:
        if kf is None:
            kf = v
        else:
            P += Q
            K = P / (P + R)
            kf = kf + K * (v - kf)
            P = (1 - K) * P
        path.append(kf)
    days = daily.index.tolist()
    return {days[i]: (path[i - 1] if i > 0 else 2.0) for i in range(len(days))}


def causal_median_flag(days_sorted, atr_of, cutoff=15.0, min_n=10):
    """날짜별로 '이전 거래가능일 ATR 확장 중앙값 이상인가'를 인과적으로 계산."""
    hist = []
    flag = {}
    for d in days_sorted:
        a = atr_of.get(d, 0.0)
        if len(hist) >= min_n:
            flag[d] = a >= np.median(hist)
        else:
            flag[d] = True          # 표본 부족 시 중립(풀사이즈)
        if a >= cutoff:
            hist.append(a)
    return flag


def wstat(trades, weights):
    w = np.array(weights)
    p = np.array([t["pnl_pt"] for t in trades]) * w
    pos, neg = p[p > 0], p[p <= 0]
    gl = -neg.sum()
    eq = np.concatenate([[0.0], np.cumsum(p)])
    return dict(n=int((w > 0).sum()), avg_w=w[w > 0].mean() if (w > 0).any() else 0,
                pf=(pos.sum() / gl) if gl > 0 else float("inf"),
                pts=p.sum(), mdd=(np.maximum.accumulate(eq) - eq).max(), worst=p.min() if len(p) else 0)


def line(label, s):
    return (f"  {label:30s} 유효{s['n']:>4d} 평균비중{s['avg_w']:>5.2f} PF{s['pf']:>6.2f} "
            f"손익{s['pts']:>+8.1f}pt MDD{s['mdd']:>7.1f}pt 최악{s['worst']:>+7.1f}pt")


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    atr_of = prior_day_atr(df)
    days_sorted = sorted(atr_of.keys())
    vol_hi = causal_median_flag(days_sorted, atr_of)

    tr = run_sar_or_bb_replica(df.copy(), **L.BASE, **CUR, return_trades=True).get("trade_log", [])
    print(f"기준: 현 배포본 {len(tr)}건 (PF 1.19) | 인과적 확장중앙값으로 고/저변동 판정")
    hi_n = sum(1 for t in tr if vol_hi.get(t["entry_time"].strftime("%Y%m%d"), True))
    print(f"고변동일 진입 {hi_n}건 / 저변동일 진입 {len(tr)-hi_n}건")

    print(f"\n{'=' * 108}")
    print("[실험 1] 변동성 연동 사이징 — 저변동일 비중 축소 (인과적 판정)")
    print("=" * 108)
    schemes = [
        ("① 고정 (현행)",            lambda t: 1.0),
        ("② 저변동일 0.5배",         lambda t: 1.0 if vol_hi.get(t["entry_time"].strftime("%Y%m%d"), True) else 0.5),
        ("③ 저변동일 0배 (미진입)",   lambda t: 1.0 if vol_hi.get(t["entry_time"].strftime("%Y%m%d"), True) else 0.0),
    ]
    for name, fn in schemes:
        print(line(name, wstat(tr, [fn(t) for t in tr])))

    print(f"\n{'=' * 108}")
    print("[실험 2] 방향 비대칭 — SHORT 우위가 실재하는가")
    print("=" * 108)
    for d in ("LONG", "SHORT"):
        sub = [t for t in tr if t["direction"] == d]
        s = wstat(sub, [1.0] * len(sub))
        print(line(f"  {d} 단독", s))
    for name, wl in (("④ LONG 0.5배", 0.5), ("⑤ LONG 0배 (SHORT만)", 0.0)):
        print(line(name, wstat(tr, [1.0 if t["direction"] == "SHORT" else wl for t in tr])))

    # 연도별 방향 성적 — 레짐 의존성 판정용
    print("\n  방향별 반기 성적 (레짐 의존이면 반기마다 뒤집힌다):")
    half = {}
    for t in tr:
        k = f"{t['entry_time'].year}-H{1 if t['entry_time'].month <= 6 else 2}"
        half.setdefault(k, {"LONG": [], "SHORT": []})[t["direction"]].append(t["pnl_pt"])
    for k in sorted(half):
        row = f"    {k}: "
        for d in ("LONG", "SHORT"):
            v = half[k][d]
            if v:
                pos = sum(x for x in v if x > 0)
                neg = -sum(x for x in v if x <= 0)
                pf = pos / neg if neg > 0 else float("inf")
                row += f"{d} PF{pf:5.2f}(n{len(v):>3d})   "
            else:
                row += f"{d} 없음        "
        print(row)

    print(f"\n{'=' * 108}")
    print("[실험 3] 15:00 진입컷오프 재검 — 정직한 비용에서")
    print("=" * 108)
    for name, eh in (("⑥ 컷오프 15:00 (현행)", 15), ("⑦ 컷오프 없음", None)):
        tr2 = run_sar_or_bb_replica(df.copy(), **{**L.BASE, "entry_end_hour": eh}, **CUR,
                                    return_trades=True).get("trade_log", [])
        s = wstat(tr2, [1.0] * len(tr2))
        late = [t for t in tr2 if t["entry_time"].hour >= 15]
        extra = f" | 15시 이후 진입 {len(late)}건 {sum(t['pnl_pt'] for t in late):+.1f}pt" if late else ""
        print(line(name, s) + extra)


if __name__ == "__main__":
    main()
