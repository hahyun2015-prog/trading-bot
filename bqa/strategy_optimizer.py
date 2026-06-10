# -*- coding: utf-8 -*-
"""
AMATS 전략 최적화 연구
======================
백테스트 결과를 기반으로 3가지 관점에서 최적화 탐색:
  1. KOSPI200: K값 × SL × TP × 방향(양방향/롱온리/숏온리) 격자 탐색
  2. ISF(삼성/SK): K값 × RSA임계값 × 방향성 최적화
  3. 시장 레짐 감지: 추세장 vs 횡보장 자동 분류 후 전략 전환
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
from itertools import product

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_PATH = os.path.join(workspace_root, "futures_data.db")


# ────────────────────────────────────────────────────────────────
# 공통 유틸
# ────────────────────────────────────────────────────────────────
def calc_metrics(trades, initial):
    if not trades:
        return None
    cap_series = [initial]
    for t in trades:
        cap_series.append(cap_series[-1] + t)
    final = cap_series[-1]
    arr = np.array(cap_series)
    peak = np.maximum.accumulate(arr)
    mdd = float(np.min((arr - peak) / peak) * 100)
    wins = sum(1 for t in trades if t > 0)
    win_rate = wins / len(trades) * 100
    total_ret = (final - initial) / initial * 100
    score = total_ret / max(abs(mdd), 1)   # Return/MDD 비율 (높을수록 좋음)
    return {
        'trades': len(trades), 'win_rate': round(win_rate, 1),
        'total_ret': round(total_ret, 1), 'mdd': round(mdd, 1),
        'final': int(final), 'score': round(score, 3),
        'avg_pnl': round(sum(trades) / len(trades))
    }


def load_kospi_daily():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    df = pd.read_sql(
        "SELECT date,open,high,low,close FROM futures_ohlcv WHERE code='10500000' ORDER BY date",
        conn
    )
    conn.close()
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['date'], inplace=True)
    df.set_index('date', inplace=True)
    daily = df.resample('D').agg({'open': 'first', 'high': 'max',
                                   'low': 'min', 'close': 'last'}).dropna()
    return daily[daily.index.weekday < 5]


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


# ════════════════════════════════════════════════════════════════
# 1. KOSPI200 격자 탐색 (K × SL × TP × 방향)
# ════════════════════════════════════════════════════════════════
def optimize_kospi200():
    print('\n' + '='*60)
    print('[ 1. KOSPI200 파라미터 격자 탐색 ]')
    print('='*60)

    daily = load_kospi_daily()
    MULTIPLIER = 50_000
    MARGIN_RATE = 0.10
    CAPITAL = 31_000_000

    K_RANGE  = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    SL_RANGE = [1.5, 2.0, 2.5, 3.0]
    TP_RANGE = [3.0, 4.0, 5.0, 6.0]
    DIR_LIST = ['both', 'long_only', 'short_only']

    results = []

    for K, SL_PT, TP_PT, direction in product(K_RANGE, SL_RANGE, TP_RANGE, DIR_LIST):
        if TP_PT <= SL_PT:
            continue

        cap = CAPITAL
        trades = []

        for i in range(1, len(daily)):
            prev = daily.iloc[i - 1]
            cur  = daily.iloc[i]
            prev_range = prev['high'] - prev['low']
            if prev_range <= 0:
                continue
            open_p = cur['open']
            tl = open_p + prev_range * K
            ts = open_p - prev_range * K

            margin_per = open_p * MULTIPLIER * MARGIN_RATE
            qty = max(1, int(cap * 0.3 / margin_per)) if margin_per > 0 else 1

            pnl = None
            if direction in ('both', 'long_only') and cur['high'] >= tl:
                entry = tl
                if cur['low'] <= entry - SL_PT:
                    pnl = -SL_PT * MULTIPLIER * qty
                elif cur['high'] >= entry + TP_PT:
                    pnl = TP_PT * MULTIPLIER * qty
                else:
                    pnl = (cur['close'] - entry) * MULTIPLIER * qty

            elif direction in ('both', 'short_only') and cur['low'] <= ts and pnl is None:
                entry = ts
                if cur['high'] >= entry + SL_PT:
                    pnl = -SL_PT * MULTIPLIER * qty
                elif cur['low'] <= entry - TP_PT:
                    pnl = TP_PT * MULTIPLIER * qty
                else:
                    pnl = (entry - cur['close']) * MULTIPLIER * qty

            if pnl is not None:
                cap += pnl
                trades.append(pnl)

        m = calc_metrics(trades, CAPITAL)
        if m and m['trades'] >= 20:
            m.update({'K': K, 'SL': SL_PT, 'TP': TP_PT, 'dir': direction})
            results.append(m)

    if not results:
        print('결과 없음')
        return None

    df_r = pd.DataFrame(results)

    # 상위 10개 (Return/MDD 비율 기준)
    top10 = df_r.sort_values('score', ascending=False).head(10)
    print('\n▶ Return/MDD 비율 상위 10개:')
    print(f"{'K':>5} {'SL':>5} {'TP':>5} {'방향':>10} {'수익률':>8} {'MDD':>8} {'승률':>7} {'Score':>8}")
    for _, row in top10.iterrows():
        print(f"{row['K']:>5.2f} {row['SL']:>5.1f} {row['TP']:>5.1f} {row['dir']:>10} "
              f"{row['total_ret']:>7.1f}% {row['mdd']:>7.1f}% {row['win_rate']:>6.1f}% {row['score']:>8.3f}")

    # 총수익률 상위 5개
    top5_ret = df_r.sort_values('total_ret', ascending=False).head(5)
    print('\n▶ 총수익률 상위 5개:')
    for _, row in top5_ret.iterrows():
        print(f"  K={row['K']:.2f} SL={row['SL']} TP={row['TP']} {row['dir']:>10} "
              f"수익률={row['total_ret']:.1f}% MDD={row['mdd']:.1f}% 승률={row['win_rate']:.1f}%")

    # 현행(K=0.1) 비교
    cur_params = df_r[(df_r['K'] == 0.10) & (df_r['SL'] == 2.0) & (df_r['TP'] == 5.0) & (df_r['dir'] == 'both')]
    if not cur_params.empty:
        row = cur_params.iloc[0]
        print(f'\n▶ 현행(K=0.1, SL=2pt, TP=5pt, both): 수익률={row["total_ret"]:.1f}% MDD={row["mdd"]:.1f}% Score={row["score"]:.3f}')

    best = df_r.sort_values('score', ascending=False).iloc[0]
    return best


# ════════════════════════════════════════════════════════════════
# 2. ISF(삼성/SK) 최적화 — K × RSA임계값 × 방향성
# ════════════════════════════════════════════════════════════════
def optimize_isf(stock_code, name, SL_PCT=1.5, TP_PCT=4.0, CONTRACT_SIZE=10):
    print(f'\n' + '='*60)
    print(f'[ 2. {name} ISF 최적화 ]')
    print('='*60)

    df = get_naver_daily(stock_code)
    if len(df) < 30:
        print('데이터 부족')
        return None

    df['ret5'] = df['close'].pct_change(5)
    df['ret3'] = df['close'].pct_change(3)
    df['ret1'] = df['close'].pct_change(1)

    CAPITAL = 31_000_000
    MARGIN_RATE = 0.15

    K_RANGE        = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
    LONG_THRESH_R  = [0.01, 0.02, 0.03]    # RSA LONG 진입 임계(5일수익률)
    SHORT_THRESH_R = [-0.01, -0.02, -0.03]  # RSA SHORT 진입 임계
    DIR_LIST       = ['both', 'long_only']

    results = []

    for K, LT, ST, direction in product(K_RANGE, LONG_THRESH_R, SHORT_THRESH_R, DIR_LIST):
        if LT <= abs(ST) * 0.5:
            continue

        cap = CAPITAL
        trades = []

        for i in range(6, len(df)):
            prev = df.iloc[i - 1]
            cur  = df.iloc[i]

            ret5 = prev['ret5']
            if pd.isna(ret5):
                continue

            # RSA 방향 결정
            if ret5 >= LT:
                direction_day = 'LONG'
            elif direction != 'long_only' and ret5 <= ST:
                direction_day = 'SHORT'
            else:
                continue

            prev_range = prev['high'] - prev['low']
            if prev_range <= 0:
                continue
            open_p = cur['open']
            tl = open_p + prev_range * K
            ts = open_p - prev_range * K

            pnl = None
            if direction_day == 'LONG' and cur['high'] >= tl:
                entry = tl
                sl = entry * (1 - SL_PCT / 100)
                tp = entry * (1 + TP_PCT / 100)
                if cur['low'] <= sl:
                    pnl = (sl - entry) * CONTRACT_SIZE
                elif cur['high'] >= tp:
                    pnl = (tp - entry) * CONTRACT_SIZE
                else:
                    pnl = (cur['close'] - entry) * CONTRACT_SIZE

            elif direction_day == 'SHORT' and cur['low'] <= ts:
                entry = ts
                sl = entry * (1 + SL_PCT / 100)
                tp = entry * (1 - TP_PCT / 100)
                if cur['high'] >= sl:
                    pnl = (entry - sl) * CONTRACT_SIZE
                elif cur['low'] <= tp:
                    pnl = (entry - tp) * CONTRACT_SIZE
                else:
                    pnl = (entry - cur['close']) * CONTRACT_SIZE

            if pnl is not None:
                cap += pnl
                trades.append(pnl)

        m = calc_metrics(trades, CAPITAL)
        if m and m['trades'] >= 10:
            m.update({'K': K, 'LT': LT, 'ST': ST, 'dir': direction})
            results.append(m)

    if not results:
        print('결과 없음')
        return None

    df_r = pd.DataFrame(results)
    top10 = df_r.sort_values('score', ascending=False).head(10)
    print(f'\n▶ {name} Return/MDD 비율 상위 10개:')
    print(f"{'K':>5} {'LT임계':>8} {'ST임계':>8} {'방향':>10} {'수익률':>8} {'MDD':>8} {'승률':>7} {'Score':>8}")
    for _, row in top10.iterrows():
        print(f"{row['K']:>5.2f} {row['LT']:>8.2f} {row['ST']:>8.2f} {row['dir']:>10} "
              f"{row['total_ret']:>7.1f}% {row['mdd']:>7.1f}% {row['win_rate']:>6.1f}% {row['score']:>8.3f}")

    best = df_r.sort_values('score', ascending=False).iloc[0]
    return best


# ════════════════════════════════════════════════════════════════
# 3. 시장 레짐 감지 전략 (추세장 vs 횡보장 분리)
# ════════════════════════════════════════════════════════════════
def analyze_regime_strategy():
    print('\n' + '='*60)
    print('[ 3. 시장 레짐 감지 전략 ]')
    print('='*60)

    daily = load_kospi_daily()
    MULTIPLIER = 50_000
    CAPITAL = 31_000_000

    # 레짐 분류: 20일 EMA 기울기 + ATR 비율
    daily['ema20'] = daily['close'].ewm(span=20).mean()
    daily['ema50'] = daily['close'].ewm(span=50).mean()
    daily['atr14'] = (daily['high'] - daily['low']).rolling(14).mean()
    daily['ema_slope'] = (daily['ema20'] - daily['ema20'].shift(5)) / daily['ema20'].shift(5) * 100
    daily['atr_ratio'] = daily['atr14'] / daily['close'] * 100  # ATR % of price
    daily.dropna(inplace=True)

    # 레짐 정의
    # TRENDING_UP:   EMA20 > EMA50 AND EMA 기울기 > +0.3%
    # TRENDING_DOWN: EMA20 < EMA50 AND EMA 기울기 < -0.3%
    # RANGING:       나머지
    def classify_regime(row):
        if row['ema20'] > row['ema50'] and row['ema_slope'] > 0.3:
            return 'UP'
        elif row['ema20'] < row['ema50'] and row['ema_slope'] < -0.3:
            return 'DOWN'
        else:
            return 'RANGE'

    daily['regime'] = daily.apply(classify_regime, axis=1)

    regime_counts = daily['regime'].value_counts()
    print(f'\n▶ 레짐 분포: UP={regime_counts.get("UP",0)}일, DOWN={regime_counts.get("DOWN",0)}일, RANGE={regime_counts.get("RANGE",0)}일')

    # 레짐별 전략
    # UP장: LONG-Only, K=0.25
    # DOWN장: SHORT-Only, K=0.25
    # RANGE장: 거래 없음 (횡보 손실 차단)
    strategies = {
        'UP':    {'direction': 'long_only',  'K': 0.25, 'SL': 2.0, 'TP': 5.0},
        'DOWN':  {'direction': 'short_only', 'K': 0.25, 'SL': 2.0, 'TP': 5.0},
        'RANGE': None,  # skip
    }

    cap = CAPITAL
    trades = []
    regime_pnl = {'UP': [], 'DOWN': [], 'RANGE': []}

    for i in range(1, len(daily)):
        prev = daily.iloc[i - 1]
        cur  = daily.iloc[i]
        regime = prev['regime']   # 전날 레짐으로 오늘 전략 결정

        strat = strategies.get(regime)
        if strat is None:
            continue

        K = strat['K']
        SL_PT = strat['SL']
        TP_PT = strat['TP']
        prev_range = prev['high'] - prev['low']
        if prev_range <= 0:
            continue

        open_p = cur['open']
        tl = open_p + prev_range * K
        ts = open_p - prev_range * K
        margin_per = open_p * MULTIPLIER * 0.10
        qty = max(1, int(cap * 0.3 / margin_per)) if margin_per > 0 else 1

        pnl = None
        if strat['direction'] in ('both', 'long_only') and cur['high'] >= tl:
            entry = tl
            if cur['low'] <= entry - SL_PT:
                pnl = -SL_PT * MULTIPLIER * qty
            elif cur['high'] >= entry + TP_PT:
                pnl = TP_PT * MULTIPLIER * qty
            else:
                pnl = (cur['close'] - entry) * MULTIPLIER * qty

        elif strat['direction'] in ('both', 'short_only') and cur['low'] <= ts:
            entry = ts
            if cur['high'] >= entry + SL_PT:
                pnl = -SL_PT * MULTIPLIER * qty
            elif cur['low'] <= entry - TP_PT:
                pnl = TP_PT * MULTIPLIER * qty
            else:
                pnl = (entry - cur['close']) * MULTIPLIER * qty

        if pnl is not None:
            cap += pnl
            trades.append(pnl)
            regime_pnl[regime].append(pnl)

    m = calc_metrics(trades, CAPITAL)
    if m:
        total_ret = (cap - CAPITAL) / CAPITAL * 100
        print(f'\n▶ 레짐 감지 전략 성과:')
        print(f'  총수익률: {total_ret:.1f}%  MDD: {m["mdd"]:.1f}%  승률: {m["win_rate"]:.1f}%  거래: {m["trades"]}회')
        for reg, pnls in regime_pnl.items():
            if pnls:
                wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                print(f'  {reg}장: {len(pnls)}회 거래, 승률 {wr:.0f}%, 누적 {sum(pnls):+,.0f}원')

    return m


# ════════════════════════════════════════════════════════════════
# 4. 최적 파라미터 종합 요약
# ════════════════════════════════════════════════════════════════
def print_summary(best_k200, best_ss, best_sk, regime_result):
    print('\n' + '='*60)
    print('[ 최적화 연구 최종 요약 ]')
    print('='*60)

    if best_k200 is not None:
        print(f'\n[KOSPI200 최적 파라미터]')
        print(f"  K={best_k200['K']:.2f}, SL={best_k200['SL']}pt, TP={best_k200['TP']}pt, 방향={best_k200['dir']}")
        print(f"  수익률={best_k200['total_ret']:.1f}%, MDD={best_k200['mdd']:.1f}%, 승률={best_k200['win_rate']:.1f}%, Score={best_k200['score']:.3f}")

    if best_ss is not None:
        print(f'\n[삼성전자 ISF 최적 파라미터]')
        print(f"  K={best_ss['K']:.2f}, LONG임계={best_ss['LT']:.2f}, 방향={best_ss['dir']}")
        print(f"  수익률={best_ss['total_ret']:.1f}%, MDD={best_ss['mdd']:.1f}%, 승률={best_ss['win_rate']:.1f}%, Score={best_ss['score']:.3f}")

    if best_sk is not None:
        print(f'\n[SK하이닉스 ISF 최적 파라미터]')
        print(f"  K={best_sk['K']:.2f}, LONG임계={best_sk['LT']:.2f}, 방향={best_sk['dir']}")
        print(f"  수익률={best_sk['total_ret']:.1f}%, MDD={best_sk['mdd']:.1f}%, 승률={best_sk['win_rate']:.1f}%, Score={best_sk['score']:.3f}")

    print('\n[추가 연구 권장사항]')
    print('  1. 레짐 감지 전략: 횡보장 진입 차단으로 손실 제한')
    print('  2. ISF Long-Only: 강세장에서 일방향 집중이 효과적')
    print('  3. RSA NSAA 실제 연동: 5일 모멘텀보다 AI 감성 분석이 더 정확')
    print('  4. 주간/야간 세션 분리: 야간은 변동성 다르므로 별도 K값 필요')


# ════════════════════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print('AMATS 전략 최적화 연구 시작...\n')

    best_k200 = optimize_kospi200()
    best_ss   = optimize_isf('005930', '삼성전자', SL_PCT=1.5, TP_PCT=4.0)
    best_sk   = optimize_isf('000660', 'SK하이닉스', SL_PCT=1.5, TP_PCT=4.0)
    regime_r  = analyze_regime_strategy()

    print_summary(best_k200, best_ss, best_sk, regime_r)
