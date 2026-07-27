# -*- coding: utf-8 -*-
"""chandelier_mult=0.25 최근 1주일 백테스트 (사용자 요청, 2026-07-27).

거래 건수가 적은 구간이므로 집계 통계뿐 아니라 거래별 상세(진입/청산 시각·가격·손익)를
mult=0.25와 현행 0.30 나란히 출력해 직접 대조할 수 있게 한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

PROD_KW = dict(
    Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
    margin_cap=0.30, reentry_k=0.25,
    point_value=50_000,  # 미니선물(A0568000, futures_prefix='105') 실제 승수 — 표준선물 25만원 아님
    enable_reentry_filter=True,
    chandelier_hard_cap=60.0,
    min_std_error_entry=0.9,
    trim_std_outliers=1,
    trend_bar_minutes=15,
    consecutive_loss_limit=5,
    return_trades=True,
)


def fmt(res):
    if res is None:
        return "거래 없음"
    return (f"거래{res['trades']:>3d}건 | 승률{res['win_rate']:6.2f}% | PF{res['pf']:7.2f} | "
            f"수익률{res['profit_pct']:+9.2f}% | MDD{res['mdd']:6.2f}% | 최악{res['worst_loss_pt']:+7.2f}pt")


def print_trades(res, label):
    print(f"\n  [{label}] 거래 상세:")
    if res is None or not res.get('trade_log'):
        print("    (거래 없음)")
        return
    for t in res['trade_log']:
        force = " [강제청산]" if t['is_force'] else ""
        print(f"    {t['entry_time'].strftime('%m/%d %H:%M')} {t['direction']:<5} 진입:{t['entry_price']:8.2f} "
              f"-> {t['exit_time'].strftime('%m/%d %H:%M')} 청산:{t['exit_price']:8.2f}  "
              f"손익:{t['pnl_pt']:+7.2f}pt ({t['gain_krw']:+13,.0f}원, {t['contracts']}계약){force}")


def main():
    # (2026-07-27) 범용 표준선물 코드 10100000엔 오늘 09:00 이후 장중 데이터가 없어
    # (실서버 수집이 그 시각에 멈춰있음) 오늘 실거래 4건이 백테스트에서 누락된다.
    # 실제 라이브가 거래 중인 미니선물 코드로 대체해 오늘 세션까지 정확히 포함시킨다.
    df = load_futures_data('A0568000')
    if df.empty:
        print("[-] 데이터 없음"); return

    last = df.index.max()
    seg = df[df.index >= last - pd.Timedelta(days=7)]
    print(f"{'='*100}\n[최근 1주일]  캔들수={len(seg)}  기간={seg.index.min()} ~ {seg.index.max()}\n{'='*100}")

    for m in (0.30, 0.25):
        kw = dict(PROD_KW); kw['chandelier_mult'] = m
        res = run_chandelier_live_replica(seg.copy(), **kw)
        tag = "  <= 현행" if m == 0.30 else "  <= 검토안"
        print(f"\nmult={m}{tag}   {fmt(res)}")
        print_trades(res, f"mult={m}")

    # 14일도 참고용으로 함께 (표본 너무 작아서 1주일만으로 결론내기 위험함을 보여주기 위함)
    seg14 = df[df.index >= last - pd.Timedelta(days=14)]
    print(f"\n\n{'='*100}\n[참고: 최근 2주일]  캔들수={len(seg14)}  기간={seg14.index.min()} ~ {seg14.index.max()}\n{'='*100}")
    for m in (0.30, 0.25):
        kw = dict(PROD_KW); kw['chandelier_mult'] = m
        res = run_chandelier_live_replica(seg14.copy(), **kw)
        tag = "  <= 현행" if m == 0.30 else "  <= 검토안"
        print(f"mult={m}{tag}   {fmt(res)}")


if __name__ == '__main__':
    main()
