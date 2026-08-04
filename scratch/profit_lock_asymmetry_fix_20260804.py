"""손익 비대칭 해결안 탐색 — 2026-08-04.

문제:
    2026-08-04 실매매 9건에서 평균익절 +3.42 / 평균손절 -15.99pt로 백테스트 기대치
    (+16.18 / -3.06)와 정반대가 됐다. 로그를 MFE별로 나누면 원인이 분명하다.

      MFE < 4pt  (3건) : -26.4 ~ -27.6pt  ← 보호 없음, 트레일링 폭 그대로
      MFE 4~9pt  (4건) : -0.14 ~ +0.98pt  ← 본전 바닥에 걸려 사실상 0
      MFE > 9pt  (2건) : +12.78pt 등

    핵심 결함: 이익보전의 '타이트' 폭이 발동 기준보다 넓다.
      trigger = 8.0pt (고정)
      tight   = profit_lock_mult(0.10) x ATR14(95.5) = 9.55pt
    폭이 트리거보다 크므로 MFE 8~9pt에서 발동해도 되돌림이 진입가를 뚫고,
    본전 바닥에 걸려 +1pt 남짓으로 끝난다. ATR이 클수록 심해지는 구조다.

검증 대상:
    A. profit_lock_mult 하향 — 타이트 폭이 트리거보다 확실히 작아지게
    B. be_stage_buffer 상향 — 본전이 아니라 소액이익을 잠금
    C. session_range_cap 하향 — 손실측(MFE<4 구간)을 줄임
    D. 조합
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
    session_range_cap_min_bars=6,
    min_std_error_entry=1.5,
    trim_std_outliers=1, trend_bar_minutes=15, consecutive_loss_limit=5,
    dynamic_sizing=True, max_contracts=15,
    regime_filter_enabled=True, profit_lock_enabled=True,
    profit_lock_be_buffer_pt=1.0,
    profit_lock_trigger_pt=8.0,
    entry_end_hour=15, entry_end_minute=0,
)
COST = dict(slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0)

CUR = dict(session_range_cap_mult=0.5, profit_lock_mult=0.10,
           profit_lock_be_move_trigger_pt=4.0, profit_lock_be_stage_buffer_pt=0.0)

VARIANTS = [
    ("현행 (기준)",                    dict()),
    ("A. lock_mult 0.05",             dict(profit_lock_mult=0.05)),
    ("A. lock_mult 0.03",             dict(profit_lock_mult=0.03)),
    ("B. be_buffer 1.0pt",            dict(profit_lock_be_stage_buffer_pt=1.0)),
    ("B. be_buffer 2.0pt",            dict(profit_lock_be_stage_buffer_pt=2.0)),
    ("C. cap 0.35",                   dict(session_range_cap_mult=0.35)),
    ("D. lock0.05 + cap0.35",         dict(profit_lock_mult=0.05, session_range_cap_mult=0.35)),
    ("D. lock0.05 + be1.0",           dict(profit_lock_mult=0.05, profit_lock_be_stage_buffer_pt=1.0)),
    ("D. lock0.05+be1.0+cap0.35",     dict(profit_lock_mult=0.05, profit_lock_be_stage_buffer_pt=1.0,
                                           session_range_cap_mult=0.35)),
]


def run(df, over):
    kw = dict(BASE, **COST, **CUR)
    kw.update(over)
    return run_chandelier_live_replica(df.copy(), **kw)


def row(label, r, base=None):
    if r is None:
        print(f"  {label:28s} 거래 없음"); return None
    ratio = abs(r['avg_loss_pt']) / r['avg_win_pt'] if r['avg_win_pt'] else 0
    d = f"{r['final_capital']-base['final_capital']:>+16,.0f}" if base else f"{'(기준)':>16s}"
    print(f"  {label:28s} 거래{r['trades']:>5d} | 승률{r['win_rate']:6.2f}% | PF{r['pf']:7.2f} | "
          f"MDD{r['mdd']:6.2f}% | 익{r['avg_win_pt']:+6.2f}/손{r['avg_loss_pt']:+6.2f} "
          f"(손/익 {ratio:4.2f}) | {d}")
    return r


def section(title, df):
    print(f"\n{'='*152}")
    print(f"[{title}] {df.index[0].date()} ~ {df.index[-1].date()}")
    print('='*152)
    base = None
    for label, over in VARIANTS:
        r = run(df, over)
        if base is None:
            base = row(label, r)
        else:
            row(label, r, base)
    return base


def main():
    df = load_futures_data('10100000')
    print(f"[데이터] {len(df)}봉 | 비용: 차등 슬리피지 + 수수료 0.0065% | 15시 컷오프 적용")
    print("[지표] '손/익'은 평균손절÷평균익절 — 1보다 작을수록 비대칭이 유리한 방향")

    section("전체기간", df)

    last = pd.to_datetime(df['date_day'].iloc[-1], format='%Y%m%d')
    for days, name in ((60, "최근 60일"), (120, "최근 120일")):
        cut = (last - pd.Timedelta(days=days)).strftime('%Y%m%d')
        sub = df[df['date_day'] >= cut].copy()
        if not sub.empty:
            section(name, sub)


if __name__ == '__main__':
    main()
