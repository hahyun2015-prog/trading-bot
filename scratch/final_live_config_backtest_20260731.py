import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

# 2026-07-31 기준 config.json(futures_settings) 그대로 이식한 "최종 버전" 라이브 설정.
# 미니선물(point_value=50,000), 계약수는 그때그때 누적자본 기준 재계산(dynamic_sizing=True),
# 최대 15계약 상한.
FINAL_KW = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    min_std_error_entry=1.5,
    trim_std_outliers=1,
    trend_bar_minutes=15,
    consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True,
    profit_lock_enabled=True,
    profit_lock_trigger_pt=8.0,
    profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_be_move_trigger_pt=4.0,
    profit_lock_be_stage_buffer_pt=0.0,
)

# 2026-07-27 시점(레짐필터/이익보전 도입 전) 설정 — 이번 3일(7/29~7/31) 신규 반영분의
# 효과를 분리해서 보기 위한 비교 기준선.
BASELINE_KW = dict(FINAL_KW)
BASELINE_KW.update(dict(
    min_std_error_entry=0.9,
    regime_filter_enabled=False,
    profit_lock_enabled=False,
))

QUARTER_BOUNDARIES = [
    ("2025-Q1", "20250401"),
    ("2025-Q2", "20250701"),
    ("2025-Q3", "20251001"),
    ("2025-Q4", "20260101"),
    ("2026-Q1", "20260401"),
    ("2026-Q2", "20260701"),
    ("2026-Q3(진행중)", "99999999"),
]


def summarize(label, res):
    print(f"  [{label}] 거래 {res['trades']:>4d}건 | 승률 {res['win_rate']:6.2f}% | PF {res['pf']:6.2f} | "
          f"MDD {res['mdd']:6.2f}% | 최악손실 {res['worst_loss_pt']:+7.2f}pt | 최종자본 {res['final_capital']:>18,.0f}원")


def quarterly_table(label, res):
    print(f"\n  -- {label} 분기별 잔고 추이 --")
    equity, days = res['equity'], res['equity_days']
    prev_cap, start_idx = 50_000_000, 0
    for qlabel, boundary in QUARTER_BOUNDARIES:
        idxs = [i for i in range(start_idx, len(days)) if days[i] < boundary]
        if not idxs:
            print(f"    {qlabel}: 거래 없음")
            continue
        end_idx = idxs[-1]
        q_cap = equity[end_idx]
        q_trades = end_idx - start_idx + 1
        growth = (q_cap / prev_cap - 1) * 100
        print(f"    {qlabel}: 거래 {q_trades:>3d}건 | 분기말 잔고 {q_cap:>15,.0f}원 | 분기수익률 {growth:+9.2f}%")
        prev_cap = q_cap
        start_idx = end_idx + 1


def main():
    df = load_futures_data('10100000')
    if df.empty:
        print("[-] 표준 코스피200 데이터 없음 — 미니(10500000)로 재시도")
        df = load_futures_data('10500000')
    if df.empty:
        print("[-] 데이터 없음")
        return

    print(f"[데이터] 봉 수: {len(df)} | 기간: {df.index[0]} ~ {df.index[-1]}")

    print(f"\n{'='*100}")
    print("[전체기간] 최종 버전 vs 7/27 시점 기준선 (레짐필터+이익보전+진입임계 상향 효과)")
    print('='*100)
    res_final = run_chandelier_live_replica(df.copy(), **FINAL_KW)
    res_base = run_chandelier_live_replica(df.copy(), **BASELINE_KW)
    if res_base:
        summarize("7/27 기준선(구성 없음)", res_base)
    if res_final:
        summarize("최종 버전(현재 라이브)", res_final)

    if res_final:
        print(f"\n  [최종 버전 상세]")
        print(f"    초기자본: 50,000,000원")
        print(f"    최종자본: {res_final['final_capital']:,.0f}원")
        print(f"    총수익률: {(res_final['final_capital']/50_000_000-1)*100:+.2f}%")
        print(f"    최종 계약수: {res_final['final_contracts']}계약 | 평균 계약수: {res_final['avg_contracts']:.2f}계약")
        print(f"    평균 익절: {res_final['avg_win_pt']:+.2f}pt | 평균 손절: {res_final['avg_loss_pt']:+.2f}pt")
        quarterly_table("최종 버전", res_final)

    # 최근 60일/30일 구간 — 레짐필터/이익보전이 실제로 최근 장세에서도 개선 방향인지 교차검증
    print(f"\n{'='*100}")
    print("[최근 구간 교차검증]")
    print('='*100)
    last_day = df['date_day'].iloc[-1]
    import pandas as pd
    last_dt = pd.to_datetime(last_day, format='%Y%m%d')
    for days_back, label in [(30, "최근 30일"), (60, "최근 60일")]:
        cutoff = (last_dt - pd.Timedelta(days=days_back)).strftime('%Y%m%d')
        sub = df[df['date_day'] >= cutoff].copy()
        if sub.empty:
            print(f"  {label}: 데이터 없음")
            continue
        rb = run_chandelier_live_replica(sub.copy(), **BASELINE_KW)
        rf = run_chandelier_live_replica(sub.copy(), **FINAL_KW)
        print(f"\n  -- {label} ({sub.index[0].date()} ~ {sub.index[-1].date()}) --")
        if rb:
            summarize("기준선", rb)
        else:
            print("    기준선: 거래 없음")
        if rf:
            summarize("최종본", rf)
        else:
            print("    최종본: 거래 없음")


if __name__ == '__main__':
    main()
