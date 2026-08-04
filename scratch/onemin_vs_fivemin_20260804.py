"""1분봉 vs 5분봉 백테스트 비교 — 5분봉 낙관 편향의 정량화 (2026-08-04).

배경:
    2026-08-04 하루 동안 파라미터 스윕에서 같은 신호가 세 번 반복됐다.
      session_range_cap 0.5→0.1  : 자본 +56억 (단조 증가)
      profit_lock_mult 0.10→0.03 : 자본 +93억 (단조 증가)
      08-04 09:09 거래 재생        : 실제 -27.64pt vs 재생 -11.85pt (15.8pt 낙관)
    전부 "5분봉 고가/저가 안에서 스탑을 스쳤지만 실제로는 체결되지 않았을" 케이스로
    설명된다. 청산 폭이 좁아질수록 이 편향이 커지므로, 좁히는 방향의 최적화가
    항상 좋아 보인다.

    1분봉이면 같은 스탑이 훨씬 정밀하게 판정되므로, 두 해상도의 차이가 곧
    "5분봉이 얼마나 낙관적인가"의 하한 추정치가 된다.

주의:
    봉 개수 기반 파라미터를 시간 기준으로 환산해야 한다. 환산하지 않으면
    칼만 필터가 보는 구간이 200분 → 40분으로 줄어 다른 전략이 되어버린다.
      kf_window                 40 → 200
      std_window                20 → 100
      session_range_cap_min_bars 6 → 30
    trend_bar_minutes는 시각 기반 버킷이라 그대로 둔다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

# 2026-08-04 배포본 설정 (봉 개수 기반 항목 제외)
DEPLOYED = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5,
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
COST = dict(slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
            commission_rate=0.00003, margin_rate=0.20)

# 봉 개수 기반 — 해상도별로 같은 '시간'을 보도록 환산
BARS_5M = dict(kf_window=40, std_window=20, session_range_cap_min_bars=6)
BARS_1M = dict(kf_window=200, std_window=100, session_range_cap_min_bars=30)


def show(label, r):
    if r is None:
        print(f"  {label:26s} 거래 없음"); return None
    print(f"  {label:26s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | "
          f"익{r['avg_win_pt']:+6.2f}/손{r['avg_loss_pt']:+6.2f} | 자본{r['final_capital']:>16,.0f}")
    return r


def main():
    d5 = load_futures_data('10100000', table='futures_ohlcv')
    d1 = load_futures_data('10100000', table='futures_ohlcv_1m')
    print(f"[5분봉] {len(d5):>7,}봉 | {d5.index[0]} ~ {d5.index[-1]}")
    print(f"[1분봉] {len(d1):>7,}봉 | {d1.index[0]} ~ {d1.index[-1]}")

    # 공통 구간으로 자른다 — 기간이 다르면 비교가 성립하지 않는다
    lo = max(d5['date_day'].min(), d1['date_day'].min())
    hi = min(d5['date_day'].max(), d1['date_day'].max())
    s5 = d5[(d5['date_day'] >= lo) & (d5['date_day'] <= hi)].copy()
    s1 = d1[(d1['date_day'] >= lo) & (d1['date_day'] <= hi)].copy()
    print(f"\n[공통 구간] {lo} ~ {hi} | 5분봉 {len(s5):,}봉 / 1분봉 {len(s1):,}봉")

    print(f"\n{'='*150}")
    print("[동일 설정 · 동일 기간] 해상도만 다름")
    print('='*150)
    r5 = show("5분봉", run_chandelier_live_replica(s5.copy(), **DEPLOYED, **COST, **BARS_5M))
    r1 = show("1분봉 (시간 환산)", run_chandelier_live_replica(s1.copy(), **DEPLOYED, **COST, **BARS_1M))

    if r5 and r1:
        print(f"\n{'='*150}")
        print("[판정]")
        print('='*150)
        gap = (r5['final_capital'] / r1['final_capital'] - 1) * 100 if r1['final_capital'] else 0
        print(f"  최종자본  5분봉 {r5['final_capital']:>16,.0f}  vs  1분봉 {r1['final_capital']:>16,.0f}")
        print(f"  → 5분봉이 {gap:+.1f}% 낙관적" if gap > 0 else f"  → 5분봉이 {gap:+.1f}% 보수적")
        print(f"  PF        {r5['pf']:>7.2f}  vs  {r1['pf']:>7.2f}")
        print(f"  평균손절  {r5['avg_loss_pt']:>+7.2f}pt  vs  {r1['avg_loss_pt']:>+7.2f}pt")
        print(f"  최악손실  {r5['worst_loss_pt']:>+7.2f}pt  vs  {r1['worst_loss_pt']:>+7.2f}pt")

    # 좁은 청산폭에서 편향이 커지는지 — 오늘 기각한 스윕을 1분봉으로 재확인
    print(f"\n{'='*150}")
    print("[session_range_cap 스윕] 5분봉에서 단조 증가하던 것이 1분봉에서도 그런가")
    print('='*150)
    print(f"  {'cap':>6s} {'5분봉 자본':>20s} {'1분봉 자본':>20s} {'괴리':>10s}")
    for cap in (0.5, 0.35, 0.2, 0.1):
        a = run_chandelier_live_replica(s5.copy(), **{**DEPLOYED, 'session_range_cap_mult': cap}, **COST, **BARS_5M)
        b = run_chandelier_live_replica(s1.copy(), **{**DEPLOYED, 'session_range_cap_mult': cap}, **COST, **BARS_1M)
        if a and b:
            g = (a['final_capital'] / b['final_capital'] - 1) * 100 if b['final_capital'] else 0
            print(f"  {cap:>6.2f} {a['final_capital']:>20,.0f} {b['final_capital']:>20,.0f} {g:>+9.1f}%")


if __name__ == '__main__':
    main()
