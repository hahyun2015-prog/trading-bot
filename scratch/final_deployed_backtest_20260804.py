"""최종 배포본 백테스트 — 2026-08-04 21:14 배포(PID 5208) 기준.

이전 보고서들과 달라진 점:
  1) 15시 진입 컷오프 반영 (2026-08-03 적용)
  2) 하드 초기손절 반영 (2026-08-04 적용, se_mult 1.5)
  3) 증거금을 계약당 고정액 실측치로 (10,360,560원) — 기존 정률 10% 가정은
     실제의 절반이라 계약수를 2.11배 과대 산정, 복리 결과가 낙관 편향돼 있었다
  4) 수수료를 실측치로 (0.0030%) — 기존 0.0065% 가정은 2.2배 과대(보수적 방향)

백테스터가 모델링하지 못하는 배포분(참고):
  reentry_cooldown_sec 301, global_cooldown_sec 90, 틱무관 청산감시,
  마감창 고속동기화, 청산시점 재진입값 기록 — 전부 시간·체결 신뢰성 영역이라
  5분봉 백테스트로는 표현되지 않는다. 즉 아래 수치에 이 개선분은 빠져 있다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

# 2026-08-04 21:14 배포 시점 config/config.json 그대로
DEPLOYED = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    min_std_error_entry=1.5,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True,
    profit_lock_enabled=True,
    profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0,
    entry_end_hour=15, entry_end_minute=0,
    hard_stop_enabled=True, hard_stop_se_mult=1.5,
)
# 2026-08-04 실측 비용 모델
REAL_COST = dict(
    slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
    slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
    commission_rate=0.00003,            # 실측 0.0030% (7개 거래일 편차 0.000005%p)
    margin_per_contract=10_360_560,     # 실측 계약당 고정 증거금
)
# 비교용: 이전 보고서들이 쓰던 가정
OLD_COST = dict(
    slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
    slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
    commission_rate=0.000065,           # 가정 0.0065%
)


def run(df, cost, **over):
    kw = dict(DEPLOYED, **cost)
    kw.update(over)
    return run_chandelier_live_replica(df.copy(), **kw)


def show(label, r, base=None):
    if r is None:
        print(f"  {label:30s} 거래 없음"); return None
    d = f"{r['final_capital']-base['final_capital']:>+16,.0f}" if base else f"{'(기준)':>16s}"
    print(f"  {label:30s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | 평균계약{r['avg_contracts']:>5.2f} | "
          f"자본{r['final_capital']:>16,.0f} {d}")
    return r


def main():
    df = load_futures_data('10100000')
    print(f"[데이터] {len(df)}봉 | {df.index[0]} ~ {df.index[-1]}")
    print("[비용] 차등 슬리피지(1.5/3.0/0.5/2.0pt) + 실측 수수료 0.0030% + 실측 증거금 10,360,560원/계약")

    print(f"\n{'='*168}")
    print("[전체기간] 비용 가정에 따른 차이 — 이전 보고서 수치가 왜 달랐는지")
    print('='*168)
    a = show("이전 가정(수수료0.0065%/정률증거금)", run(df, OLD_COST))
    show("실측 수수료만 반영", run(df, dict(OLD_COST, commission_rate=0.00003)), a)
    show("실측 증거금만 반영", run(df, dict(OLD_COST, margin_per_contract=10_360_560)), a)
    final = show("★ 최종 배포본 (실측 비용 전부)", run(df, REAL_COST), a)

    last = pd.to_datetime(df['date_day'].iloc[-1], format='%Y%m%d')
    for days, name in ((60, "최근 60일"), (120, "최근 120일")):
        cut = (last - pd.Timedelta(days=days)).strftime('%Y%m%d')
        sub = df[df['date_day'] >= cut].copy()
        if sub.empty:
            continue
        print(f"\n{'='*168}")
        print(f"[{name}] {sub.index[0].date()} ~ {sub.index[-1].date()}")
        print('='*168)
        show("★ 최종 배포본", run(sub, REAL_COST))

    print(f"\n{'='*168}")
    print("[분기별] 최종 배포본 — 국면별 안정성")
    print('='*168)
    bounds = [("2025-Q1","20250101","20250401"),("2025-Q2","20250401","20250701"),
              ("2025-Q3","20250701","20251001"),("2025-Q4","20251001","20260101"),
              ("2026-Q1","20260101","20260401"),("2026-Q2","20260401","20260701"),
              ("2026-Q3","20260701","99999999")]
    prev_cap = None
    for q, s, e in bounds:
        sub = df[(df['date_day'] >= s) & (df['date_day'] < e)].copy()
        if len(sub) < 200:
            print(f"  {q:10s} 데이터 부족"); continue
        r = run(sub, REAL_COST)
        if not r:
            print(f"  {q:10s} 거래 없음"); continue
        print(f"  {q:10s} 거래{r['trades']:>4d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
              f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | 평균익{r['avg_win_pt']:+6.2f}/손{r['avg_loss_pt']:+6.2f}pt")

    if final:
        print(f"\n{'='*168}")
        print("[최종 배포본 요약]")
        print('='*168)
        print(f"  초기자본 50,000,000원 → 최종 {final['final_capital']:,.0f}원 ({(final['final_capital']/50_000_000-1)*100:+,.2f}%)")
        print(f"  거래 {final['trades']}건 | 승률 {final['win_rate']:.2f}% | PF {final['pf']:.2f} | MDD {final['mdd']:.2f}%")
        print(f"  평균익절 {final['avg_win_pt']:+.2f}pt | 평균손절 {final['avg_loss_pt']:+.2f}pt | 손익비 {abs(final['avg_loss_pt'])/final['avg_win_pt']:.3f}")
        print(f"  최악 단일손실 {final['worst_loss_pt']:+.2f}pt | 평균계약수 {final['avg_contracts']:.2f} | 최종계약수 {final['final_contracts']}")


if __name__ == '__main__':
    main()
