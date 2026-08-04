"""하드 초기손절 독립 검증 — 2026-08-04.

검증 대상 (사용자 작업분, 미배포):
    본전이동(be_move_trigger_pt=4pt) 도달 '이전' 구간에만
      floor = entry ∓ (hard_stop_se_mult × 진입시점_std_error)
    를 적용해, 한 번도 이익권에 못 간 트레이드가 넓은 샹들리에 트레일(≈28pt)을
    그대로 맞는 것을 막는다. 이익보전 대상(4pt 도달)은 건드리지 않는다.

    주석에 적힌 근거: 최악손실 -108.6→-17.9pt, PF 28.95→37.28,
    평균손 -4.2→-2.7pt, 수익 유지(+13,881→+13,957%)
    → 이 수치를 독립적으로 재현·검증한다.

주의:
    앞선 스윕들에서 확인됐듯 손절폭을 좁히면 백테스터가 낙관 편향을 보인다
    (5분봉 고가/저가 터치 가정). se_mult를 낮출수록 개선되는 단조 곡선이면
    그 자체가 경고 신호이므로 곡선 모양까지 본다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

BASE = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25, point_value=50_000,
    enable_reentry_filter=True,
    chandelier_mult=0.3, chandelier_hard_cap=60.0,
    session_range_cap_mult=0.5, session_range_cap_min_bars=6,
    min_std_error_entry=1.5,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True, profit_lock_enabled=True,
    profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0,
    entry_end_hour=15, entry_end_minute=0,
)
COST = dict(slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0)


def run(df, hs_on, se_mult=1.5, fixed_pt=None):
    return run_chandelier_live_replica(
        df.copy(), **BASE, **COST,
        hard_stop_enabled=hs_on, hard_stop_se_mult=se_mult, hard_stop_pt=fixed_pt)


def row(label, r, base=None):
    if r is None:
        print(f"  {label:26s} 거래 없음"); return None
    d = f"{r['final_capital']-base['final_capital']:>+16,.0f}" if base else f"{'(기준)':>16s}"
    print(f"  {label:26s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | "
          f"익{r['avg_win_pt']:+6.2f}/손{r['avg_loss_pt']:+6.2f} | {d}")
    return r


def section(title, df):
    print(f"\n{'='*148}")
    print(f"[{title}] {df.index[0].date()} ~ {df.index[-1].date()}")
    print('='*148)
    base = row("하드손절 OFF (현행)", run(df, False))
    print("  " + "-"*144)
    for m in (2.5, 2.0, 1.5, 1.0, 0.5):
        row(f"se_mult {m} (설정값={m==1.5})", run(df, True, se_mult=m), base)
    return base


def main():
    df = load_futures_data('10100000')
    print(f"[데이터] {len(df)}봉 | 비용: 차등 슬리피지 + 수수료 | 15시 컷오프 적용")
    print("[대상] 본전이동(4pt) 미도달 구간에만 floor = entry ∓ se_mult × 진입시 std_error")

    section("전체기간", df)
    last = pd.to_datetime(df['date_day'].iloc[-1], format='%Y%m%d')
    for days, name in ((60, "최근 60일"), (120, "최근 120일")):
        cut = (last - pd.Timedelta(days=days)).strftime('%Y%m%d')
        sub = df[df['date_day'] >= cut].copy()
        if not sub.empty:
            section(name, sub)

    print(f"\n{'='*148}")
    print("[분기별] 하드손절 OFF 대비 자본 차이 (se_mult=1.5, 설정값)")
    print('='*148)
    bounds = [("2025-Q1","20250101","20250401"),("2025-Q2","20250401","20250701"),
              ("2025-Q3","20250701","20251001"),("2025-Q4","20251001","20260101"),
              ("2026-Q1","20260101","20260401"),("2026-Q2","20260401","20260701"),
              ("2026-Q3","20260701","99999999")]
    tot, worse = 0, 0
    for q, s, e in bounds:
        sub = df[(df['date_day'] >= s) & (df['date_day'] < e)].copy()
        if len(sub) < 200:
            print(f"  {q:10s} 데이터 부족"); continue
        b = run(sub, False); r = run(sub, True, 1.5)
        if not (b and r):
            continue
        d = r['final_capital'] - b['final_capital']
        tot += d
        if d < 0:
            worse += 1
        print(f"  {q:10s} PF {b['pf']:>6.2f} → {r['pf']:>6.2f} | 최악 {b['worst_loss_pt']:>+8.2f} → {r['worst_loss_pt']:>+8.2f}pt | 자본차 {d:>+16,.0f}")
    print()
    print(f"  악화 분기 {worse}/7 | 분기합 {tot:+,.0f}원")


if __name__ == '__main__':
    main()
