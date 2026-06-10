# -*- coding: utf-8 -*-
"""
ISF K값 5분봉 정밀 최적화
==========================
NSAA 방향 필터(Long-Only) 고정 상태에서
K × SL × TP 격자 탐색으로 최적 파라미터 도출
"""
import sys, io, sqlite3, os, json
from datetime import datetime, timedelta
import datetime as _dt
import pandas as pd
import numpy as np
from itertools import product

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(workspace_root, "unified_data.db")
LOCAL_CFG = os.path.join(workspace_root, "config", "config_local.json")
ACTIVE_STRAT = os.path.join(workspace_root, "config", "active_strategy.json")


def load_5min(code):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    df = pd.read_sql(f"SELECT date,open,high,low,close FROM isf_5min_ohlcv WHERE code='{code}' ORDER BY date", conn)
    conn.close()
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('dt', inplace=True)
    df['date_only'] = df.index.date
    return df


def calc_metrics(trades, capital):
    if not trades: return None
    pnls = [t['pnl'] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    arr = np.array([capital + sum(pnls[:i]) for i in range(len(pnls)+1)])
    peak = np.maximum.accumulate(arr)
    mdd = float(np.min((arr - peak) / peak) * 100)
    ret = total / capital * 100
    rr = ret / max(abs(mdd), 0.01)
    return {
        'n': len(trades), 'wins': wins,
        'wr': round(wins/len(trades)*100, 1),
        'ret': round(ret, 2), 'mdd': round(mdd, 2),
        'rr': round(rr, 3),
        'avg': round(total/len(trades)),
        'sl_n': sum(1 for t in trades if t['type']=='SL'),
        'tp_n': sum(1 for t in trades if t['type']=='TP'),
    }


def run_backtest(df, K, SL_PCT, TP_PCT, LONG_THRESH,
                 CONTRACT=10, CAPITAL=31_000_000):
    """5분봉 K값 변동성 돌파 + NSAA Long-Only 방향 필터"""
    daily_groups = list(df.groupby('date_only'))
    daily_close  = df.groupby('date_only')['close'].last()

    trades = []
    for idx, (trade_date, day_df) in enumerate(daily_groups):
        if idx < 6: continue
        day_df = day_df.sort_index()

        # 5일 모멘텀 NSAA 대리변수
        past = [daily_close[d] for d in daily_close.index if d <= daily_groups[idx-1][0]]
        if len(past) < 6: continue
        ret5 = (past[-1] - past[-6]) / past[-6]
        if ret5 < LONG_THRESH: continue

        # 전일 고저폭
        prev_df = daily_groups[idx-1][1]
        prev_range = float(prev_df['high'].max() - prev_df['low'].min())
        if prev_range <= 0: continue

        # 시초가
        morning = day_df[day_df.index.time >= _dt.time(9, 5)]
        if morning.empty: continue
        day_open = float(morning.iloc[0]['open'])
        target   = day_open + prev_range * K

        in_pos = False
        entry_p = sl_p = tp_p = 0.0

        for ts, bar in day_df.iterrows():
            t = ts.time()
            if t >= _dt.time(15, 15):
                if in_pos:
                    pnl = (bar['open'] - entry_p) * CONTRACT
                    trades.append({'type':'FC','pnl':pnl,'date':trade_date})
                    in_pos = False
                break
            if not in_pos:
                if t < _dt.time(9, 5): continue
                if bar['high'] >= target:
                    entry_p = target
                    sl_p = entry_p * (1 - SL_PCT/100)
                    tp_p = entry_p * (1 + TP_PCT/100)
                    in_pos = True
            else:
                if bar['low'] <= sl_p:
                    trades.append({'type':'SL','pnl':(sl_p-entry_p)*CONTRACT,'date':trade_date})
                    in_pos = False
                elif bar['high'] >= tp_p:
                    trades.append({'type':'TP','pnl':(tp_p-entry_p)*CONTRACT,'date':trade_date})
                    in_pos = False
    return trades


def optimize(code, name, LONG_THRESH, CAPITAL=31_000_000):
    print(f'\n{"="*60}')
    print(f'[ {name} K값 최적화 ]')
    print(f'{"="*60}')

    df = load_5min(code)
    if df.empty:
        print('5분봉 데이터 없음'); return None

    K_LIST   = [0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
    SL_LIST  = [0.8, 1.0, 1.2, 1.5, 2.0, 2.5]
    TP_LIST  = [2.0, 2.5, 3.0, 4.0, 5.0, 6.0]

    results = []
    total = len(K_LIST) * len(SL_LIST) * len(TP_LIST)
    done  = 0

    for K, SL, TP in product(K_LIST, SL_LIST, TP_LIST):
        if TP <= SL * 1.2:
            done += 1; continue
        trades = run_backtest(df, K, SL, TP, LONG_THRESH)
        m = calc_metrics(trades, CAPITAL)
        if m and m['n'] >= 5:
            m.update({'K': K, 'SL': SL, 'TP': TP})
            results.append(m)
        done += 1

    if not results:
        print('결과 없음'); return None

    df_r = pd.DataFrame(results)

    print(f'\n▶ Return/MDD 비율 Top 15:')
    top = df_r.sort_values('rr', ascending=False).head(15)
    print(f"{'K':>5} {'SL%':>5} {'TP%':>5} {'거래':>5} {'승률':>6} {'수익률':>8} {'MDD':>7} {'R/MDD':>7} {'SL건':>5} {'TP건':>5}")
    for _, r in top.iterrows():
        print(f"{r['K']:>5.2f} {r['SL']:>5.1f} {r['TP']:>5.1f} "
              f"{r['n']:>5} {r['wr']:>5.1f}% {r['ret']:>7.2f}% "
              f"{r['mdd']:>6.2f}% {r['rr']:>7.3f} {r['sl_n']:>5} {r['tp_n']:>5}")

    print(f'\n▶ 총수익률 Top 5:')
    top_ret = df_r.sort_values('ret', ascending=False).head(5)
    for _, r in top_ret.iterrows():
        print(f"  K={r['K']:.2f} SL={r['SL']}% TP={r['TP']}% "
              f"수익률={r['ret']:.2f}% MDD={r['mdd']:.2f}% 승률={r['wr']:.1f}% R/MDD={r['rr']:.3f}")

    # K값별 최고 성과 요약
    print(f'\n▶ K값별 최고 R/MDD (SL/TP 최적 조합):')
    for k_val in K_LIST:
        sub = df_r[df_r['K'] == k_val]
        if sub.empty: continue
        best = sub.sort_values('rr', ascending=False).iloc[0]
        mark = ' ◀ BEST' if best['rr'] == df_r['rr'].max() else ''
        print(f"  K={k_val:.2f}: SL={best['SL']}% TP={best['TP']}% "
              f"수익={best['ret']:.2f}% MDD={best['mdd']:.2f}% R/MDD={best['rr']:.3f}{mark}")

    best_overall = df_r.sort_values('rr', ascending=False).iloc[0]
    return best_overall


def apply_and_save(ss_best, sk_best):
    """최적 파라미터를 config_local.json에 저장"""
    with open(LOCAL_CFG, encoding='utf-8') as f:
        lcfg = json.load(f)

    mapping = {'005930': ss_best, '000660': sk_best}
    for item in lcfg.get('individual_stock_futures', []):
        sc = item['stock_code']
        if sc in mapping and mapping[sc] is not None:
            best = mapping[sc]
            item['best_k']         = float(best['K'])
            item['stop_loss_pct']  = float(best['SL'])
            item['take_profit_pct']= float(best['TP'])

    with open(LOCAL_CFG, 'w', encoding='utf-8') as f:
        json.dump(lcfg, f, ensure_ascii=False, indent=4)

    print(f'\n✅ config_local.json 업데이트 완료')
    for sc, best in mapping.items():
        if best is not None:
            nm = '삼성전자' if sc=='005930' else 'SK하이닉스'
            print(f'  {nm}: K={best["K"]:.2f}  SL={best["SL"]}%  TP={best["TP"]}%')


if __name__ == '__main__':
    print('=== ISF 5분봉 K값 최적화 ===\n')

    ss_best = optimize('005930', '삼성전자',  LONG_THRESH=0.01)
    sk_best = optimize('000660', 'SK하이닉스', LONG_THRESH=0.03)

    print('\n' + '='*60)
    print('[ 최종 최적 파라미터 요약 ]')
    print('='*60)
    for nm, best in [('삼성전자', ss_best), ('SK하이닉스', sk_best)]:
        if best is not None:
            print(f'\n  {nm}:')
            print(f'    K={best["K"]:.2f}  SL={best["SL"]}%  TP={best["TP"]}%')
            print(f'    수익률={best["ret"]:.2f}%  MDD={best["mdd"]:.2f}%  '
                  f'승률={best["wr"]:.1f}%  R/MDD={best["rr"]:.3f}')
            print(f'    거래수={best["n"]}회  손절={best["sl_n"]}  익절={best["tp_n"]}')

    apply_and_save(ss_best, sk_best)
