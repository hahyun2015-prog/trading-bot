"""재진입 휩소밴드 계수 스윕 — 2026-08-04.

문제:
    청산 후 같은 방향 재진입이 오늘 손실의 대부분을 만들었다.
      10분 이내 재진입 7건 -40.35pt, 그중 같은 방향 4건이 -52.73pt.
      최악 2건(-26.69, -25.87)이 모두 5분 만의 같은 방향 재진입.

    휩소밴드는 청산가 주변에서만 막는다:
      SHORT: [청산가 - unit*breakout, 청산가 + unit*pullback]
      unit = prev_range * reentry_k (오늘 33.66 * 0.25 = 8.415pt)
    현행 breakout=0.2 → 하단이 청산가 -1.68pt에 불과해, 조금만 더 밀리면
    '추가 돌파'로 인정돼 통과한다. 13:09 SHORT는 -6.75pt(≈0.8*unit)에서
    재진입해 -25.87pt를 냈다.

    breakout 계수를 넓히면 이런 재진입을 막지만, 진짜 추세 추종 진입까지
    막을 수 있어 백테스트로 확인한다.
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

BREAKOUTS = [0.2, 0.4, 0.6, 0.8, 1.0, 1.5]


def run(df, bo, pb=0.5):
    return run_chandelier_live_replica(
        df.copy(), **BASE, **COST,
        reentry_breakout_mult=bo, reentry_pullback_mult=pb)


def row(label, r, base=None):
    if r is None:
        print(f"  {label:24s} 거래 없음"); return None
    d = f"{r['final_capital']-base['final_capital']:>+16,.0f}" if base else f"{'(기준)':>16s}"
    print(f"  {label:24s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | "
          f"익{r['avg_win_pt']:+6.2f}/손{r['avg_loss_pt']:+6.2f} | {d}")
    return r


def section(title, df):
    print(f"\n{'='*146}")
    print(f"[{title}] {df.index[0].date()} ~ {df.index[-1].date()}")
    print('='*146)
    base = None
    for bo in BREAKOUTS:
        r = run(df, bo)
        lab = f"breakout {bo}" + (" (현행)" if bo == 0.2 else "")
        if base is None:
            base = row(lab, r)
        else:
            row(lab, r, base)
    return base


def main():
    df = load_futures_data('10100000')
    print(f"[데이터] {len(df)}봉 | 비용: 차등 슬리피지 + 수수료 | 15시 컷오프 적용")
    print(f"[밴드] SHORT 차단 = [청산가 - unit*breakout, 청산가 + unit*0.5], unit = prev_range * 0.25")

    section("전체기간", df)
    last = pd.to_datetime(df['date_day'].iloc[-1], format='%Y%m%d')
    for days, name in ((60, "최근 60일"), (120, "최근 120일")):
        cut = (last - pd.Timedelta(days=days)).strftime('%Y%m%d')
        sub = df[df['date_day'] >= cut].copy()
        if not sub.empty:
            section(name, sub)

    print(f"\n{'='*146}")
    print("[분기별] 현행(breakout 0.2) 대비 자본 차이 — 특정 국면 과최적화 점검")
    print('='*146)
    bounds = [("2025-Q1","20250101","20250401"),("2025-Q2","20250401","20250701"),
              ("2025-Q3","20250701","20251001"),("2025-Q4","20251001","20260101"),
              ("2026-Q1","20260101","20260401"),("2026-Q2","20260401","20260701"),
              ("2026-Q3","20260701","99999999")]
    cands = [0.4, 0.6, 0.8, 1.0]
    print("  분기        현행PF" + "".join(f"{f'bo {c}':>20s}" for c in cands))
    tot = {c: 0 for c in cands}; worse = {c: 0 for c in cands}
    for q, s, e in bounds:
        sub = df[(df['date_day'] >= s) & (df['date_day'] < e)].copy()
        if len(sub) < 200:
            print(f"  {q:10s} 데이터 부족"); continue
        b = run(sub, 0.2)
        if not b:
            continue
        cells = ""
        for c in cands:
            r = run(sub, c)
            dv = r['final_capital'] - b['final_capital'] if r else 0
            tot[c] += dv
            if dv < 0:
                worse[c] += 1
            cells += f"{dv:>+20,.0f}"
        print(f"  {q:10s} {b['pf']:>7.2f}" + cells)
    print()
    for c in cands:
        print(f"  breakout {c}: 악화 분기 {worse[c]}/7 | 분기합 {tot[c]:+,.0f}원")


if __name__ == '__main__':
    main()
