# -*- coding: utf-8 -*-
"""분봉 간격 최적화 검토 (2026-08-16).

현행 5분봉. DB에 주간 이력은 5분봉뿐이라 상향(1분)은 불가능하고 하향만 가능하다.
  10500000  32,016봉 / 388거래일 / 최빈간격 300초
  1분봉은 A05609에 673봉(2026-08-14~15)뿐 — 야간수집기 산물이라 검증에 못 쓴다.

분봉을 바꾸면 파라미터 하나가 아니라 전역이 재척도된다. 봉 수로 정의된 것들의
'시간' 의미가 통째로 달라진다.

    ma_filter_period=200봉   5분 2.4일 → 15분 7.2일 → 30분 14.4일 → 60분 28.8일
    kf_window=40 / std_window=20 / bb_window=20 도 같은 비율로 늘어난다
    SAR은 봉당 1회 전진(sar_update_mode=bar)이므로 트레일링이 느려진다
    반면 진입 타점(전일Range×K)과 atr_cutoff(일봉 ATR)는 시간 불변이라 안 바뀐다

그래서 두 가지를 따로 잰다.

  [A] 그대로 전환  봉 수 파라미터 고정. "차트 간격만 바꾸면 어떻게 되나."
                   실무적으로 실제 일어날 일이지만, 여러 변화가 섞여 원인을 못 가른다.
  [B] 시간 정합    봉 수 파라미터를 간격 비율로 나눠 룩백 '시간'을 5분봉과 맞춘다.
                   샘플링 해상도만의 효과를 분리한다.

한계 — 결과 해석 전에 반드시 볼 것:
  · 봉이 굵어지면 봉 내부 경로가 사라진다. 백테스터는 고가/저가로 타점 터치와 SAR
    피격을 판정하므로, 60분봉에서는 "그 안에서 무엇이 먼저 일어났는지"를 알 수 없다.
    같은 봉에서 타점 터치와 손절이 함께 있으면 순서를 가정해야 한다 → 결과가 낙관/비관
    어느 쪽으로든 치우칠 수 있고, 그 편향은 간격이 커질수록 커진다.
  · 2026-08-11에 같은 축(15/30/60분)을 한 번 기각했다. 그때는 틱단위 SAR·다른 파라미터·
    다른 평가지표였다. 여기서는 현행 구성(bar 모드, sar_init_mult=2.0, 한도 없음)과
    ATR 정규화 평가로 다시 잰다.
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

# 봉 수로 정의된 파라미터 — [B] 시간정합에서 간격 비율로 나눈다
BAR_PARAMS = dict(ma_filter_period=200, kf_window=40, std_window=20,
                  bb_window=20, squeeze_window=100)


def resample(df, minutes, src_minutes=5):
    """src_minutes 봉을 minutes 봉으로. 거래일 경계를 넘지 않도록 date_day와 함께 묶는다.

    [2026-08-16] src_minutes 인자가 없던 때는 `if minutes == 5: return df`로 조기 반환했다.
    5분봉을 원본으로 가정한 것인데, 1분봉을 넣고 5분을 요청하면 아무 집계 없이 그대로
    돌려줘 '리샘플했다'고 착각하게 만든다. 정합 검증이 96% 불일치로 나온 원인이었다
    (데이터는 정상, 검증 코드가 깨져 있었음)."""
    if minutes == src_minutes:
        return df
    d = df.copy()
    # [2026-08-16 수정] 종전엔 일중 순번(cumcount // k)으로 묶었는데, 봉이 하나라도
    # 빠지면 이후 묶음이 통째로 밀린다. 5분봉은 하루 84봉이어야 하나 실제 평균 82.5봉이라
    # 결측이 있고, 그 탓에 리샘플 결과가 원본과 어긋났다(1분→5분 재구성 시 96.6% 불일치).
    # 시각을 간격 경계로 내림하면 결측과 무관하게 정확히 묶인다.
    # 세션이 08:45 시작이고 45는 5의 배수라 표준 floor가 경계와 맞는다.
    d["_i"] = pd.Series(d.index, index=d.index).dt.floor(f"{minutes}min")
    g = d.groupby(["date_day", "_i"])
    out = g.agg(open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"))
    out = out.reset_index()                      # date_day, _i 가 열로 나온다
    out.index = pd.DatetimeIndex(out["_i"].values)   # 묶음 경계 시각을 대표로
    out = out.drop(columns=["_i"])
    return out.sort_index()


def main():
    df5 = H.load_df()
    print(f"원본 5분봉 {len(df5):,} | 거래일 {df5['date_day'].nunique()}")
    print(f"평가: 건당 ATR배수 (거래일 클러스터 로버스트 SE) | 슬리피지 편도 {H.SLIP}pt")

    base_amap = H.atr_by_day(df5)      # ATR 정규화는 일봉 기준이라 간격과 무관 — 공통 사용

    for mode in ("A", "B"):
        title = ("[A] 그대로 전환 — 봉 수 파라미터 고정 (룩백 '시간'이 늘어남)"
                 if mode == "A" else
                 "[B] 시간 정합 — 봉 수를 간격 비율로 나눠 룩백 시간을 5분봉과 일치")
        print(f"\n{'=' * 112}")
        print(title)
        print("=" * 112)
        print(f"  {'간격':10s}{'봉수':>9s}{'거래':>6s}{'거래일':>7s}{'PF':>7s}{'WR':>8s}"
              f"{'손익pt':>10s}{'MDD':>8s}{'ATR평균':>10s}{'SE':>8s}{'t':>7s}  MA룩백")
        print("  " + "-" * 108)

        for m in (5, 15, 30, 60):
            d = resample(df5, m)
            over = {}
            if mode == "B" and m != 5:
                r = m // 5
                for k, v in BAR_PARAMS.items():
                    over[k] = max(2, int(round(v / r)))
            tr = H.run(d, **over)
            s = H.evaluate(tr, base_amap)
            if not s.get("n"):
                print(f"  {m:>3d}분{'':6s}{len(d):>9,}{'거래 없음':>20s}")
                continue
            ma_p = over.get("ma_filter_period", BAR_PARAMS["ma_filter_period"])
            look = ma_p * m / 60 / 6.5      # 세션 6.5시간 기준 거래일 환산
            mark = "  ← 현행" if m == 5 else ""
            print(f"  {m:>3d}분{'':6s}{len(d):>9,}{s['n']:>6d}{s['days']:>7d}{s['pf']:>7.3f}"
                  f"{s['wr']:>7.2f}%{s['pts']:>+10.1f}{s['mdd']:>8.1f}"
                  f"{s['atr_mean']:>+10.4f}{s['atr_se']:>8.4f}{s['t']:>+7.2f}"
                  f"  MA{ma_p}={look:.1f}일{mark}")


if __name__ == "__main__":
    main()
