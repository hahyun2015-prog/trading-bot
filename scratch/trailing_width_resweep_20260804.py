"""트레일링 폭(chandelier_mult / session_range_cap_mult) 재스윕 — 2026-08-04.

배경:
    2026-08-04 실매매에서 큰 손실 3건이 전부 -26~-27pt로 균일했다. 우연이 아니라
    트레일링 폭 그 자체다(당일 ATR14=95.93 → dist = min(0.3*95.93, 60) = 28.8pt).
    반면 이익은 세션레인지캡·이익보전에 걸려 일찍 잘려, 평균익절 +3.42pt /
    평균손절 -15.99pt로 백테스트 기대치(+15.91 / -3.00)와 정반대가 됐다.

전제 변화:
    과거 스윕에서 mult 0.15/0.20을 "절대 금지"로 뒀던 근거는 2026-06-11 거래정지 갭
    사고(-114.74pt)였다. 그런데 그 거래는 15:05 진입이고, 2026-08-03부터 15:00 진입
    컷오프가 적용돼 애초에 체결되지 않는다. 즉 금지 근거가 사라졌으므로 재검증한다.

주의:
    slip_* 를 생략하면 차등 슬리피지가 아니라 slip_fee_pt(0.05)로 떨어져 비용이
    사실상 0이 된다(2026-07-31 실수). 반드시 명시 전달한다.
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
    chandelier_hard_cap=60.0,
    session_range_cap_min_bars=6,
    min_std_error_entry=1.5,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True,
    profit_lock_enabled=True,
    profit_lock_trigger_pt=8.0, profit_lock_mult=0.10,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0,
    entry_end_hour=15, entry_end_minute=0,          # 현재 라이브: 15시 컷오프 적용
)
COST = dict(slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0)

CUR_MULT, CUR_CAP = 0.30, 0.5


def run(df, mult, cap):
    return run_chandelier_live_replica(
        df.copy(), **BASE, **COST, chandelier_mult=mult, session_range_cap_mult=cap)


def row(label, r, base=None):
    if r is None:
        print(f"  {label:22s} 거래 없음"); return
    d = ""
    if base:
        d = f" | 자본차 {r['final_capital']-base['final_capital']:+15,.0f}"
    print(f"  {label:22s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 최악{r['worst_loss_pt']:+8.2f}pt | "
          f"익{r['avg_win_pt']:+6.2f}/손{r['avg_loss_pt']:+6.2f}pt | 자본{r['final_capital']:>15,.0f}{d}")


def section(title, df):
    print(f"\n{'='*150}")
    print(f"[{title}] {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)}봉)")
    print('='*150)
    base = run(df, CUR_MULT, CUR_CAP)
    row(f"현행 m{CUR_MULT} c{CUR_CAP}", base)
    print("  " + "-"*146)
    for m in (0.20, 0.25, 0.35):
        row(f"mult {m} (cap {CUR_CAP})", run(df, m, CUR_CAP), base)
    print("  " + "-"*146)
    for c in (0.30, 0.35, 0.40):
        row(f"cap {c} (mult {CUR_MULT})", run(df, CUR_MULT, c), base)
    print("  " + "-"*146)
    for m, c in ((0.25, 0.40), (0.25, 0.35), (0.20, 0.40)):
        row(f"mult {m} + cap {c}", run(df, m, c), base)
    return base


def main():
    df = load_futures_data('10100000')
    print(f"[데이터] {len(df)}봉 | {df.index[0]} ~ {df.index[-1]}")
    print("[비용] 차등 슬리피지(1.5/3.0/0.5/2.0pt) + 수수료 0.0065% | 15시 진입 컷오프 적용")

    section("전체기간", df)

    last = pd.to_datetime(df['date_day'].iloc[-1], format='%Y%m%d')
    for days, name in ((60, "최근 60일"), (120, "최근 120일")):
        cut = (last - pd.Timedelta(days=days)).strftime('%Y%m%d')
        sub = df[df['date_day'] >= cut].copy()
        if not sub.empty:
            section(name, sub)

    # 분기별 — 특정 국면 과최적화 방지
    print(f"\n{'='*150}")
    print("[분기별] 현행(m0.3/c0.5) 대비 자본 차이")
    print('='*150)
    bounds = [("2025-Q1","20250101","20250401"),("2025-Q2","20250401","20250701"),
              ("2025-Q3","20250701","20251001"),("2025-Q4","20251001","20260101"),
              ("2026-Q1","20260101","20260401"),("2026-Q2","20260401","20260701"),
              ("2026-Q3","20260701","99999999")]
    cands = [(0.25, 0.5), (0.20, 0.5), (0.30, 0.35), (0.25, 0.40)]
    print(f"  {'분기':10s} {'현행 PF':>9s}" + "".join(f"{f'm{m}/c{c}':>20s}" for m, c in cands))
    for q, s, e in bounds:
        sub = df[(df['date_day'] >= s) & (df['date_day'] < e)].copy()
        if len(sub) < 200:
            print(f"  {q:10s} 데이터 부족"); continue
        b = run(sub, CUR_MULT, CUR_CAP)
        if not b:
            print(f"  {q:10s} 거래 없음"); continue
        cells = ""
        for m, c in cands:
            r = run(sub, m, c)
            cells += f"{(r['final_capital']-b['final_capital']):>+20,.0f}" if r else f"{'거래없음':>20s}"
        print(f"  {q:10s} {b['pf']:>9.2f}" + cells)


if __name__ == '__main__':
    main()
