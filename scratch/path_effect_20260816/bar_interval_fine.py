# -*- coding: utf-8 -*-
"""분봉 최적화 — 1분과 5분 사이 (2026-08-16).

bar_interval_1m.py에서 5분이 봉우리로 나왔다(1분 1.230/1.097 < 5분 1.505 > 15분 0.913/1.164).
그렇다면 진짜 꼭짓점이 2·3·4분 어딘가일 수 있다. 1분봉 원본이 있으니 만들 수 있다.

묶음 기준 — 세션 시작 앵커를 쓴다:
    주간 세션은 08:45 시작이다. 시계 기준 floor를 쓰면 45가 5로 나뉘어 5분봉은 맞지만
    2분·4분은 08:44 경계에 걸려 **첫 봉이 반토막**난다(08:45 한 개짜리 그룹).
    그래서 "세션 시작으로부터 경과 분 // 간격"으로 묶는다. 모든 봉이 같은 길이가 되고,
    시각 기반이라 결측봉에도 밀리지 않는다(bar_interval.py에서 겪은 cumcount 버그 회피).

    5분 요청 시 결과가 원본 5분봉과 일치하는지로 이 묶음 규칙 자체를 검증한다.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import harness as H
from bar_interval import BAR_PARAMS


def resample_session(df, minutes):
    """세션 시작 기준으로 minutes 분봉을 만든다. 원본은 1분봉을 가정."""
    if minutes == 1:
        return df
    d = df.copy()
    t = pd.Series(d.index, index=d.index)
    open_t = t.groupby(d["date_day"]).transform("min")
    d["_g"] = ((t - open_t).dt.total_seconds() // (minutes * 60)).astype(int)
    g = d.groupby(["date_day", "_g"])
    out = g.agg(open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"))
    lab = g.apply(lambda x: x.index[0])           # 묶음의 첫 봉 시각을 대표로
    out = out.reset_index(drop=True)
    out.index = pd.DatetimeIndex(lab.values)
    out["date_day"] = [x.strftime("%Y%m%d") for x in out.index]
    return out.sort_index()


def main():
    d5 = H.load_df("10500000", table="futures_ohlcv")
    d1 = H.load_df("10500000", table="futures_ohlcv_1m")
    lo = max(d5["date_day"].min(), d1["date_day"].min())
    hi = min(d5["date_day"].max(), d1["date_day"].max())
    d5 = d5[(d5["date_day"] >= lo) & (d5["date_day"] <= hi)]
    d1 = d1[(d1["date_day"] >= lo) & (d1["date_day"] <= hi)]
    print(f"공통 기간 {lo} ~ {hi} | 1분봉 {len(d1):,}봉 / {d1['date_day'].nunique()}일")

    # 묶음 규칙 검증: 1분→5분이 원본 5분봉과 같아야 한다
    r5 = resample_session(d1, 5)
    j = pd.merge(d5[["open", "high", "low", "close"]].assign(k=d5.index),
                 r5[["open", "high", "low", "close"]].assign(k=r5.index),
                 on="k", suffixes=("_o", "_r"))
    print(f"\n[묶음 규칙 검증] 시각 일치 {len(j):,}봉")
    for f in ("open", "high", "low", "close"):
        v = (j[f + "_o"] - j[f + "_r"]).abs()
        print(f"  {f:6s} 평균차 {v.mean():.5f} | 최대 {v.max():.3f} | 0.01pt 초과 {(v > 0.01).mean() * 100:.2f}%")

    amap = H.atr_by_day(d5)

    for mode in ("A", "B"):
        print(f"\n{'=' * 108}")
        print("[A] 그대로 전환 — 봉 수 파라미터 고정" if mode == "A"
              else "[B] 시간 정합 — 룩백 시간을 5분봉과 일치")
        print("=" * 108)
        print(f"  {'간격':8s}{'봉수':>9s}{'거래':>6s}{'거래일':>7s}{'PF':>7s}{'WR':>8s}"
              f"{'손익pt':>10s}{'MDD':>8s}{'ATR평균':>10s}{'SE':>8s}{'t':>7s}  MA")
        print("  " + "-" * 104)
        for m in (1, 2, 3, 4, 5):
            d = resample_session(d1, m)
            over = {}
            if mode == "B":
                r = m / 5.0
                for k, v in BAR_PARAMS.items():
                    over[k] = max(2, int(round(v / r)))
            tr = H.run(d, **over)
            s = H.evaluate(tr, amap)
            if not s.get("n"):
                print(f"  {m:>2d}분{'':4s}{len(d):>9,}{'거래 없음':>22s}")
                continue
            ma_p = over.get("ma_filter_period", BAR_PARAMS["ma_filter_period"])
            mark = "  ← 현행" if m == 5 else ""
            print(f"  {m:>2d}분{'':4s}{len(d):>9,}{s['n']:>6d}{s['days']:>7d}{s['pf']:>7.3f}"
                  f"{s['wr']:>7.2f}%{s['pts']:>+10.1f}{s['mdd']:>8.1f}"
                  f"{s['atr_mean']:>+10.4f}{s['atr_se']:>8.4f}{s['t']:>+7.2f}  {ma_p}{mark}")


if __name__ == "__main__":
    main()
