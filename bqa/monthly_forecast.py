# -*- coding: utf-8 -*-
"""
1개월 모의투자 수익률 예측
===========================
최근 22거래일(약 1개월) 실데이터로
KOSPI200 선물 + ISF(삼성전자/SK하이닉스) 동시 운용 시
예상 성과를 시뮬레이션합니다.

주식 전략은 RSA 필터 특성상 별도 확률 모델로 추정.
"""
import sys, io, sqlite3, os, json
from datetime import datetime, timedelta
import datetime as _dt
import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_FUTURES = os.path.join(workspace_root, "futures_data.db")
DB_UNIFIED = os.path.join(workspace_root, "unified_data.db")
LOCAL_CFG  = os.path.join(workspace_root, "config", "config_local.json")
ACTIVE     = os.path.join(workspace_root, "config", "active_strategy.json")

CAPITAL_FUTURES = 31_000_000   # 선물 계좌 (현재 상태)
CAPITAL_STOCK   = 6_880_516    # 주식 계좌 (system_status.json 기준)


# ─────────────────────────────────────────────────────────
# 파라미터 로드
# ─────────────────────────────────────────────────────────
with open(ACTIVE, encoding='utf-8') as f:
    strat = json.load(f)
K_KOSPI  = strat['best_k']        # 0.1
SL_KOSPI = strat['stop_loss_pt']  # 3.0
TP_KOSPI = strat['take_profit_pt']# 6.0

with open(LOCAL_CFG, encoding='utf-8') as f:
    lcfg = json.load(f)
isf_cfgs = {i['stock_code']: i for i in lcfg.get('individual_stock_futures', [])}
SS = isf_cfgs.get('005930', {'best_k':0.35,'stop_loss_pct':2.0,'take_profit_pct':2.5,'nsaa_long_min':72})
SK = isf_cfgs.get('000660', {'best_k':0.18,'stop_loss_pct':1.2,'take_profit_pct':2.0,'nsaa_long_min':80})


# ─────────────────────────────────────────────────────────
# 최근 22거래일 = 약 1개월
# ─────────────────────────────────────────────────────────
def load_recent_kospi(n_days=22):
    conn = sqlite3.connect(DB_FUTURES)
    df = pd.read_sql(
        "SELECT date,open,high,low,close FROM futures_ohlcv WHERE code='10500000' ORDER BY date",
        conn)
    conn.close()
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('dt', inplace=True)
    df['date_only'] = df.index.date
    days = sorted(set(df['date_only']))
    recent = days[-n_days-5:]   # 버퍼 포함
    return df[df['date_only'].isin(recent)], days[-n_days:]

def load_recent_isf(code, n_days=22):
    conn = sqlite3.connect(DB_UNIFIED)
    df = pd.read_sql(
        f"SELECT date,open,high,low,close FROM isf_5min_ohlcv WHERE code='{code}' ORDER BY date",
        conn)
    conn.close()
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('dt', inplace=True)
    df['date_only'] = df.index.date
    days = sorted(set(df['date_only']))
    recent = days[-n_days-5:]
    return df[df['date_only'].isin(recent)], days[-n_days:]


# ─────────────────────────────────────────────────────────
# KOSPI200 선물 1개월 시뮬
# ─────────────────────────────────────────────────────────
def sim_kospi200(n_days=22):
    df_full, trade_days = load_recent_kospi(n_days)
    daily_groups = {d: g for d, g in df_full.groupby('date_only')}
    all_days = sorted(daily_groups.keys())

    MULT = 50_000
    MARGIN_RATE = 0.10
    cap = CAPITAL_FUTURES
    trades = []

    for trade_date in trade_days:
        idx = all_days.index(trade_date)
        if idx == 0: continue
        prev_date = all_days[idx-1]
        if prev_date not in daily_groups or trade_date not in daily_groups:
            continue
        prev_df = daily_groups[prev_date]
        day_df  = daily_groups[trade_date].sort_index()

        prev_range = float(prev_df['high'].max() - prev_df['low'].min())
        if prev_range <= 0: continue

        morning = day_df[day_df.index.time >= _dt.time(9, 5)]
        if morning.empty: continue
        day_open = float(morning.iloc[0]['open'])
        tl = day_open + prev_range * K_KOSPI
        ts = day_open - prev_range * K_KOSPI

        # 계약 수: 초기 자본 고정 기준 (복리 폭발 방지)
        margin_per = day_open * MULT * MARGIN_RATE
        qty = max(1, int(CAPITAL_FUTURES * 0.3 / margin_per)) if margin_per > 0 else 1

        in_pos = False
        entry = sl = tp = dir_ = 0
        traded_today = False          # 1일 1거래 제한

        for ts_bar, bar in day_df.iterrows():
            t = ts_bar.time()
            if t >= _dt.time(15, 15):
                if in_pos:
                    pnl = (bar['open'] - entry) * dir_ * MULT * qty
                    cap += pnl
                    trades.append({'date':trade_date,'type':'FC','pnl':pnl,'dir':'L' if dir_==1 else 'S'})
                break
            if not in_pos and not traded_today:
                if t < _dt.time(9, 5): continue
                if bar['high'] >= tl:
                    entry = tl; sl = entry - SL_KOSPI; tp = entry + TP_KOSPI
                    dir_ = 1; in_pos = True; traded_today = True
                elif bar['low'] <= ts:
                    entry = ts; sl = entry + SL_KOSPI; tp = entry - TP_KOSPI
                    dir_ = -1; in_pos = True; traded_today = True
            elif in_pos:
                if dir_ == 1:
                    if bar['low'] <= sl:
                        pnl=(sl-entry)*MULT*qty; cap+=pnl; trades.append({'date':trade_date,'type':'SL','pnl':pnl,'dir':'L'}); in_pos=False
                    elif bar['high'] >= tp:
                        pnl=(tp-entry)*MULT*qty; cap+=pnl; trades.append({'date':trade_date,'type':'TP','pnl':pnl,'dir':'L'}); in_pos=False
                else:
                    if bar['high'] >= sl:
                        pnl=(entry-sl)*MULT*qty; cap+=pnl; trades.append({'date':trade_date,'type':'SL','pnl':pnl,'dir':'S'}); in_pos=False
                    elif bar['low'] <= tp:
                        pnl=(entry-tp)*MULT*qty; cap+=pnl; trades.append({'date':trade_date,'type':'TP','pnl':pnl,'dir':'S'}); in_pos=False

    return trades, cap


# ─────────────────────────────────────────────────────────
# ISF 1개월 시뮬 (1일 1거래 제한)
# ─────────────────────────────────────────────────────────
def sim_isf(code, cfg, long_thresh, n_days=22):
    df_full, trade_days = load_recent_isf(code, n_days)
    daily_groups = {d: g for d, g in df_full.groupby('date_only')}
    all_days = sorted(daily_groups.keys())

    K   = cfg['best_k']
    SL  = cfg['stop_loss_pct']
    TP  = cfg['take_profit_pct']
    CONTRACT = 10
    MARGIN_RATE = 0.15
    cap = CAPITAL_FUTURES
    trades = []

    for trade_date in trade_days:
        idx = all_days.index(trade_date)
        if idx < 6: continue
        prev_date = all_days[idx-1]
        if prev_date not in daily_groups or trade_date not in daily_groups: continue

        # 5일 모멘텀으로 NSAA 방향 판단
        past_closes = []
        for past_d in all_days[:idx+1][-7:]:
            g = daily_groups.get(past_d)
            if g is not None:
                past_closes.append(float(g['close'].iloc[-1]))
        if len(past_closes) < 6: continue
        ret5 = (past_closes[-1] - past_closes[-6]) / past_closes[-6]
        if ret5 < long_thresh: continue

        prev_df = daily_groups[prev_date]
        day_df  = daily_groups[trade_date].sort_index()
        prev_range = float(prev_df['high'].max() - prev_df['low'].min())
        if prev_range <= 0: continue

        morning = day_df[day_df.index.time >= _dt.time(9, 5)]
        if morning.empty: continue
        day_open = float(morning.iloc[0]['open'])
        target = day_open + prev_range * K

        in_pos = False
        entry = sl_p = tp_p = 0.0
        traded_today = False

        for ts_bar, bar in day_df.iterrows():
            t = ts_bar.time()
            if t >= _dt.time(15, 15):
                if in_pos:
                    pnl = (bar['open']-entry)*CONTRACT; cap+=pnl
                    trades.append({'date':trade_date,'type':'FC','pnl':pnl})
                    in_pos = False
                break
            if not in_pos and not traded_today:
                if t < _dt.time(9, 5): continue
                if bar['high'] >= target:
                    entry = target
                    sl_p = entry*(1-SL/100); tp_p = entry*(1+TP/100)
                    in_pos = True; traded_today = True
            elif in_pos:
                if bar['low'] <= sl_p:
                    pnl=(sl_p-entry)*CONTRACT; cap+=pnl; trades.append({'date':trade_date,'type':'SL','pnl':pnl}); in_pos=False
                elif bar['high'] >= tp_p:
                    pnl=(tp_p-entry)*CONTRACT; cap+=pnl; trades.append({'date':trade_date,'type':'TP','pnl':pnl}); in_pos=False

    return trades, cap


# ─────────────────────────────────────────────────────────
# 주식 전략 확률 모델 (테마 종목 실데이터 없으므로 확률 추정)
# ─────────────────────────────────────────────────────────
def estimate_stock_strategy():
    """
    주식 단타/스윙 전략을 확률 모델로 추정:
    - 월 평균 단타 진입: 6~10회 (테마 조건 + RSA 70점+)
    - 단타 승률: 55~65% (RSA 필터 적용)
    - 단타 평균 수익: +2.5% / 평균 손실: -2.0%
    - 스윙 진입: 2~4회 / 월
    - 스윙 승률: 50~60%
    - 스윙 평균 수익: +6% / 평균 손실: -3%
    """
    budget_day   = CAPITAL_STOCK * 0.60
    budget_swing = CAPITAL_STOCK * 0.40
    budget_per_day_stock  = budget_day   / 5   # 최대 5종목
    budget_per_swing_stock= budget_swing / 3   # 최대 3종목

    scenarios = {
        '보수적': {'day_n':6, 'day_wr':0.55, 'day_win':0.025, 'day_loss':-0.020,
                    'sw_n':2, 'sw_wr':0.50, 'sw_win':0.060, 'sw_loss':-0.030},
        '기본':   {'day_n':8, 'day_wr':0.60, 'day_win':0.025, 'day_loss':-0.020,
                    'sw_n':3, 'sw_wr':0.55, 'sw_win':0.060, 'sw_loss':-0.030},
        '낙관':   {'day_n':10,'day_wr':0.65, 'day_win':0.030, 'day_loss':-0.020,
                    'sw_n':4, 'sw_wr':0.60, 'sw_win':0.080, 'sw_loss':-0.025},
    }

    results = {}
    for label, s in scenarios.items():
        day_pnl = s['day_n'] * (s['day_wr']*s['day_win'] + (1-s['day_wr'])*s['day_loss']) * budget_per_day_stock
        sw_pnl  = s['sw_n']  * (s['sw_wr'] *s['sw_win']  + (1-s['sw_wr']) *s['sw_loss']) * budget_per_swing_stock
        total   = day_pnl + sw_pnl
        ret     = total / CAPITAL_STOCK * 100
        results[label] = {'day_pnl':int(day_pnl), 'sw_pnl':int(sw_pnl),
                          'total':int(total), 'ret':round(ret,2)}
    return results


# ─────────────────────────────────────────────────────────
# 결과 분석 함수
# ─────────────────────────────────────────────────────────
def analyze(trades, cap, initial, name):
    if not trades:
        return {'name':name,'n':0,'wr':0,'ret':0,'mdd':0,'final':cap}
    wins = sum(1 for t in trades if t['pnl']>0)
    total_pnl = sum(t['pnl'] for t in trades)
    arr = np.array([initial]+[initial+sum(t['pnl'] for t in trades[:i+1]) for i in range(len(trades))])
    peak = np.maximum.accumulate(arr)
    mdd = float(np.min((arr-peak)/peak)*100)
    sl_n = sum(1 for t in trades if t['type']=='SL')
    tp_n = sum(1 for t in trades if t['type']=='TP')
    fc_n = sum(1 for t in trades if t['type']=='FC')
    return {
        'name':name, 'n':len(trades), 'wins':wins,
        'wr':round(wins/len(trades)*100,1),
        'total_pnl':int(total_pnl),
        'ret':round(total_pnl/initial*100,2),
        'mdd':round(mdd,2), 'final':int(cap),
        'sl':sl_n,'tp':tp_n,'fc':fc_n,
        'avg_pnl':round(total_pnl/len(trades))
    }


# ─────────────────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    N_DAYS = 22  # 1개월 = 약 22거래일
    print('=== 1개월 모의투자 수익률 예측 시뮬레이션 ===')
    print(f'시뮬레이션 기간: 최근 {N_DAYS}거래일 (약 1개월)\n')

    print('[1] KOSPI200 미니선물 시뮬...')
    k_trades, k_cap = sim_kospi200(N_DAYS)
    k_res = analyze(k_trades, k_cap, CAPITAL_FUTURES, 'KOSPI200 미니선물')

    print('[2] 삼성전자 ISF 시뮬...')
    ss_trades, ss_cap = sim_isf('005930', SS, long_thresh=0.01, n_days=N_DAYS)
    ss_res = analyze(ss_trades, ss_cap, CAPITAL_FUTURES, '삼성전자 ISF')

    print('[3] SK하이닉스 ISF 시뮬...')
    sk_trades, sk_cap = sim_isf('000660', SK, long_thresh=0.03, n_days=N_DAYS)
    sk_res = analyze(sk_trades, sk_cap, CAPITAL_FUTURES, 'SK하이닉스 ISF')

    print('[4] 주식 전략 확률 모델 추정...')
    stock_est = estimate_stock_strategy()

    # ── 출력 ──────────────────────────────────────────────
    print('\n' + '='*65)
    print('[ 선물 계좌 시뮬레이션 결과 (최근 22거래일) ]')
    print('='*65)
    for r in [k_res, ss_res, sk_res]:
        if r['n'] == 0:
            print(f"\n  {r['name']}: 해당 기간 거래 없음")
            continue
        print(f"\n  [{r['name']}]")
        print(f"  거래수: {r['n']}회  승률: {r['wr']}%  수익률: {r['ret']:+.2f}%")
        print(f"  총손익: {r['total_pnl']:+,}원  MDD: {r['mdd']:.2f}%")
        print(f"  청산유형: 손절 {r['sl']}회 / 익절 {r['tp']}회 / 시간청산 {r['fc']}회")

    # 선물 계좌 합산 (KOSPI200 + 두 ISF가 같은 계좌 사용)
    futures_total_pnl = k_res['total_pnl'] + ss_res['total_pnl'] + sk_res['total_pnl']
    futures_ret = futures_total_pnl / CAPITAL_FUTURES * 100
    print(f'\n  [선물 계좌 합산]')
    print(f'  총손익: {futures_total_pnl:+,}원  수익률: {futures_ret:+.2f}%')
    print(f'  시작 자본: {CAPITAL_FUTURES:,}원 → 예상 최종: {CAPITAL_FUTURES+futures_total_pnl:,}원')

    print('\n' + '='*65)
    print('[ 주식 계좌 확률 모델 추정 ]')
    print(f'  시작 자본: {CAPITAL_STOCK:,}원')
    print('='*65)
    for label, s in stock_est.items():
        print(f"\n  [{label} 시나리오]")
        print(f"  단타 손익: {s['day_pnl']:+,}원  스윙 손익: {s['sw_pnl']:+,}원")
        print(f"  합계: {s['total']:+,}원  수익률: {s['ret']:+.2f}%")

    # 전체 포트폴리오 합산
    print('\n' + '='*65)
    print('[ 전체 포트폴리오 예상 수익률 (기본 시나리오) ]')
    print('='*65)
    stock_base = stock_est['기본']['total']
    total_assets = CAPITAL_FUTURES + CAPITAL_STOCK
    total_pnl = futures_total_pnl + stock_base
    total_ret = total_pnl / total_assets * 100

    print(f"\n  총 운용 자산: {total_assets:,}원")
    print(f"  선물 손익:   {futures_total_pnl:+,}원 ({futures_ret:+.2f}%)")
    print(f"  주식 손익:   {stock_base:+,}원 ({stock_est['기본']['ret']:+.2f}%)")
    print(f"  ---")
    print(f"  합산 손익:   {total_pnl:+,}원")
    print(f"  합산 수익률: {total_ret:+.2f}%")
    print(f"  예상 최종 자산: {total_assets+total_pnl:,}원")

    # 시나리오별 요약
    print('\n' + '='*65)
    print('[ 시나리오별 1개월 수익률 요약 ]')
    print('='*65)
    for label, s in stock_est.items():
        sp = s['total']
        combined = futures_total_pnl + sp
        r = combined / total_assets * 100
        print(f"  {label:>5} 시나리오: {r:+.2f}% ({combined:+,}원)")

    print(f'\n  * 선물 시뮬은 최근 22거래일 실데이터 기반')
    print(f'  * 주식 시뮬은 RSA 70점+ 필터 통과 확률 모델')
    print(f'  * 현재 SK하이닉스 강세장 지속 가정 (리스크 주의)')
