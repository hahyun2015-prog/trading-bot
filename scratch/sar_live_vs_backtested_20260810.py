"""실거래 SAR과 백테스트된 SAR은 같은 전략인가 (2026-08-10).

배경:
    2026-08-09에 주간선물 전략을 chandelier → parabolic_sar로 전환했다. 근거는
    scratch/backtest_sar_bb_20260809.py 계열의 검증이었다. 그런데 전환 후 첫 거래일인
    2026-08-10 실거래가 0건으로 끝났고, 로그를 파보니 두 가지가 어긋나 있었다.

    (1) 진입 타점
        백테스트 : kf_price ± std_error × mult      (칼만 밴드, 장중 계속 이동)
        실거래   : 시초가 ± 전일Range × K           (돌파, 하루 종일 고정)
        era_order_manager.py:4049가 칼만 타점을 kalman/chandelier에만 주고,
        SAR은 else(4065행)로 빠져 돌파 타점을 쓴다. 08-10 로그의 LONG 998.52 /
        SHORT 980.96이 정확히 989.74 ± 0.2 × 43.90이다.

    (2) std_error
        백테스트 : 매 봉 칼만 잔차로 재계산
        실거래   : update_kalman_targets()가 SAR에서 호출되지 않아 초기값 0.5 고정
        min_std_error_entry=1.5와 비교되므로 **모든 진입이 조용히 반환**된다.
        08-10에 양방향 목표가를 모두 관통하고도 거래가 0건이고 차단 로그조차
        없었던 이유다.

    (2)만 고치면 "돌파 타점 + 게이트 없음"이라는, 백테스트한 적 없는 세 번째 조합이
    된다. 그래서 고치기 전에 어느 조합이 무엇인지부터 수치로 확인한다.

비교 대상:
    A  칼만밴드 + std_error게이트 1.5    ← 08-09 전환 근거가 된 검증 조합
    B1 돌파타점 + 게이트 없음(0.0)       ← (2)만 고쳤을 때 실제로 돌아갈 조합
    B2 돌파타점 + std_error게이트 1.5    ← 실거래 의도에 가장 가까운 해석
    (실거래 현 상태는 게이트가 항상 참이라 거래 0건 — 백테스트할 것이 없다)

주의:
    비용·기간·기타 파라미터는 전부 동일하게 두고 위 두 축만 바꾼다.
"""
import sys, os

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\bqa")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import json
import pandas as pd
from kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

CFG = json.load(open(r"c:\Antigravity\AI_T_Agent\config\config.json", encoding="utf-8"))["futures_settings"]
try:
    LOCAL = json.load(open(r"c:\Antigravity\AI_T_Agent\config\config_local.json", encoding="utf-8"))
    CFG.update(LOCAL.get("futures_settings", {}))
except Exception:
    pass

# 실거래와 같은 값으로 고정 (config.json + config_local.json 반영분)
BASE = dict(
    strategy="sar",
    Q=CFG["kf_q"], R=CFG["kf_r"], mult=CFG["kf_mult"],
    reentry_k=CFG["reentry_k"], point_value=50_000,
    trim_std_outliers=CFG.get("std_trim_outliers", 1),
    atr_cutoff=float(CFG.get("atr_cutoff", 15.0)),
    entry_end_hour=CFG.get("entry_end_hour"), entry_end_minute=CFG.get("entry_end_minute", 0),
    realistic_gap_fill=True,
    commission_rate=0.00003,                      # 2026-08-04 실측
    slip_entry_pt=1.5, slip_exit_sl_pt=3.0, slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
)
BREAKOUT_K = float(CFG.get("best_k", 0.2))
STD_GATE = float(CFG.get("min_std_error_entry", 1.5))

VARIANTS = [
    ("A  칼만밴드 + 게이트1.5", dict(entry_target_mode="kalman_band", min_std_error_entry=STD_GATE)),
    ("A' 칼만밴드 + 게이트없음", dict(entry_target_mode="kalman_band", min_std_error_entry=0.0)),
    ("B1 돌파타점 + 게이트없음", dict(entry_target_mode="breakout", breakout_k=BREAKOUT_K, min_std_error_entry=0.0)),
    ("B2 돌파타점 + 게이트1.5", dict(entry_target_mode="breakout", breakout_k=BREAKOUT_K, min_std_error_entry=STD_GATE)),
]

# 08-09 SAR 검증은 10500000(미니 연속, 380일)으로 돌렸다. 실제 체결 종목은 A0568000
# (미니 근월물, 120일)이다. 검증 재현성과 실물 정합성을 둘 다 보려면 양쪽이 필요하다.
CODES = [("10500000 (08-09 검증이 쓴 자료)", "10500000"),
         ("A0568000 (실제 체결 종목)", "A0568000")]


def fmt(r):
    if r is None:
        return "거래 없음"
    return (f"거래{r['trades']:>5d} 승률{r['win_rate']:6.2f}% PF{r['pf']:7.2f} "
            f"MDD{r['mdd']:6.2f}% 최악{r['worst_loss_pt']:+8.2f}pt "
            f"익{r['avg_win_pt']:+6.2f}/손{r['avg_loss_pt']:+6.2f} 자본{r['final_capital']:>15,.0f}")


def main():
    print(f"설정: atr_cutoff={BASE['atr_cutoff']} K={BREAKOUT_K} 게이트={STD_GATE} "
          f"진입종료={BASE['entry_end_hour']}:{BASE['entry_end_minute']:02d} "
          f"수수료={BASE['commission_rate']} 슬리피지=1.5/3.0/0.5/2.0pt")

    for title, code in CODES:
        df = load_futures_data(code, table="futures_ohlcv")
        if df is None or df.empty:
            print(f"\n[{title}] 데이터 없음 — 건너뜀")
            continue

        days = sorted(df["date_day"].unique())
        print(f"\n\n{'#' * 132}")
        print(f"# {title} — {len(df):,}봉 / {len(days)}거래일 | {days[0]} ~ {days[-1]}")
        print("#" * 132)

        spans = [("전체기간", days[0], days[-1])]
        for label, k in (("최근60일", 60), ("최근30일", 30)):
            if len(days) > k:
                spans.append((label, days[-k], days[-1]))

        for label, lo, hi in spans:
            sub = df[(df["date_day"] >= lo) & (df["date_day"] <= hi)]
            print(f"\n[{label}] {lo} ~ {hi} ({len(sub):,}봉)")
            print("-" * 132)
            for name, over in VARIANTS:
                r = run_sar_or_bb_replica(sub.copy(), **{**BASE, **over})
                print(f"  {name:24s} {fmt(r)}")


if __name__ == "__main__":
    main()
