# -*- coding: utf-8 -*-
"""
AMATS 전체 시스템 백테스트
- KOSPI200 미니선물 (ERA K=0.1 전략)
- 삼성전자 개별주식선물 (ISF K=0.35, RSA 방향 필터)
- SK하이닉스 개별주식선물 (ISF K=0.40, RSA 방향 필터)
- 삼성전자 / SK하이닉스 Buy&Hold 비교
"""
import sqlite3
import os
import sys
import io
import requests
import json
from datetime import datetime
from bs4 import BeautifulSoup

import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_PATH = os.path.join(workspace_root, "futures_data.db")


# ────────────────────────────────────────────────────────────────
# 헬퍼: MDD 계산
# ────────────────────────────────────────────────────────────────
def calc_mdd(capital_series):
    arr = np.array(capital_series)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / peak
    return float(np.min(dd) * 100)


# ────────────────────────────────────────────────────────────────
# 1. KOSPI200 미니선물 백테스트
#    현행 ERA 전략: K=0.1 / 손절 2pt / 익절 5pt / 미니선물 승수 50000
# ────────────────────────────────────────────────────────────────
def backtest_kospi200(K=0.1, SL_PT=2.0, TP_PT=5.0, CAPITAL=31_000_000):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    df = pd.read_sql(
        "SELECT date,open,high,low,close FROM futures_ohlcv WHERE code='10500000' ORDER BY date",
        conn
    )
    conn.close()

    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['date'], inplace=True)
    df.set_index('date', inplace=True)

    # 일봉 집계
    daily = df.resample('D').agg({'open': 'first', 'high': 'max',
                                   'low': 'min', 'close': 'last'}).dropna()
    daily = daily[daily.index.weekday < 5]

    MULTIPLIER = 50_000
    MARGIN_RATE = 0.10
    cap = CAPITAL
    caps = [CAPITAL]
    trades, wins = [], 0

    for i in range(1, len(daily)):
        prev = daily.iloc[i - 1]
        cur  = daily.iloc[i]
        prev_range = prev['high'] - prev['low']
        if prev_range <= 0:
            continue
        open_p = cur['open']
        target_long  = open_p + prev_range * K
        target_short = open_p - prev_range * K

        margin_per = open_p * MULTIPLIER * MARGIN_RATE
        qty = max(1, int(cap * 0.3 / margin_per)) if margin_per > 0 else 1

        pnl = None
        # LONG 시뮬
        if cur['high'] >= target_long:
            entry = target_long
            if cur['low'] <= entry - SL_PT:
                pnl = -SL_PT * MULTIPLIER * qty
            elif cur['high'] >= entry + TP_PT:
                pnl = TP_PT * MULTIPLIER * qty
            else:
                pnl = (cur['close'] - entry) * MULTIPLIER * qty
        # SHORT 시뮬
        elif cur['low'] <= target_short:
            entry = target_short
            if cur['high'] >= entry + SL_PT:
                pnl = -SL_PT * MULTIPLIER * qty
            elif cur['low'] <= entry - TP_PT:
                pnl = TP_PT * MULTIPLIER * qty
            else:
                pnl = (entry - cur['close']) * MULTIPLIER * qty

        if pnl is not None:
            cap += pnl
            trades.append(pnl)
            if pnl > 0:
                wins += 1
            caps.append(cap)

    if not trades:
        return None

    total_ret = (cap - CAPITAL) / CAPITAL * 100
    win_rate  = wins / len(trades) * 100
    days = (daily.index[-1] - daily.index[0]).days
    cagr = ((cap / CAPITAL) ** (365 / max(days, 1)) - 1) * 100
    mdd  = calc_mdd([CAPITAL] + [CAPITAL + sum(trades[:i+1]) for i in range(len(trades))])
    avg_pnl = sum(trades) / len(trades)

    return {
        'name':          f'KOSPI200 미니선물 (K={K}, SL={SL_PT}pt, TP={TP_PT}pt)',
        'period':        f'{daily.index[0].date()} ~ {daily.index[-1].date()}',
        'start_capital': CAPITAL,
        'final_capital': int(cap),
        'total_return':  round(total_ret, 1),
        'cagr':          round(cagr, 1),
        'mdd':           round(mdd, 1),
        'trades':        len(trades),
        'win_rate':      round(win_rate, 1),
        'avg_pnl':       round(avg_pnl),
    }


# ────────────────────────────────────────────────────────────────
# 2. 개별주식선물 (ISF) 백테스트
#    RSA 방향 필터를 5일 모멘텀으로 시뮬 (±2% 기준)
# ────────────────────────────────────────────────────────────────
def get_naver_daily(code, pages=30):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    rows = []
    for page in range(1, pages + 1):
        url = f'https://finance.naver.com/item/sise_day.naver?code={code}&page={page}'
        try:
            r = requests.get(url, headers=headers, timeout=7)
            soup = BeautifulSoup(r.content, 'html.parser')
            for tr in soup.select('table.type2 tr'):
                tds = tr.select('td')
                if len(tds) < 7:
                    continue
                dstr = tds[0].text.strip()
                if not dstr or '.' not in dstr:
                    continue
                try:
                    rows.append({
                        'date':  datetime.strptime(dstr, '%Y.%m.%d'),
                        'open':  int(tds[3].text.strip().replace(',', '')),
                        'high':  int(tds[4].text.strip().replace(',', '')),
                        'low':   int(tds[5].text.strip().replace(',', '')),
                        'close': int(tds[1].text.strip().replace(',', '')),
                    })
                except Exception:
                    pass
        except Exception:
            pass

    df = pd.DataFrame(sorted(rows, key=lambda x: x['date']))
    if df.empty:
        return df
    df.set_index('date', inplace=True)
    return df


def backtest_isf(stock_code, name, K, SL_PCT, TP_PCT,
                 CONTRACT_SIZE=10, CAPITAL=31_000_000):
    df = get_naver_daily(stock_code)
    if len(df) < 20:
        return None

    MARGIN_RATE = 0.15   # 개별주식선물 위탁증거금률
    cap = CAPITAL
    trades, wins = [], 0

    # 5일 모멘텀으로 RSA NSAA 방향 대리변수
    df['ret5'] = df['close'].pct_change(5)
    LONG_THRESH  =  0.02   # +2% 이상 → NSAA 호재 → LONG 허용
    SHORT_THRESH = -0.02   # -2% 이하 → NSAA 악재 → SHORT 허용

    neutral_days = 0

    for i in range(6, len(df)):
        prev = df.iloc[i - 1]
        cur  = df.iloc[i]

        ret5 = prev['ret5']
        if pd.isna(ret5):
            continue
        if ret5 >= LONG_THRESH:
            direction = 'LONG'
        elif ret5 <= SHORT_THRESH:
            direction = 'SHORT'
        else:
            direction = 'NEUTRAL'
            neutral_days += 1
            continue

        prev_range = prev['high'] - prev['low']
        if prev_range <= 0:
            continue
        open_p = cur['open']
        target_long  = open_p + prev_range * K
        target_short = open_p - prev_range * K

        margin_per = open_p * CONTRACT_SIZE * MARGIN_RATE
        qty = 1

        pnl = None
        if direction == 'LONG' and cur['high'] >= target_long:
            entry = target_long
            sl = entry * (1 - SL_PCT / 100)
            tp = entry * (1 + TP_PCT / 100)
            if cur['low'] <= sl:
                pnl = (sl - entry) * CONTRACT_SIZE * qty
            elif cur['high'] >= tp:
                pnl = (tp - entry) * CONTRACT_SIZE * qty
            else:
                pnl = (cur['close'] - entry) * CONTRACT_SIZE * qty

        elif direction == 'SHORT' and cur['low'] <= target_short:
            entry = target_short
            sl = entry * (1 + SL_PCT / 100)
            tp = entry * (1 - TP_PCT / 100)
            if cur['high'] >= sl:
                pnl = (entry - sl) * CONTRACT_SIZE * qty
            elif cur['low'] <= tp:
                pnl = (entry - tp) * CONTRACT_SIZE * qty
            else:
                pnl = (entry - cur['close']) * CONTRACT_SIZE * qty

        if pnl is not None:
            cap += pnl
            trades.append(pnl)
            if pnl > 0:
                wins += 1

    if not trades:
        return None

    total_ret = (cap - CAPITAL) / CAPITAL * 100
    win_rate  = wins / len(trades) * 100
    days = (df.index[-1] - df.index[0]).days
    cagr = ((cap / CAPITAL) ** (365 / max(days, 1)) - 1) * 100
    mdd  = calc_mdd([CAPITAL] + [CAPITAL + sum(trades[:i+1]) for i in range(len(trades))])
    avg_pnl = sum(trades) / len(trades)
    total_days = len(df) - 6
    active_rate = (total_days - neutral_days) / total_days * 100 if total_days else 0

    return {
        'name':          f'{name} ISF (K={K}, SL={SL_PCT}%, TP={TP_PCT}%)',
        'period':        f'{df.index[0].date()} ~ {df.index[-1].date()}',
        'start_capital': CAPITAL,
        'final_capital': int(cap),
        'total_return':  round(total_ret, 1),
        'cagr':          round(cagr, 1),
        'mdd':           round(mdd, 1),
        'trades':        len(trades),
        'win_rate':      round(win_rate, 1),
        'avg_pnl':       round(avg_pnl),
        'rsa_active_rate': round(active_rate, 1),
        'neutral_days':  neutral_days,
    }


# ────────────────────────────────────────────────────────────────
# 3. 주식 Buy & Hold 비교
# ────────────────────────────────────────────────────────────────
def backtest_stock_bnh(stock_code, name, CAPITAL=10_000_000):
    df = get_naver_daily(stock_code)
    if len(df) < 2:
        return None
    start_price = df['close'].iloc[0]
    end_price   = df['close'].iloc[-1]
    shares = CAPITAL // start_price
    final_cap = shares * end_price + (CAPITAL - shares * start_price)
    total_ret = (final_cap - CAPITAL) / CAPITAL * 100
    days = (df.index[-1] - df.index[0]).days
    cagr = ((final_cap / CAPITAL) ** (365 / max(days, 1)) - 1) * 100

    # 일별 MDD
    caps = [(CAPITAL - shares * start_price) + shares * p for p in df['close']]
    mdd = calc_mdd(caps)

    return {
        'name':          f'{name} Buy&Hold (주식 현물)',
        'period':        f'{df.index[0].date()} ~ {df.index[-1].date()}',
        'start_price':   f'{start_price:,}',
        'end_price':     f'{end_price:,}',
        'start_capital': CAPITAL,
        'final_capital': int(final_cap),
        'total_return':  round(total_ret, 1),
        'cagr':          round(cagr, 1),
        'mdd':           round(mdd, 1),
        'shares':        int(shares),
    }


# ────────────────────────────────────────────────────────────────
# 실행
# ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=== AMATS 전체 시스템 백테스트 실행 ===\n')

    print('[1/5] KOSPI200 미니선물 백테스트...')
    r1 = backtest_kospi200(K=0.1, SL_PT=2.0, TP_PT=5.0)

    print('[2/5] 삼성전자 ISF 백테스트...')
    r2 = backtest_isf('005930', '삼성전자', K=0.35, SL_PCT=1.5, TP_PCT=4.0)

    print('[3/5] SK하이닉스 ISF 백테스트...')
    r3 = backtest_isf('000660', 'SK하이닉스', K=0.40, SL_PCT=1.5, TP_PCT=4.0)

    print('[4/5] 삼성전자 Buy&Hold...')
    r4 = backtest_stock_bnh('005930', '삼성전자')

    print('[5/5] SK하이닉스 Buy&Hold...')
    r5 = backtest_stock_bnh('000660', 'SK하이닉스')

    print('\n' + '='*60)
    print('백테스트 결과')
    print('='*60)
    for r in [r1, r2, r3, r4, r5]:
        if r is None:
            print('  데이터 부족')
            continue
        print(f"\n[{r['name']}]")
        print(f"  기간: {r['period']}")
        print(f"  시작자본: {r['start_capital']:,}원")
        print(f"  최종자본: {r['final_capital']:,}원")
        print(f"  총수익률: {r['total_return']}%")
        print(f"  CAGR:    {r['cagr']}%")
        print(f"  MDD:     {r['mdd']}%")
        if 'trades' in r:
            print(f"  거래수:  {r['trades']}회")
            print(f"  승률:    {r['win_rate']}%")
        if 'avg_pnl' in r:
            print(f"  평균손익: {r['avg_pnl']:,}원")
        if 'rsa_active_rate' in r:
            print(f"  RSA 진입 허용률: {r['rsa_active_rate']}% (중립 제외)")
