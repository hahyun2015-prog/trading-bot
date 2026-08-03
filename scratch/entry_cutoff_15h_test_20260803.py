"""15시 이후 신규 진입 차단(entry cutoff) 효과 검증 — 2026-08-03 최종 라이브 설정 기준.

배경:
    장마감 무조건청산(15:35~15:45)이 도입된 뒤로는 늦은 진입일수록 트레일링이 작동할
    시간 자체가 없어 강제청산으로 끝날 확률이 높다. 게다가 2026-08-03에는 15:42 청산
    주문이 미확인으로 끝나 포지션이 오버나잇으로 넘어가는 사고까지 났다. 늦은 진입을
    아예 막으면 이 구간의 위험 노출 자체가 사라진다.

주의:
    slip_* 인자를 하나도 넘기지 않으면 run_chandelier_live_replica가 차등 슬리피지 대신
    slip_fee_pt(0.05pt)로 떨어져 거래비용이 사실상 0이 된다(2026-07-31 실수). 여기서는
    반드시 명시적으로 전달한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from bqa.kalman_backtester import load_futures_data, run_chandelier_live_replica

# 2026-08-03 현재 config.json(futures_settings) 그대로
LIVE = dict(
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
)
# 현실적 차등 슬리피지 + 위탁수수료(기본 0.0065%)
COST = dict(slip_entry_pt=1.5, slip_exit_sl_pt=3.0,
            slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0)

CUTOFFS = [
    ("컷오프 없음(현행)", None, 0),
    ("15:00 이후 차단", 15, 0),
    ("14:30 이후 차단", 14, 30),
    ("15:30 이후 차단", 15, 30),
]


def row(label, r):
    if r is None:
        print(f"  {label:20s}  거래 없음")
        return None
    print(f"  {label:20s}  거래 {r['trades']:>5d} | 승률 {r['win_rate']:6.2f}% | PF {r['pf']:7.2f} | "
          f"MDD {r['mdd']:6.2f}% | 최악손실 {r['worst_loss_pt']:+8.2f}pt | 최종자본 {r['final_capital']:>16,.0f}원")
    return r


def section(title, df):
    print(f"\n{'='*118}")
    print(f"[{title}]  {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)}봉)")
    print('='*118)
    out = {}
    for label, h, m in CUTOFFS:
        kw = dict(LIVE, **COST)
        if h is not None:
            kw['entry_end_hour'] = h
            kw['entry_end_minute'] = m
        out[label] = row(label, run_chandelier_live_replica(df.copy(), **kw))
    return out


def main():
    df = load_futures_data('10100000')
    print(f"[데이터] {len(df)}봉 | {df.index[0]} ~ {df.index[-1]}")

    full = section("전체기간", df)

    last_dt = pd.to_datetime(df['date_day'].iloc[-1], format='%Y%m%d')
    for days, name in [(60, "최근 60일"), (120, "최근 120일")]:
        cut = (last_dt - pd.Timedelta(days=days)).strftime('%Y%m%d')
        sub = df[df['date_day'] >= cut].copy()
        if not sub.empty:
            section(name, sub)

    # 분기별 — 특정 국면에서만 좋은 건 아닌지 교차확인
    print(f"\n{'='*118}")
    print("[분기별] 컷오프 없음 vs 15:00 차단")
    print('='*118)
    bounds = [("2025-Q1","20250101","20250401"),("2025-Q2","20250401","20250701"),
              ("2025-Q3","20250701","20251001"),("2025-Q4","20251001","20260101"),
              ("2026-Q1","20260101","20260401"),("2026-Q2","20260401","20260701"),
              ("2026-Q3","20260701","99999999")]
    for q, s, e in bounds:
        sub = df[(df['date_day'] >= s) & (df['date_day'] < e)].copy()
        if len(sub) < 200:
            print(f"  {q}: 데이터 부족"); continue
        a = run_chandelier_live_replica(sub.copy(), **dict(LIVE, **COST))
        b = run_chandelier_live_replica(sub.copy(), **dict(LIVE, **COST, entry_end_hour=15, entry_end_minute=0))
        if a and b:
            d = b['final_capital'] - a['final_capital']
            print(f"  {q}: 현행 거래{a['trades']:>4d}/PF{a['pf']:6.2f}/MDD{a['mdd']:5.2f}%  →  "
                  f"15시차단 거래{b['trades']:>4d}/PF{b['pf']:6.2f}/MDD{b['mdd']:5.2f}%  | 자본차 {d:+,.0f}원")
        else:
            print(f"  {q}: 한쪽 거래 없음 (현행={'있음' if a else '없음'}, 15시차단={'있음' if b else '없음'})")

    # 15시 이후 진입이 실제로 얼마나 되고 성적이 어떤지 직접 집계
    print(f"\n{'='*118}")
    print("[15시 이후 진입 건들만 따로 집계] (현행 설정, 전체기간)")
    print('='*118)
    r = run_chandelier_live_replica(df.copy(), **dict(LIVE, **COST), return_trades=True)
    late = [t for t in r['trade_log'] if t['entry_time'].hour >= 15]
    allt = r['trade_log']
    if late:
        wins = [t for t in late if t['gain_krw'] > 0]
        print(f"  전체 거래 {len(allt)}건 중 15시 이후 진입 {len(late)}건 ({len(late)/len(allt)*100:.1f}%)")
        print(f"  15시 이후 진입 손익합 : {sum(t['gain_krw'] for t in late):>+16,.0f}원")
        print(f"  승률                  : {len(wins)/len(late)*100:.1f}%")
        print(f"  강제청산 비율         : {sum(1 for t in late if t['is_force'])/len(late)*100:.1f}%")
        print(f"  (참고) 전체 강제청산  : {sum(1 for t in allt if t['is_force'])/len(allt)*100:.1f}%")
    else:
        print("  15시 이후 진입 없음")


if __name__ == '__main__':
    main()
