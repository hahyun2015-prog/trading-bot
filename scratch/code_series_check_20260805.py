"""백테스트 기초자료 코드 검증 — 10100000(정규) vs 10500000(미니) (2026-08-05).

배경:
    백테스터 기본값은 `10100000`이다. 그런데 ERA가 실제로 체결하는 종목은
    미니 KOSPI200 근월물(real_day_code, 예: A0568000)이고, 승수도 미니 기준
    50,000원을 쓴다. 두 계열은 만기 구조가 다르다.
      101 = KOSPI200 선물   → 분기월물(3/6/9/12)
      105 = 미니 KOSPI200   → 매월물(근월)
    잔존만기가 다르면 베이시스가 달라 가격 경로 자체가 달라진다.

    실측(2026-02-13~08-04, 공통 5분봉 3,163개):
      10100000 − A0568000 : 평균 +3.238pt, 일치율  0.1%
      10500000 − A0568000 : 평균 -2.677pt, 일치율 44.1%
      10100000 − 10500000 : 평균 +5.915pt, 표준편차 2.814

    즉 실거래 종목에 가까운 쪽은 10500000이다. 청산 폭이 4~8pt인 전략에서
    5.9pt 계통오차는 무시할 수 없으므로, 계열 교체가 결과를 바꾸는지 확인한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

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

BARS_5M = dict(kf_window=40, std_window=20, session_range_cap_min_bars=6)
BARS_1M = dict(kf_window=200, std_window=100, session_range_cap_min_bars=30)


def show(label, r):
    if r is None:
        print(f"  {label:34s} 거래 없음"); return None
    print(f"  {label:34s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | 자본{r['final_capital']:>16,.0f}")
    return r


def main():
    print("=" * 140)
    print("[5분봉] 계열 교체")
    print("=" * 140)
    res5 = {}
    for code, name in (('10100000', '정규(분기월) 10100000'), ('10500000', '미니(근월) 10500000')):
        d = load_futures_data(code, table='futures_ohlcv')
        res5[code] = show(f"{name} {len(d):,}봉", run_chandelier_live_replica(d, **DEPLOYED, **COST, **BARS_5M))

    print()
    print("=" * 140)
    print("[1분봉 + 5분 타점] 계열 교체")
    print("=" * 140)
    res1 = {}
    for code, name in (('10100000', '정규(분기월) 10100000'), ('10500000', '미니(근월) 10500000')):
        d = load_futures_data(code, table='futures_ohlcv_1m')
        res1[code] = show(f"{name} {len(d):,}봉",
                          run_chandelier_live_replica(d, **DEPLOYED, **COST, **BARS_1M,
                                                      signal_only_on_5min=True))

    print()
    print("=" * 140)
    print("[판정]")
    print("=" * 140)
    for tag, res in (('5분봉', res5), ('1분+5분타점', res1)):
        a, b = res.get('10100000'), res.get('10500000')
        if a and b:
            g = (a['final_capital'] / b['final_capital'] - 1) * 100 if b['final_capital'] else 0
            print(f"  {tag:12s} 정규 {a['final_capital']:>16,.0f}  vs  미니 {b['final_capital']:>16,.0f}  "
                  f"→ 정규가 {g:+.1f}%")


if __name__ == '__main__':
    main()
