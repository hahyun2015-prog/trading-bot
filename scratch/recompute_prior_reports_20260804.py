"""이전 보고서 수치 재계산 — 2026-08-04.

문제:
    선물매매_최종설정_백테스트_20260731.md 와 선물_15시진입차단_백테스트_20260803.md 의
    MDD·최종자본이 전부 '정률 10% 증거금' 가정으로 산출됐다. 2026-08-04 실측 결과
    증거금은 계약당 고정 10,360,560원이고, 정률 가정은 실제의 절반이라 계약수를
    2.11배 과대 산정했다. 즉 두 보고서의 수치는 실현 불가능한 레버리지 기준이다.
    수수료도 0.0065% 가정이었으나 실측은 0.0030%다.

    각 보고서의 '구성 간 상대 비교'는 유효하지만 절대 수치는 정정이 필요하다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

COMMON = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    profit_lock_be_buffer_pt=1.0,
)
SLIP = dict(slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0)
OLD = dict(SLIP, commission_rate=0.000065)                                    # 보고서 당시 가정
NEW = dict(SLIP, commission_rate=0.00003, margin_per_contract=10_360_560)     # 실측


def go(cost, **over):
    kw = dict(COMMON, **cost)
    kw.update(over)
    return run_chandelier_live_replica(df.copy(), **kw)


def line(label, r):
    if r is None:
        print(f"    {label:34s} 거래 없음"); return
    print(f"    {label:34s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | 자본{r['final_capital']:>16,.0f}")


df = load_futures_data('10100000')
print(f"[데이터] {len(df)}봉 | {df.index[0].date()} ~ {df.index[-1].date()}")
print("  ※ 보고서 작성 시점보다 데이터가 늘었다(31,194/31,277 → 현재). 비용 모델 외에")
print("     데이터 증가분도 수치 차이에 섞여 있다.")

# ── 보고서 1: 선물매매_최종설정_백테스트_20260731.md ──────────────────────
print("\n" + "=" * 132)
print("[보고서 1] 선물매매_최종설정_백테스트_20260731.md — 15시 컷오프·하드손절 없음")
print("=" * 132)
BASE_727 = dict(min_std_error_entry=0.9, regime_filter_enabled=False, profit_lock_enabled=False)
FINAL_731 = dict(min_std_error_entry=1.5, regime_filter_enabled=True, profit_lock_enabled=True,
                 profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
                 profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0)
for lab, cost in (("보고서 수치 (구 가정)", OLD), ("정정 수치 (실측 비용)", NEW)):
    print(f"  ── {lab} ──")
    line("7/27 기준선", go(cost, **BASE_727))
    line("최종 버전(당시)", go(cost, **FINAL_731))

# ── 보고서 2: 선물_15시진입차단_백테스트_20260803.md ──────────────────────
print("\n" + "=" * 132)
print("[보고서 2] 선물_15시진입차단_백테스트_20260803.md — 하드손절 없음")
print("=" * 132)
for lab, cost in (("보고서 수치 (구 가정)", OLD), ("정정 수치 (실측 비용)", NEW)):
    print(f"  ── {lab} ──")
    line("컷오프 없음", go(cost, **FINAL_731))
    line("15:00 이후 차단", go(cost, **FINAL_731, entry_end_hour=15, entry_end_minute=0))
