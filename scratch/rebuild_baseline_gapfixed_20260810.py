"""정직한 기준선 재구축 — 유령체결 시절 설정을 되돌리면 어디까지 가나 (2026-08-11).

앞선 분해(scratch/filter_ablation_gapfixed_20260810.py) 결과:

  떼면 좋아지는 것          이익보전 +7,414만 / 하드손절 +1,538만 / 세션레인지캡 +580만
  떼면 나빠지는 것          동적사이징 -3.96억 / std_error필터 -5,557만 / 재진입필터 -3,242만
  중립                      레짐필터 -64만
  파라미터                  chandelier_mult·session_range_cap·atr_cutoff 모두
                            "느슨할수록 좋다" — 채택 당시와 정반대 방향

  유령체결에서는 목표가에 항상 체결되므로 좁은 손절이 이득처럼 보인다. 정직한
  체결에서는 좁은 손절이 그냥 비용이다. 방향이 뒤집힌 이유가 이것으로 설명된다.

여기서는 (1) 되돌린 조합이 어디까지 가는지, (2) atr_cutoff를 더 올리면 계속 좋아지는지,
(3) 그 결과가 SAR B2(전체 PF 0.84)를 넘는지 본다.
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.stdout.reconfigure(encoding="utf-8")

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

COST = dict(commission_rate=0.00003, margin_rate=0.20,
            slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
            realistic_gap_fill=True)

DEPLOYED = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    kf_window=40, std_window=20, min_std_error_entry=1.5,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True, profit_lock_enabled=True,
    profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0,
    entry_end_hour=15, entry_end_minute=0,
    hard_stop_enabled=True, hard_stop_se_mult=1.5,
)

# 분해 결과가 가리키는 방향으로 되돌린 조합
REBUILT = dict(DEPLOYED)
REBUILT.update(profit_lock_enabled=False,        # +7,414만
               hard_stop_enabled=False,          # +1,538만
               session_range_cap_mult=None,      # +580만
               chandelier_mult=1.2,              # 스윕 상 0.3보다 우수
               atr_cutoff=20.0)                  # 스윕 상 계속 개선

STAGES = [
    ("① 배포본 그대로", DEPLOYED),
    ("② +이익보전 해제", {**DEPLOYED, "profit_lock_enabled": False}),
    ("③ +하드손절 해제", {**DEPLOYED, "profit_lock_enabled": False, "hard_stop_enabled": False}),
    ("④ +세션캡 해제", {**DEPLOYED, "profit_lock_enabled": False, "hard_stop_enabled": False,
                     "session_range_cap_mult": None}),
    ("⑤ +트레일 1.2", {**DEPLOYED, "profit_lock_enabled": False, "hard_stop_enabled": False,
                     "session_range_cap_mult": None, "chandelier_mult": 1.2}),
    ("⑥ +atr컷 20 (최종)", REBUILT),
]


def run(df, cfg, **over):
    return run_chandelier_live_replica(df.copy(), **{**cfg, **COST, **over})


def line(label, r):
    if r is None:
        return f"  {label:22s} 거래 없음"
    return (f"  {label:22s} 거래{r['trades']:>5d} 승률{r['win_rate']:6.2f}% PF{r['pf']:7.2f} "
            f"MDD{r['mdd']:8.2f}% 최악{r['worst_loss_pt']:+8.2f}pt 자본{r['final_capital']:>15,.0f}")


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    days = sorted(df["date_day"].unique())
    print(f"자료: 10500000 {len(df):,}봉 / {len(days)}거래일 | realistic_gap_fill=True")

    for label, k in (("전체기간", None), ("최근60일", 60), ("최근30일", 30)):
        sub = df if k is None else df[df["date_day"] >= days[-k]]
        print(f"\n{'=' * 128}")
        print(f"[{label}] 되돌리기 단계별 누적")
        print("=" * 128)
        for name, cfg in STAGES:
            print(line(name, run(sub, cfg)))

    print(f"\n\n{'=' * 128}")
    print("[atr_cutoff 추가 스윕] 최종 조합 위에서 더 올리면 계속 좋아지나 (전체기간)")
    print("=" * 128)
    for v in (15.0, 20.0, 25.0, 30.0, 40.0, 60.0):
        print(line(f"atr_cutoff={v}", run(df, REBUILT, atr_cutoff=v)))

    print(f"\n{'=' * 128}")
    print("[chandelier_mult 재스윕] 최종 조합 위에서 (전체기간)")
    print("=" * 128)
    for v in (0.5, 0.8, 1.2, 1.6, 2.0, 3.0):
        print(line(f"chandelier_mult={v}", run(df, REBUILT, chandelier_mult=v)))


if __name__ == "__main__":
    main()
