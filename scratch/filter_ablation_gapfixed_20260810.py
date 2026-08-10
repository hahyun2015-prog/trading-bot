"""보조필터 개별 분해 — 정직한 체결 판정 위에서 다시 (2026-08-10).

배경:
    scratch 재현 스크립트 59개 중 48개가 realistic_gap_fill 없이 돌았다(감사:
    scratch/audit_gapfill_20260810.py). 유령체결이 켜진 상태에서는 승률이 81%로
    부풀고, 좁은 손절일수록 더 유리해 보인다. 지금 배포본에 붙어 있는 보조필터는
    대부분 그 위에서 채택된 것들이다.

      chandelier_mult 0.3        07-27  session_range_cap 0.5      07-27
      regime_filter               07-30  profit_lock 2단계          07-30/08-04
      15시 진입컷오프              08-03  hard_stop                  08-04
      min_std_error_entry 1.5     07-09  재진입 밴드/쿨다운          08-04

    이 중 무엇이 실제로 기여하고 무엇이 해가 되는지, realistic_gap_fill=True에서
    하나씩 떼어 본다(leave-one-out).

읽는 법:
    자본이 기준선보다 **높으면** 그 장치는 떼는 편이 낫다는 뜻이다.
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.stdout.reconfigure(encoding="utf-8")

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

COST = dict(commission_rate=0.00003, margin_rate=0.20,
            slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
            realistic_gap_fill=True)          # ← 이번 감사의 핵심

BASE = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    kf_window=40, std_window=20,
    min_std_error_entry=1.5,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True, profit_lock_enabled=True,
    profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0,
    entry_end_hour=15, entry_end_minute=0,
    hard_stop_enabled=True, hard_stop_se_mult=1.5,
)

# (표시명, 끄는 방법, 언제·왜 채택됐나)
ABLATIONS = [
    ("레짐필터",          dict(regime_filter_enabled=False),                  "07-30"),
    ("이익보전 2단계",     dict(profit_lock_enabled=False),                    "07-30/08-04"),
    ("하드 초기손절",      dict(hard_stop_enabled=False),                      "08-04"),
    ("15시 진입컷오프",    dict(entry_end_hour=None),                          "08-03"),
    ("std_error 진입필터", dict(min_std_error_entry=0.0),                      "07-09"),
    ("재진입 휩소필터",    dict(enable_reentry_filter=False),                  "이전"),
    ("세션레인지 캡",      dict(session_range_cap_mult=None),                  "07-27"),
    ("동적 사이징",        dict(dynamic_sizing=False),                         "이전"),
]

SWEEPS = [
    ("chandelier_mult", "chandelier_mult", [0.2, 0.3, 0.5, 0.8, 1.2, 2.0]),
    ("session_range_cap_mult", "session_range_cap_mult", [0.3, 0.5, 1.0, 2.0, None]),
    ("atr_cutoff", "atr_cutoff", [0.5, 5.0, 10.0, 15.0, 20.0]),
]


def run(df, **over):
    return run_chandelier_live_replica(df.copy(), **{**BASE, **COST, **over})


def line(label, r, base_cap=None):
    if r is None:
        return f"  {label:24s} 거래 없음"
    delta = ""
    if base_cap is not None:
        d = r["final_capital"] - base_cap
        delta = f" {d:>+15,.0f}"
    return (f"  {label:24s} 거래{r['trades']:>5d} 승률{r['win_rate']:6.2f}% PF{r['pf']:7.2f} "
            f"MDD{r['mdd']:7.2f}% 자본{r['final_capital']:>15,.0f}{delta}")


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    days = sorted(df["date_day"].unique())
    print(f"자료: 10500000 {len(df):,}봉 / {len(days)}거래일 | {days[0]} ~ {days[-1]}")
    print("체결 판정: realistic_gap_fill=True (정직한 갭 체결)")

    spans = [("전체기간", days[0], days[-1]), ("최근60일", days[-60], days[-1])]

    for label, lo, hi in spans:
        sub = df[(df["date_day"] >= lo) & (df["date_day"] <= hi)]
        print(f"\n{'=' * 136}")
        print(f"[{label}] {lo} ~ {hi}")
        print("=" * 136)

        base = run(sub)
        print(line("기준선(배포본 그대로)", base))
        base_cap = base["final_capital"] if base else 0
        print(f"  {'-' * 130}")

        results = []
        for name, over, when in ABLATIONS:
            r = run(sub, **over)
            results.append((name, r, when))
            print(line(f"− {name} ({when})", r, base_cap))

        # 도움이 안 되는 장치를 한꺼번에 뗀 조합
        harmful = {}
        for name, r, when in results:
            if r and r["final_capital"] > base_cap:
                for k, v in dict(ABLATIONS[[a[0] for a in ABLATIONS].index(name)][1]).items():
                    harmful[k] = v
        if harmful:
            r = run(sub, **harmful)
            print(f"  {'-' * 130}")
            print(line(f"해로운 것 전부 제거({len(harmful)}개)", r, base_cap))

    # 파라미터 스윕 — 전체기간만
    print(f"\n\n{'=' * 136}")
    print("[파라미터 스윕] 전체기간 — 유령체결 시절 정한 값이 여전히 최적인가")
    print("=" * 136)
    full = df
    for title, key, values in SWEEPS:
        print(f"\n  {title} (현재 {BASE.get(key)})")
        for v in values:
            r = run(full, **{key: v})
            mark = "  ← 현재" if v == BASE.get(key) else ""
            print(line(f"  {key}={v}", r) + mark)


if __name__ == "__main__":
    main()
