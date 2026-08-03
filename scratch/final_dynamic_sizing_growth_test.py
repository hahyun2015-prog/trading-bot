import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

# 2026-07-27 최종 적용 설정 그대로 (era_order_manager.py:4673-4679 계약수 공식 +
# session_range_cap 추가분까지 반영) — 미니선물(point_value=50,000), 계약수를 그때그때
# 누적자본 기준으로 재계산(dynamic_sizing=True), 최대 15계약 상한.
# dynamic_sizing_growth_test.py(2026-07-25, session_range_cap 도입 전) 대비 차이는
# session_range_cap_mult=0.5 / session_range_cap_min_bars=6 추가뿐.
PROD_KW = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    min_std_error_entry=0.9,
    trim_std_outliers=1,
    trend_bar_minutes=15,
    consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
)

QUARTER_BOUNDARIES = [
    ("2025-Q1", "20250401"),
    ("2025-Q2", "20250701"),
    ("2025-Q3", "20251001"),
    ("2025-Q4", "20260101"),
    ("2026-Q1", "20260401"),
    ("2026-Q2", "20260701"),
    ("2026-Q3(진행중)", "99999999"),
]


def main():
    df = load_futures_data('10100000')
    if df.empty:
        print("[-] 데이터 없음")
        return

    res = run_chandelier_live_replica(df.copy(), **PROD_KW)
    if res is None:
        print("[-] 거래 없음")
        return

    print(f"\n{'='*90}")
    print("[전체 기간] 5천만원 시작, 최종 적용 설정(mult=0.3+세션레인지캡0.5, 미니선물+계약수 자동증가)")
    print('='*90)
    print(f"  총 거래: {res['trades']}건 | 승률 {res['win_rate']:.2f}% | PF {res['pf']:.2f} | MDD {res['mdd']:.2f}%")
    print(f"  초기자본: 50,000,000원")
    print(f"  최종자본: {res['final_capital']:,.0f}원")
    print(f"  총수익률: {(res['final_capital']/50_000_000-1)*100:+.2f}%")
    print(f"  최종 계약수: {res['final_contracts']}계약 | 평균 계약수: {res['avg_contracts']:.2f}계약")
    print(f"  최악 단일손실: {res['worst_loss_pt']:+.2f}pt")

    print(f"\n{'='*90}")
    print("[분기별 자본금 추이] (같은 연속 백테스트 내에서 분기 마지막 거래 시점 잔고)")
    print('='*90)
    equity = res['equity']
    days = res['equity_days']
    prev_cap = 50_000_000
    start_idx = 0
    for label, boundary in QUARTER_BOUNDARIES:
        idxs = [i for i in range(start_idx, len(days)) if days[i] < boundary]
        if not idxs:
            print(f"  {label}: 거래 없음")
            continue
        end_idx = idxs[-1]
        q_cap = equity[end_idx]
        q_trades = end_idx - start_idx + 1
        growth = (q_cap / prev_cap - 1) * 100
        print(f"  {label}: 거래 {q_trades:>3d}건 | 분기말 잔고 {q_cap:>15,.0f}원 | 분기수익률 {growth:+9.2f}%")
        prev_cap = q_cap
        start_idx = end_idx + 1


if __name__ == '__main__':
    main()
