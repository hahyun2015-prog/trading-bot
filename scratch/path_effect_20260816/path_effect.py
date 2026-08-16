# -*- coding: utf-8 -*-
"""MA200 필터 이득의 경로효과 분리 실험 (2026-08-16).

MA200필터_라이브제거검토_20260815.md §6-2가 지목한 실험이다.

물음:
    MA200을 켜면 MA-off 대비 건당 ATR엣지가 +0.0285 → +0.0424로 +0.0139 좋아진다.
    보고서는 이 격차의 82%가 '필터 판별력'이 아니라 '경로효과'라고 추정했다 —
    진입 1건이 막히면 쿨다운·재진입 순번이 재배열되어, 이후 거래 구성 자체가 달라진다.

    그렇다면 MA와 무관하게 아무 진입이나 같은 수만큼 막아도 비슷한 격차가 나오는가?
    나온다면 MA200의 이득은 판별력이 아니라 우연이고, 필터의 존재 이유가 사라진다.

방법:
    ① MA-off로 돌려 진입 시각 목록을 얻는다(모집단).
    ② 그중 K개를 무작위로 골라 block_entry_times로 차단하고 재실행한다.
       차단으로 경로가 바뀌므로 이후 진입 시각도 달라진다 — 그게 측정 대상이다.
    ③ 시드를 바꿔 N회 반복해 ATR엣지 분포를 만든다.
    ④ 실제 MA200의 +0.0424가 그 분포 안 어디에 위치하는지 본다.

    K는 MA200이 실제로 차단한 건수에 맞춘다(MA-off 진입 중 MA 조건 위반 건수).

판정:
    · MA200이 분포의 평범한 위치(백분위 30~70)  → 이득은 경로효과, 즉 우연
    · 상위 5% 밖                                → 판별력이 실재할 여지
    표본이 작아 어느 쪽이든 '증명'은 못 한다. 위치만 기록한다.
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import harness as H

N_ITER = 300


def main():
    df = H.load_df()
    amap = H.atr_by_day(df)

    on = H.run(df)                                   # MA200 (현행)
    off = H.run(df, ma_filter_period=None)           # MA 필터 없음
    s_on, s_off = H.evaluate(on, amap), H.evaluate(off, amap)

    print(H.fmt(s_on, "MA200 (현행)"))
    print(H.fmt(s_off, "MA-off"))
    gap = s_on["atr_mean"] - s_off["atr_mean"]
    print(f"\n관측 격차 (MA200 − off) = {gap:+.4f} ATR/건")

    # MA-off 진입 중 MA200 조건을 위반하는 건수 = 실제 차단 수
    ma = H.I.moving_average_series(df["close"].values, 200)
    idx = {t: i for i, t in enumerate(df.index)}
    blocked = []
    for t in off:
        i = idx.get(t["entry_time"])
        if i is None or i < 1 or not np.isfinite(ma[i - 1]):
            continue
        above = df["close"].values[i - 1] >= ma[i - 1]
        if (t["direction"] == "LONG" and not above) or (t["direction"] == "SHORT" and above):
            blocked.append(t["entry_time"])
    K = len(blocked)
    print(f"MA200이 차단하는 진입: {K}건 / MA-off {s_off['n']}건")

    # 무작위 K건 차단 반복
    times = [t["entry_time"] for t in off]
    rng = np.random.default_rng(20260816)
    vals, ns = [], []
    for it in range(N_ITER):
        pick = set(rng.choice(len(times), size=K, replace=False).tolist())
        bt = {times[j] for j in pick}
        tr = H.run(df, ma_filter_period=None, block_entry_times=bt)
        s = H.evaluate(tr, amap)
        if s["n"]:
            vals.append(s["atr_mean"])
            ns.append(s["n"])
        if (it + 1) % 50 == 0:
            print(f"  {it + 1}/{N_ITER} … 평균 {np.mean(vals):+.4f}")

    v = np.array(vals)
    pct = float((v < s_on["atr_mean"]).mean() * 100)
    print(f"\n{'=' * 88}")
    print(f"[무작위 {K}건 차단 × {len(v)}회]")
    print("=" * 88)
    print(f"  거래수      평균 {np.mean(ns):.1f}건 (MA200 {s_on['n']}건)")
    print(f"  ATR엣지     평균 {v.mean():+.4f} | SD {v.std(ddof=1):.4f}")
    print(f"              5% {np.percentile(v, 5):+.4f} | 50% {np.percentile(v, 50):+.4f} "
          f"| 95% {np.percentile(v, 95):+.4f}")
    print(f"  MA-off 기준 {s_off['atr_mean']:+.4f}")
    print(f"  MA200 실측  {s_on['atr_mean']:+.4f}  →  분포의 {pct:.1f} 백분위")
    print()
    rand_gap = v.mean() - s_off["atr_mean"]
    print(f"  무작위 차단만으로 생긴 평균 격차 {rand_gap:+.4f} "
          f"(관측 격차 {gap:+.4f}의 {rand_gap / gap * 100:.0f}%)")
    print()
    if pct < 95:
        print("  판정: MA200은 무작위 차단 분포 안에 있다 — 판별력의 증거가 아니다.")
    else:
        print("  판정: MA200이 상위 5% 밖 — 판별력이 실재할 여지가 있다.")


if __name__ == "__main__":
    main()
