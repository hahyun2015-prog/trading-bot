# -*- coding: utf-8 -*-
"""
ISF 5분봉 정밀 백테스트
======================
Yahoo Finance에서 삼성전자/SK하이닉스 5분봉 데이터를 수집하여
일봉 시뮬레이션의 한계(SL/TP 순서 불명)를 해소하고
실제 장중 진행을 정확히 재현합니다.

전략 (백테스트 최적화 v2 반영):
  - K=0.15, Long-Only
  - RSA 방향: 전일 종가 대비 5일 수익률 기준 LONG 판별
  - 삼성전자: NSAA임계=72 (LT≥+1%)
  - SK하이닉스: NSAA임계=80 (LT≥+3%)
  - 손절 -1.5%, 익절 +4.0%
  - 09:05 시초가 확정 → 목표가 계산 → 당일 내 진입
"""
import sys
import io
import json
import sqlite3
import os
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict

import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_PATH = os.path.join(workspace_root, "unified_data.db")


# ────────────────────────────────────────────────────────────────
# Yahoo Finance 5분봉 수집 + DB 저장
# ────────────────────────────────────────────────────────────────
def fetch_and_store_5min(code_kr, yf_symbol, table='isf_5min_ohlcv', range_='60d'):
    """Yahoo Finance에서 5분봉 수집 → unified_data.db에 저장"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
               'Accept': 'application/json'}
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}?interval=5m&range={range_}'

    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quotes = result['indicators']['quote'][0]
        opens   = quotes.get('open',  [None]*len(timestamps))
        highs   = quotes.get('high',  [None]*len(timestamps))
        lows    = quotes.get('low',   [None]*len(timestamps))
        closes  = quotes.get('close', [None]*len(timestamps))
        volumes = quotes.get('volume',[None]*len(timestamps))
    except Exception as e:
        print(f'[수집 오류] {yf_symbol}: {e}')
        return pd.DataFrame()

    rows = []
    for i, ts in enumerate(timestamps):
        if closes[i] is None or opens[i] is None:
            continue
        dt = datetime.fromtimestamp(ts)
        # 09:00~15:30 장중만 유지
        if not (9 <= dt.hour < 15 or (dt.hour == 15 and dt.minute <= 30)):
            continue
        rows.append({
            'code': code_kr,
            'date': dt.strftime('%Y%m%d%H%M%S'),
            'open':   round(opens[i]),
            'high':   round(highs[i]),
            'low':    round(lows[i]),
            'close':  round(closes[i]),
            'volume': int(volumes[i]) if volumes[i] else 0
        })

    if not rows:
        return pd.DataFrame()

    # DB 저장
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    c = conn.cursor()
    c.execute(f'''CREATE TABLE IF NOT EXISTS {table}
                 (code TEXT, date TEXT, open REAL, high REAL, low REAL,
                  close REAL, volume INTEGER, UNIQUE(code, date))''')
    c.executemany(
        f'INSERT OR REPLACE INTO {table} (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)',
        [(r['code'],r['date'],r['open'],r['high'],r['low'],r['close'],r['volume']) for r in rows]
    )
    conn.commit()
    conn.close()

    df = pd.DataFrame(rows)
    df['datetime'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('datetime', inplace=True)
    print(f'  {code_kr}: {len(df)}개 5분봉 수집/저장 완료 '
          f'({df.index[0].date()} ~ {df.index[-1].date()})')
    return df


# ────────────────────────────────────────────────────────────────
# 5분봉 ISF 백테스트
# ────────────────────────────────────────────────────────────────
def backtest_isf_5min(df, name, K, SL_PCT, TP_PCT,
                      LONG_THRESH, CONTRACT_SIZE=10,
                      CAPITAL=31_000_000):
    """
    5분봉 데이터로 ISF K값 변동성 돌파 전략 시뮬
    - 09:00~09:04 : 전일 데이터로 prev_range 계산
    - 09:05 첫 봉 : 당일 시초가(open) 확정
    - 09:05 이후  : 장중 가격이 목표가 돌파하면 즉시 진입
    - 손절/익절   : 분봉 단위로 실시간 체크 (일봉보다 정확)
    - 15:15 강제청산 (당일 미실현 포지션)
    """
    MARGIN_RATE = 0.15
    cap = CAPITAL
    trades_log = []

    # 일별로 그룹핑
    df['date_only'] = df.index.date
    daily_groups = [(d, g) for d, g in df.groupby('date_only')]

    # 5일 수익률로 RSA LONG 방향 판별
    # 전일 일봉 종가 시리즈 만들기
    daily_close = df.groupby('date_only')['close'].last()
    daily_close_series = daily_close

    for idx, (trade_date, day_df) in enumerate(daily_groups):
        day_df = day_df.sort_index()

        # 전일 고저폭 계산
        if idx == 0:
            continue
        prev_date, prev_df = daily_groups[idx - 1]
        prev_range = float(prev_df['high'].max() - prev_df['low'].min())
        if prev_range <= 0:
            continue

        # 5일 수익률 RSA 방향 판별
        past_closes = [daily_close_series[d] for d in daily_close_series.index
                       if d <= prev_date]
        if len(past_closes) < 6:
            continue
        ret5 = (past_closes[-1] - past_closes[-6]) / past_closes[-6]
        if ret5 < LONG_THRESH:
            continue   # LONG 조건 미충족 → 오늘 거래 없음

        # 09:05 이후 첫 봉 시초가 (당일 시초가)
        morning_bars = day_df[day_df.index.time >= __import__('datetime').time(9, 5)]
        if morning_bars.empty:
            continue
        day_open = float(morning_bars.iloc[0]['open'])
        target_long = day_open + prev_range * K

        in_position = False
        entry_price = 0.0
        sl_price = 0.0
        tp_price = 0.0
        qty = 1

        for dt, bar in day_df.iterrows():
            bar_time = dt.time()
            import datetime as _dt

            # 15:15 강제청산
            if bar_time >= _dt.time(15, 15) and in_position:
                pnl = (bar['open'] - entry_price) * CONTRACT_SIZE * qty
                cap += pnl
                trades_log.append({
                    'date': trade_date, 'type': 'FORCE_CLOSE',
                    'entry': entry_price, 'exit': bar['open'],
                    'pnl': pnl, 'ret5': ret5
                })
                in_position = False
                break

            # 포지션 없음: 진입 감시
            if not in_position:
                if bar_time < _dt.time(9, 5):
                    continue
                # LONG 진입 (K값 돌파)
                if bar['high'] >= target_long:
                    entry_price = target_long
                    sl_price = entry_price * (1 - SL_PCT / 100)
                    tp_price = entry_price * (1 + TP_PCT / 100)
                    margin_per = entry_price * CONTRACT_SIZE * MARGIN_RATE
                    qty = 1
                    in_position = True

            # 포지션 보유: 손절/익절 감시
            else:
                if bar['low'] <= sl_price:
                    exit_p = sl_price
                    pnl = (exit_p - entry_price) * CONTRACT_SIZE * qty
                    cap += pnl
                    trades_log.append({
                        'date': trade_date, 'type': 'SL',
                        'entry': entry_price, 'exit': exit_p,
                        'pnl': pnl, 'ret5': ret5
                    })
                    in_position = False
                elif bar['high'] >= tp_price:
                    exit_p = tp_price
                    pnl = (exit_p - entry_price) * CONTRACT_SIZE * qty
                    cap += pnl
                    trades_log.append({
                        'date': trade_date, 'type': 'TP',
                        'entry': entry_price, 'exit': exit_p,
                        'pnl': pnl, 'ret5': ret5
                    })
                    in_position = False

    if not trades_log:
        return None

    df_t = pd.DataFrame(trades_log)
    wins = (df_t['pnl'] > 0).sum()
    total_pnl = df_t['pnl'].sum()
    total_ret = total_pnl / CAPITAL * 100

    # MDD
    cap_series = [CAPITAL]
    running = CAPITAL
    for p in df_t['pnl']:
        running += p
        cap_series.append(running)
    arr = np.array(cap_series)
    peak = np.maximum.accumulate(arr)
    mdd = float(np.min((arr - peak) / peak) * 100)

    # 유형별 분석
    type_stats = df_t.groupby('type')['pnl'].agg(['count','sum','mean'])

    return {
        'name': name,
        'trades': len(df_t),
        'wins': int(wins),
        'win_rate': round(wins / len(df_t) * 100, 1),
        'total_pnl': int(total_pnl),
        'total_ret': round(total_ret, 2),
        'mdd': round(mdd, 2),
        'final_capital': int(cap),
        'type_stats': type_stats.to_dict(),
        'avg_pnl': round(total_pnl / len(df_t))
    }


# ────────────────────────────────────────────────────────────────
# 실행
# ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=== ISF 5분봉 정밀 백테스트 ===\n')

    # 1. 데이터 수집 + DB 저장
    print('[데이터 수집] Yahoo Finance 60일 5분봉...')
    df_ss = fetch_and_store_5min('005930', '005930.KS')
    df_sk = fetch_and_store_5min('000660', '000660.KS')

    if df_ss.empty or df_sk.empty:
        print('데이터 수집 실패')
        sys.exit(1)

    print()

    # 2. 삼성전자 백테스트 (최적화 v2: K=0.15, LT=+1%)
    print('[삼성전자] K=0.15, SL=1.5%, TP=4.0%, LT=+1% (Long-Only)...')
    r_ss = backtest_isf_5min(df_ss, '삼성전자', K=0.15, SL_PCT=1.5, TP_PCT=4.0,
                              LONG_THRESH=0.01)
    print('[삼성전자] 비교: K=0.35 이전 파라미터...')
    r_ss_old = backtest_isf_5min(df_ss, '삼성전자(구)', K=0.35, SL_PCT=1.5, TP_PCT=4.0,
                                  LONG_THRESH=0.02)

    # 3. SK하이닉스 백테스트 (최적화 v2: K=0.15, LT=+3%)
    print('[SK하이닉스] K=0.15, SL=1.5%, TP=4.0%, LT=+3% (Long-Only)...')
    r_sk = backtest_isf_5min(df_sk, 'SK하이닉스', K=0.15, SL_PCT=1.5, TP_PCT=4.0,
                              LONG_THRESH=0.03)
    print('[SK하이닉스] 비교: K=0.40 이전 파라미터...')
    r_sk_old = backtest_isf_5min(df_sk, 'SK하이닉스(구)', K=0.40, SL_PCT=1.5, TP_PCT=4.0,
                                  LONG_THRESH=0.02)

    # 4. 결과 출력
    print('\n' + '='*65)
    print('5분봉 정밀 백테스트 결과 (최적화 전/후 비교)')
    print('='*65)

    for r in [r_ss_old, r_ss, r_sk_old, r_sk]:
        if r is None:
            print('  결과 없음 (데이터 부족)')
            continue
        ts = r['type_stats']
        sl_cnt = int(ts['count'].get('SL', 0))
        tp_cnt = int(ts['count'].get('TP', 0))
        fc_cnt = int(ts['count'].get('FORCE_CLOSE', 0))
        print(f"\n[{r['name']}]")
        print(f"  거래수: {r['trades']}회  승률: {r['win_rate']}%  평균손익: {r['avg_pnl']:,}원")
        print(f"  총손익: {r['total_pnl']:+,}원  수익률: {r['total_ret']:+.2f}%  MDD: {r['mdd']:.2f}%")
        print(f"  청산유형: 손절={sl_cnt}회, 익절={tp_cnt}회, 시간청산={fc_cnt}회")
        print(f"  최종자본: {r['final_capital']:,}원 (시작: 31,000,000원)")
