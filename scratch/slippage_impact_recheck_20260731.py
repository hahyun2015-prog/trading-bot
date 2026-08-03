"""2026-07-31 백테스트 비용 반영 재검증.

최초 실행(final_live_config_backtest_20260731.py)은 slip_* 인자를 하나도 넘기지 않아
_any_new_slip=False가 되면서 차등 슬리피지(진입1.5/SL3.0/익절0.5/강제2.0pt)가 적용되지
않고 전 구간 slip_fee_pt=0.05pt로 계산됐다. 즉 거래비용이 사실상 0에 가까웠다.
여기서 현실적인 차등 슬리피지를 넣어 다시 돌리고 그 차이를 정량화한다.
(수수료 commission_rate=0.000065는 기본값으로 양쪽 모두 반영돼 있었다.)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

BASE = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
)

# 2026-07-30 보고서가 명시한 차등 슬리피지 — 이번엔 명시적으로 전달한다.
REAL_COST = dict(slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
                 slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0)

FINAL_EXTRA = dict(
    min_std_error_entry=1.5,
    regime_filter_enabled=True, profit_lock_enabled=True,
    profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0,
)
BASE_EXTRA = dict(
    min_std_error_entry=0.9,
    regime_filter_enabled=False, profit_lock_enabled=False,
)


def row(label, res):
    if res is None:
        print(f'  {label:34s} 거래없음')
        return
    print(f'  {label:34s} 거래 {res["trades"]:>5d} | 승률 {res["win_rate"]:6.2f}% | PF {res["pf"]:7.2f} | '
          f'MDD {res["mdd"]:6.2f}% | 최종자본 {res["final_capital"]:>17,.0f}원')


def main():
    df = load_futures_data('10100000')
    print(f'[데이터] {len(df)}봉 | {df.index[0]} ~ {df.index[-1]}\n')

    print('=' * 118)
    print('[A] 비용 거의 0 (slip 0.05pt) — 최초 보고서에 실린 수치 (오류)')
    print('=' * 118)
    a_base = run_chandelier_live_replica(df.copy(), **BASE, **BASE_EXTRA)
    a_fin = run_chandelier_live_replica(df.copy(), **BASE, **FINAL_EXTRA)
    row('7/27 기준선', a_base)
    row('최종 버전', a_fin)

    print()
    print('=' * 118)
    print('[B] 현실적 차등 슬리피지 (진입1.5/SL3.0/익절0.5/강제2.0pt) — 정정된 수치')
    print('=' * 118)
    b_base = run_chandelier_live_replica(df.copy(), **BASE, **BASE_EXTRA, **REAL_COST)
    b_fin = run_chandelier_live_replica(df.copy(), **BASE, **FINAL_EXTRA, **REAL_COST)
    row('7/27 기준선', b_base)
    row('최종 버전', b_fin)

    print()
    print('=' * 118)
    print('[C] 최근 60일 — 현실적 비용')
    print('=' * 118)
    import pandas as pd
    last_dt = pd.to_datetime(df['date_day'].iloc[-1], format='%Y%m%d')
    cutoff = (last_dt - pd.Timedelta(days=60)).strftime('%Y%m%d')
    sub = df[df['date_day'] >= cutoff].copy()
    row('기준선(최근60일)', run_chandelier_live_replica(sub.copy(), **BASE, **BASE_EXTRA, **REAL_COST))
    row('최종본(최근60일)', run_chandelier_live_replica(sub.copy(), **BASE, **FINAL_EXTRA, **REAL_COST))

    print()
    print('=' * 118)
    print('판정')
    print('=' * 118)
    if a_fin and b_fin:
        print(f'  최종 버전 최종자본: 비용0 {a_fin["final_capital"]:,.0f}원 -> 현실비용 {b_fin["final_capital"]:,.0f}원')
        print(f'  최종 버전 PF      : 비용0 {a_fin["pf"]:.2f} -> 현실비용 {b_fin["pf"]:.2f}')
        print(f'  최종 버전 승률    : 비용0 {a_fin["win_rate"]:.2f}% -> 현실비용 {b_fin["win_rate"]:.2f}%')
    if b_base and b_fin:
        better = b_fin['final_capital'] > b_base['final_capital']
        print(f'\n  현실 비용 기준으로도 최종 버전이 기준선보다 우수한가? -> {"예" if better else "아니오"}')


if __name__ == '__main__':
    main()
