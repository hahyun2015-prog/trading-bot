# -*- coding: utf-8 -*-
"""분봉 최적화 3단계 — 1분봉을 포함한 전 구간 비교 (2026-08-16).

bar_interval.py는 5분 이상만 봤다(5/15/30/60). 결과는 "굵을수록 나쁘다"는 단조였고,
그 추세를 왼쪽으로 연장하면 1분봉이 더 나을 수 있다는 물음이 남았다.
DB에 주간 1분봉이 없어 답할 수 없었는데, futures_ohlcv_1m을 확보해 이제 가능하다.

    futures_ohlcv_1m / 10500000  160,855봉 / 2025-01-03~2026-08-14 / 393거래일
    futures_ohlcv    / 10500000   32,016봉 / 2025-01-10~2026-08-14 / 388거래일

공정 비교를 위해 두 소스의 **공통 기간으로 잘라** 쓴다. 1분봉을 5분으로 리샘플하면
원본 5분봉과 거의 같아야 하므로, 그 일치도를 먼저 확인해 데이터 정합을 검증한다.

두 모드는 bar_interval.py와 같다.
  [A] 그대로 전환   봉 수 파라미터 고정 → 룩백 '시간'이 간격에 비례해 변함
                    1분봉에서 ma_filter_period=200은 0.5거래일에 불과하다.
  [B] 시간 정합     봉 수를 간격 비율로 곱/나눠 룩백 시간을 5분봉과 일치
                    1분봉이면 MA는 1000봉이 되어야 2.4거래일이다.

한계: 1분봉은 봉 내부 경로가 5분봉보다 잘 보이므로, 타점 터치·SAR 피격 판정이
더 정확하다. 즉 굵은 봉에서 생기던 낙관/비관 편향이 줄어드는 방향이다.
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
from bar_interval import resample, BAR_PARAMS


def clip_common(df, lo, hi):
    return df[(df["date_day"] >= lo) & (df["date_day"] <= hi)]


def main():
    d5 = H.load_df("10500000", table="futures_ohlcv")
    d1 = H.load_df("10500000", table="futures_ohlcv_1m")
    lo = max(d5["date_day"].min(), d1["date_day"].min())
    hi = min(d5["date_day"].max(), d1["date_day"].max())
    d5, d1 = clip_common(d5, lo, hi), clip_common(d1, lo, hi)
    print(f"공통 기간 {lo} ~ {hi}")
    print(f"  5분봉 {len(d5):,}봉 / {d5['date_day'].nunique()}일")
    print(f"  1분봉 {len(d1):,}봉 / {d1['date_day'].nunique()}일")

    # ── 데이터 정합 확인: 1분봉→5분 리샘플이 원본 5분봉과 맞는가 ──
    r5 = resample(d1, 5, src_minutes=1)
    j = pd.merge(d5[["close"]].assign(k=d5.index),
                 r5[["close"]].assign(k=r5.index), on="k", suffixes=("_o", "_r"))
    if len(j):
        diff = (j["close_o"] - j["close_r"]).abs()
        print(f"\n[정합 확인] 시각 일치 {len(j):,}봉 | 종가 차이 "
              f"평균 {diff.mean():.4f}pt / 최대 {diff.max():.2f}pt / "
              f"0.01pt 초과 {(diff > 0.01).mean() * 100:.2f}%")
    else:
        print("\n[정합 확인] 시각이 겹치는 봉이 없다 — 리샘플 기준이 다를 수 있음")

    base_amap = H.atr_by_day(d5)      # 일봉 기준이라 간격과 무관

    for mode in ("A", "B"):
        print(f"\n{'=' * 112}")
        print("[A] 그대로 전환 — 봉 수 파라미터 고정" if mode == "A"
              else "[B] 시간 정합 — 룩백 시간을 5분봉과 일치")
        print("=" * 112)
        print(f"  {'간격':9s}{'봉수':>9s}{'거래':>6s}{'거래일':>7s}{'PF':>7s}{'WR':>8s}"
              f"{'손익pt':>10s}{'MDD':>8s}{'ATR평균':>10s}{'SE':>8s}{'t':>7s}  MA룩백")
        print("  " + "-" * 108)

        for m in (1, 5, 15, 30):
            d = d1 if m == 1 else (d5 if m == 5 else resample(d5, m))
            over = {}
            if mode == "B" and m != 5:
                r = m / 5.0
                for k, v in BAR_PARAMS.items():
                    over[k] = max(2, int(round(v / r)))
            tr = H.run(d, **over)
            s = H.evaluate(tr, base_amap)
            if not s.get("n"):
                print(f"  {m:>2d}분{'':5s}{len(d):>9,}{'거래 없음':>22s}")
                continue
            ma_p = over.get("ma_filter_period", BAR_PARAMS["ma_filter_period"])
            look = ma_p * m / 60 / 6.5
            mark = "  ← 현행" if m == 5 else ""
            print(f"  {m:>2d}분{'':5s}{len(d):>9,}{s['n']:>6d}{s['days']:>7d}{s['pf']:>7.3f}"
                  f"{s['wr']:>7.2f}%{s['pts']:>+10.1f}{s['mdd']:>8.1f}"
                  f"{s['atr_mean']:>+10.4f}{s['atr_se']:>8.4f}{s['t']:>+7.2f}"
                  f"  MA{ma_p}={look:.1f}일{mark}")


if __name__ == "__main__":
    main()
