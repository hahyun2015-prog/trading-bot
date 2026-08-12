"""
AMATS 고도화 선물 전략 백테스터 (NotebookLM 지식 기반 통합)
=============================================================
기존 변동성 돌파(K값) + 볼린저/RSI 다이버전스 전략에 다음을 추가 반영:
 1. 볼린저밴드 스퀴즈(Squeeze) → 횡보장 진입 차단 필터
 2. ATR 기반 동적 포지션 사이징 (계좌의 1~2% 위험 한도)
 3. ATR 기반 동적 트레일링 스탑 (고정 손절 대체)
 4. MDD 페널티 가중 최적화 (CAGR 대신 CAGR/MDD 비율 사용)
 5. 주간/야간 세션 분리 K값 최적화
"""

import sqlite3
import os
import sys
import json
import io
import pandas as pd
import numpy as np
from datetime import datetime

# Prevent encoding crashes in CP949 environment (Windows console)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

# 지표 계산 단일 소스 (마이그레이션 4번, 2026-08-12). 이전에는 `ta` 패키지를 썼다.
# 동작 무변경이 목표이므로 ta의 비표준 시맨틱을 그대로 재현하는 ta_* 변형을 쓴다:
#   - 볼린저: ta는 모표준편차(ddof=0). 라이브(era_order_manager)의 ddof=1과 다르다.
#   - RSI:    ta는 Wilder RSI가 아니다(0 시드 EWM). wilder_rsi와 최대 26.15 차이.
#   - ATR:    ta는 워밍업 구간을 NaN이 아니라 0.0으로 채운다(dropna에 안 걸린다).
# 이 차이들은 이번에 고치지 않는다 — 발견 목록 N1/N2/N8 참조.
import indicators as I

# (2026-08-12) 결과 저장 경로 — 기본을 안전한 scratch/ 로 바꿨다.
# 이전 기본값은 config/active_strategy.json 이었다. 이 파일은 era_order_manager.py(1402행)가
# 기동 시 읽어 주간선물 파라미터를 오버라이드하는 라이브 설정이고, 아래 저장 블록은 병합이
# 아니라 통째 덮어쓰기다. 그래서 이 백테스터를 한 번 실행해 보기만 해도 approved_at 과
# best_k(0.2) 가 소리 없이 유실됐다. approved_at 이 사라지면 ERA 는 파일 전체를 무시하고
# config.json 값으로 되돌아가는데, best_k 는 config.json 에 없어 코드 기본값 0.5 가 된다.
#   기본          : scratch/enhanced_backtest_result.json
#   EBT_OUT 환경변수 / --out : 임의 경로 지정
#   --write-live  : 라이브 설정에 직접 쓰기 (명시적으로 요구해야만 가능)
LIVE_RESULTS_FILE = os.path.join(workspace_root, "config", "active_strategy.json")
DEFAULT_RESULTS_FILE = os.path.join(workspace_root, "scratch", "enhanced_backtest_result.json")
RESULTS_FILE = os.environ.get("EBT_OUT") or DEFAULT_RESULTS_FILE


def load_futures_data(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10100000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df.dropna(subset=['date'], inplace=True)
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
    return df


def compute_indicators(df):
    """볼린저밴드, RSI, ATR 등 핵심 지표 계산"""
    _closes = df['close'].to_numpy(dtype=float)
    _highs = df['high'].to_numpy(dtype=float)
    _lows = df['low'].to_numpy(dtype=float)

    # 볼린저밴드 (20, 2)
    _bb_m, _bb_h, _bb_l = I.bollinger_series(_closes, 20, 2.0, ddof=0)  # ddof=0 = ta 시맨틱
    df['bb_m'] = _bb_m
    df['bb_h'] = _bb_h
    df['bb_l'] = _bb_l
    df['bb_width'] = (df['bb_h'] - df['bb_l']) / df['bb_m']  # 밴드 폭 (스퀴즈 판별용)

    # RSI (14)
    df['rsi'] = I.ta_rsi_series(_closes, 14)

    # ATR (14)
    df['atr'] = I.ta_atr_series(_highs, _lows, _closes, 14)

    # MACD
    _macd, _macd_sig, _macd_diff = I.ta_macd_series(_closes, window_slow=26, window_fast=12, window_sign=9)
    df['macd'] = _macd
    df['macd_signal'] = _macd_sig
    df['macd_diff'] = _macd_diff

    # EMA (10, 34) — 마하세븐 눌림목 스캘핑
    df['ema_10'] = I.ta_ema_series(_closes, 10)
    df['ema_34'] = I.ta_ema_series(_closes, 34)

    # 스퀴즈 상태 감지 (bb_width가 최근 100봉 최저의 20% 이내일 때 = 수축 중)
    df['bb_width_min_100'] = df['bb_width'].rolling(window=100, min_periods=20).min()
    df['is_squeeze'] = df['bb_width'] <= df['bb_width_min_100'] * 1.20

    df.dropna(inplace=True)
    return df


# ─── 전략 A: 기존 순수 변동성 돌파 (K값) ──────────────────────────────────
def strategy_a_volatility_breakout(df, K, run_mode='24H'):
    """기존 batch_optimizer 로직과 동일 (비교 기준선)"""
    df = df.copy()
    df['date_only'] = df.index.date
    daily = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)

    df = df.join(daily[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df.rename(columns={'open_day': 'day_open'}, inplace=True)

    PV = 250000
    INIT = 50_000_000
    capital = INIT
    pos = 0
    entry = 0.0
    trades = 0
    wins = 0
    equity = [INIT]

    for i in range(len(df)):
        row = df.iloc[i]
        t = df.index[i]
        price = row['close']
        day_o = row['day_open']
        pr = row['prev_range']
        if pd.isna(pr):
            continue

        target_l = day_o + pr * K
        target_s = day_o - pr * K

        morning_close = (t.hour == 8 and 45 <= t.minute <= 50)

        if pos != 0:
            if morning_close:
                pnl_pt = (price - entry if pos == 1 else entry - price) - 0.05
                capital += pnl_pt * PV
                pos = 0
                trades += 1
                if pnl_pt > 0: wins += 1
                equity.append(capital)
            continue

        can_enter = True
        if run_mode == 'DayOnly' and (t.hour >= 15 or t.hour < 9):
            can_enter = False
        elif run_mode == 'NightOnly' and (6 <= t.hour < 18):
            can_enter = False

        if pos == 0 and can_enter and not morning_close:
            if row['high'] >= target_l:
                pos = 1; entry = target_l
            elif row['low'] <= target_s:
                pos = -1; entry = target_s

    # MDD 계산
    peak = equity[0]
    mdd = 0
    for eq in equity:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > mdd: mdd = dd

    wr = (wins / trades * 100) if trades > 0 else 0
    profit = (capital - INIT) / INIT * 100
    days = max(1, (df.index[-1] - df.index[0]).days)
    cagr = ((capital / INIT) ** (365 / days) - 1) * 100 if capital > 0 else -100

    return {'K': K, 'trades': trades, 'win_rate': round(wr, 2), 'profit': round(profit, 2),
            'cagr': round(cagr, 2), 'mdd': round(mdd, 2), 'final_capital': round(capital)}


# ─── 전략 B: 고도화 전략 (NotebookLM 지식 기반) ────────────────────────────
def strategy_b_enhanced(df, K, run_mode='24H'):
    """
    고도화 반영 내용:
    - 볼린저밴드 스퀴즈 상태에서 진입 차단 (횡보 휩쏘 방지)
    - ATR 기반 동적 손절 (고정 1pt → ATR*2.0 배수)
    - ATR 기반 동적 트레일링 스탑 (ATR*2.5 배수)
    - RSI 50 돌파 확인 필터 (LONG: RSI > 50, SHORT: RSI < 50)
    - MACD 방향 확인 필터 (LONG: macd_diff > 0, SHORT: macd_diff < 0)
    """
    df = df.copy()
    df['date_only'] = df.index.date
    daily = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)

    df = df.join(daily[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df.rename(columns={'open_day': 'day_open'}, inplace=True)

    PV = 250000
    INIT = 50_000_000
    RISK_PER_TRADE = 0.015  # 매매당 자본의 1.5% 위험 노출 (ATR 포지션 사이징)
    capital = INIT
    pos = 0
    entry = 0.0
    contracts = 1
    trailing_stop = 0.0
    max_favorable = 0.0
    trades = 0
    wins = 0
    equity = [INIT]

    for i in range(len(df)):
        row = df.iloc[i]
        t = df.index[i]
        price = row['close']
        day_o = row['day_open']
        pr = row['prev_range']
        atr_val = row['atr']

        if pd.isna(pr) or pd.isna(atr_val) or atr_val <= 0:
            continue

        target_l = day_o + pr * K
        target_s = day_o - pr * K
        morning_close = (t.hour == 8 and 45 <= t.minute <= 50)

        # ── 포지션 보유 중: 동적 트레일링 스탑 + 장마감 청산 ──
        if pos != 0:
            exit_reason = None

            if pos == 1:
                if price > max_favorable:
                    max_favorable = price
                    trailing_stop = max_favorable - atr_val * 2.5
                if price <= trailing_stop:
                    exit_reason = "트레일링스탑"
                elif morning_close:
                    exit_reason = "장마감"
            elif pos == -1:
                if price < max_favorable:
                    max_favorable = price
                    trailing_stop = max_favorable + atr_val * 2.5
                if price >= trailing_stop:
                    exit_reason = "트레일링스탑"
                elif morning_close:
                    exit_reason = "장마감"

            if exit_reason:
                pnl_pt = (price - entry if pos == 1 else entry - price) - 0.05
                pnl_money = pnl_pt * PV * contracts
                capital += pnl_money
                pos = 0
                trades += 1
                if pnl_money > 0: wins += 1
                equity.append(capital)
            continue

        # ── 신규 진입 필터링 ──
        can_enter = True
        if run_mode == 'DayOnly' and (t.hour >= 15 or t.hour < 9):
            can_enter = False
        elif run_mode == 'NightOnly' and (6 <= t.hour < 18):
            can_enter = False

        if pos == 0 and can_enter and not morning_close:
            # 필터 1: 볼린저밴드 스퀴즈 상태 → 진입 차단
            if row.get('is_squeeze', False):
                continue

            # ATR 기반 동적 계약 수 계산
            risk_amount = capital * RISK_PER_TRADE
            stop_distance_pt = atr_val * 2.0  # 초기 손절폭
            contracts_calc = max(1, int(risk_amount / (stop_distance_pt * PV)))

            # LONG 진입
            if row['high'] >= target_l:
                # 필터 2: RSI 50 이상 확인
                # 필터 3: MACD 히스토그램 양전환 확인
                if row['rsi'] >= 50 and row['macd_diff'] > 0:
                    pos = 1
                    entry = target_l
                    contracts = contracts_calc
                    max_favorable = entry
                    trailing_stop = entry - stop_distance_pt

            # SHORT 진입
            elif row['low'] <= target_s:
                if row['rsi'] <= 50 and row['macd_diff'] < 0:
                    pos = -1
                    entry = target_s
                    contracts = contracts_calc
                    max_favorable = entry
                    trailing_stop = entry + stop_distance_pt

    # 잔여 포지션 강제 청산
    if pos != 0:
        pnl_pt = (df.iloc[-1]['close'] - entry if pos == 1 else entry - df.iloc[-1]['close']) - 0.05
        capital += pnl_pt * PV * contracts
        trades += 1
        if pnl_pt > 0: wins += 1
        equity.append(capital)

    # MDD 계산
    peak = equity[0]
    mdd = 0
    for eq in equity:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > mdd: mdd = dd

    wr = (wins / trades * 100) if trades > 0 else 0
    profit = (capital - INIT) / INIT * 100
    days = max(1, (df.index[-1] - df.index[0]).days)
    cagr = ((capital / INIT) ** (365 / days) - 1) * 100 if capital > 0 else -100

    return {'K': K, 'trades': trades, 'win_rate': round(wr, 2), 'profit': round(profit, 2),
            'cagr': round(cagr, 2), 'mdd': round(mdd, 2), 'final_capital': round(capital),
            'contracts_sample': contracts}


# ─── 전략 C: 볼린저밴드 + RSI 다이버전스 (기존 backtester 재현) ──────────
def strategy_c_bb_rsi_divergence(df, run_mode='24H'):
    """기존 backtester.py의 볼린저밴드+RSI 다이버전스 전략 재현"""
    PV = 250000
    SLIPPAGE = 0.05
    FEE = 0.00003
    INIT = 50_000_000
    STOP = 1.0
    capital = INIT
    pos = 0
    entry = 0.0
    trades = 0
    wins = 0
    equity = [INIT]

    for i in range(10, len(df)):
        row = df.iloc[i]
        t = df.index[i]
        price = row['close']

        # RSI 다이버전스 감지
        bull_div = False
        bear_div = False
        prev_rsi_min = df['rsi'].iloc[i-10:i-5].min()
        curr_rsi_min = df['rsi'].iloc[i-5:i].min()
        prev_low_min = df['low'].iloc[i-10:i-5].min()
        curr_low_min = df['low'].iloc[i-5:i].min()
        if curr_low_min <= prev_low_min and curr_rsi_min > prev_rsi_min:
            bull_div = True

        prev_rsi_max = df['rsi'].iloc[i-10:i-5].max()
        curr_rsi_max = df['rsi'].iloc[i-5:i].max()
        prev_high_max = df['high'].iloc[i-10:i-5].max()
        curr_high_max = df['high'].iloc[i-5:i].max()
        if curr_high_max >= prev_high_max and curr_rsi_max < prev_rsi_max:
            bear_div = True

        force_close = False
        if t.hour == 15 and t.minute >= 30: force_close = True
        elif t.hour == 4 and t.minute >= 50: force_close = True

        if pos != 0:
            exit_r = None
            if pos == 1:
                if price <= entry - STOP: exit_r = "손절"
                elif price >= row['bb_m']: exit_r = "익절"
                elif force_close: exit_r = "장마감"
            elif pos == -1:
                if price >= entry + STOP: exit_r = "손절"
                elif price <= row['bb_m']: exit_r = "익절"
                elif force_close: exit_r = "장마감"

            if exit_r:
                slip = -SLIPPAGE if pos == 1 else SLIPPAGE
                ep = price + slip
                pnl_pt = (ep - entry if pos == 1 else entry - ep)
                pnl = pnl_pt * PV - (entry + ep) * PV * FEE
                capital += pnl
                pos = 0
                trades += 1
                if pnl > 0: wins += 1
                equity.append(capital)

        if pos == 0 and not force_close:
            can_enter = True
            if run_mode == 'DayOnly' and (t.hour >= 15 or t.hour < 9):
                can_enter = False
            elif run_mode == 'NightOnly' and (6 <= t.hour < 18):
                can_enter = False

            if can_enter:
                if row['low'] <= row['bb_l'] and (row['rsi'] <= 30 or bull_div):
                    pos = 1; entry = price + SLIPPAGE
                elif row['high'] >= row['bb_h'] and (row['rsi'] >= 70 or bear_div):
                    pos = -1; entry = price - SLIPPAGE

    peak = equity[0]
    mdd = 0
    for eq in equity:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > mdd: mdd = dd

    wr = (wins / trades * 100) if trades > 0 else 0
    profit = (capital - INIT) / INIT * 100
    days = max(1, (df.index[-1] - df.index[0]).days)
    cagr = ((capital / INIT) ** (365 / days) - 1) * 100 if capital > 0 else -100

    return {'trades': trades, 'win_rate': round(wr, 2), 'profit': round(profit, 2),
            'cagr': round(cagr, 2), 'mdd': round(mdd, 2), 'final_capital': round(capital)}


# ─── 메인 실행 ──────────────────────────────────────────────────────────
def run_comprehensive_backtest():
    print("=" * 75)
    print("  AMATS 고도화 백테스터 — NotebookLM 지식 기반 전략 비교 분석")
    print("=" * 75)

    db_path = os.path.join(workspace_root, "futures_data.db")
    df_raw = load_futures_data(db_path)
    if df_raw.empty:
        print("데이터가 없습니다.")
        return

    print(f"데이터 로드 완료: {len(df_raw)}개 캔들 ({df_raw.index[0]} ~ {df_raw.index[-1]})")

    df = compute_indicators(df_raw)
    print(f"지표 계산 완료: {len(df)}개 유효 캔들\n")

    # ──────────────────────────────────────────────────────────────────────
    # 1. 전략 A (기존 순수 변동성 돌파) — K값 스위핑
    # ──────────────────────────────────────────────────────────────────────
    print("━" * 75)
    print("  [전략 A] 기존 순수 변동성 돌파 (래리 윌리엄스) — K값 스위핑")
    print("━" * 75)

    results_a = []
    for k in np.arange(0.3, 0.85, 0.05):
        k = round(k, 2)
        r = strategy_a_volatility_breakout(df, k, '24H')
        if r['trades'] > 10:
            results_a.append(r)

    results_a.sort(key=lambda x: x['cagr'], reverse=True)
    print(f"{'K값':>6} | {'매매횟수':>8} | {'승률(%)':>8} | {'수익률(%)':>10} | {'CAGR(%)':>8} | {'MDD(%)':>8} | {'최종자본':>14}")
    print("-" * 75)
    for r in results_a[:5]:
        print(f"{r['K']:>6.2f} | {r['trades']:>8} | {r['win_rate']:>8.1f} | {r['profit']:>10.2f} | {r['cagr']:>8.2f} | {r['mdd']:>8.2f} | {r['final_capital']:>14,}")

    best_a = results_a[0] if results_a else None

    # ──────────────────────────────────────────────────────────────────────
    # 2. 전략 B (고도화 — 스퀴즈 필터 + ATR 동적 사이징 + RSI/MACD 필터)
    # ──────────────────────────────────────────────────────────────────────
    print(f"\n{'━' * 75}")
    print("  [전략 B] 고도화 전략 (스퀴즈필터 + ATR포지션사이징 + RSI/MACD필터)")
    print("━" * 75)

    results_b = []
    for k in np.arange(0.3, 0.85, 0.05):
        k = round(k, 2)
        r = strategy_b_enhanced(df, k, '24H')
        if r['trades'] > 5:
            results_b.append(r)

    results_b.sort(key=lambda x: (x['cagr'] / max(x['mdd'], 0.1)), reverse=True)  # CAGR/MDD 비율 최적화
    print(f"{'K값':>6} | {'매매횟수':>8} | {'승률(%)':>8} | {'수익률(%)':>10} | {'CAGR(%)':>8} | {'MDD(%)':>8} | {'CAGR/MDD':>8} | {'최종자본':>14}")
    print("-" * 85)
    for r in results_b[:5]:
        ratio = r['cagr'] / max(r['mdd'], 0.1)
        print(f"{r['K']:>6.2f} | {r['trades']:>8} | {r['win_rate']:>8.1f} | {r['profit']:>10.2f} | {r['cagr']:>8.2f} | {r['mdd']:>8.2f} | {ratio:>8.2f} | {r['final_capital']:>14,}")

    best_b = results_b[0] if results_b else None

    # ──────────────────────────────────────────────────────────────────────
    # 3. 전략 C (볼린저밴드 + RSI 다이버전스 — 기존 backtester.py)
    # ──────────────────────────────────────────────────────────────────────
    print(f"\n{'━' * 75}")
    print("  [전략 C] 기존 볼린저밴드 + RSI 다이버전스 전략")
    print("━" * 75)

    modes = [('주간장 전용 (09:00~15:00)', 'DayOnly'),
             ('야간장 전용 (18:00~04:50)', 'NightOnly'),
             ('주야간 24시간 풀가동', '24H')]

    print(f"{'운영 시간대':<28} | {'매매횟수':>8} | {'승률(%)':>8} | {'수익률(%)':>10} | {'CAGR(%)':>8} | {'MDD(%)':>8}")
    print("-" * 85)
    for label, mode in modes:
        r = strategy_c_bb_rsi_divergence(df, mode)
        print(f"{label:<28} | {r['trades']:>8} | {r['win_rate']:>8.1f} | {r['profit']:>10.2f} | {r['cagr']:>8.2f} | {r['mdd']:>8.2f}")

    # ──────────────────────────────────────────────────────────────────────
    # 4. 전략 B 세션 분리 분석 (주간 vs 야간)
    # ──────────────────────────────────────────────────────────────────────
    if best_b:
        best_k = best_b['K']
        print(f"\n{'━' * 75}")
        print(f"  [전략 B 세션 분석] 최적 K={best_k} 기준 주간 vs 야간 비교")
        print("━" * 75)

        print(f"{'세션':<20} | {'매매횟수':>8} | {'승률(%)':>8} | {'수익률(%)':>10} | {'CAGR(%)':>8} | {'MDD(%)':>8}")
        print("-" * 75)
        for label, mode in [('주간장', 'DayOnly'), ('야간장', 'NightOnly'), ('통합 24H', '24H')]:
            r = strategy_b_enhanced(df, best_k, mode)
            print(f"{label:<20} | {r['trades']:>8} | {r['win_rate']:>8.1f} | {r['profit']:>10.2f} | {r['cagr']:>8.2f} | {r['mdd']:>8.2f}")

    # ──────────────────────────────────────────────────────────────────────
    # 5. 최종 비교 요약
    # ──────────────────────────────────────────────────────────────────────
    print(f"\n{'═' * 75}")
    print("  📊 최종 전략 비교 요약 (A: 기존 vs B: 고도화 vs C: BB+RSI)")
    print("═" * 75)

    rc = strategy_c_bb_rsi_divergence(df, '24H')

    print(f"{'전략':<35} | {'승률(%)':>8} | {'CAGR(%)':>8} | {'MDD(%)':>8} | {'CAGR/MDD':>8}")
    print("-" * 75)
    if best_a:
        ratio_a = best_a['cagr'] / max(best_a['mdd'], 0.1)
        print(f"{'A: 순수 변동성 돌파 K=' + str(best_a['K']):<35} | {best_a['win_rate']:>8.1f} | {best_a['cagr']:>8.2f} | {best_a['mdd']:>8.2f} | {ratio_a:>8.2f}")
    if best_b:
        ratio_b = best_b['cagr'] / max(best_b['mdd'], 0.1)
        print(f"{'B: 고도화(ATR+스퀴즈) K=' + str(best_b['K']):<35} | {best_b['win_rate']:>8.1f} | {best_b['cagr']:>8.2f} | {best_b['mdd']:>8.2f} | {ratio_b:>8.2f}")
    ratio_c = rc['cagr'] / max(rc['mdd'], 0.1)
    print(f"{'C: BB+RSI 다이버전스':<35} | {rc['win_rate']:>8.1f} | {rc['cagr']:>8.2f} | {rc['mdd']:>8.2f} | {ratio_c:>8.2f}")

    # ── 결과 저장 ─────────────────────────────────────────────────────────
    if best_b:
        out = {
            'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'strategy': 'enhanced_v2',
            'best_k': best_b['K'],
            'top_strategies': results_b[:3],
            'comparison': {
                'strategy_a_best': best_a,
                'strategy_b_best': best_b,
                'strategy_c_24h': rc
            }
        }
        os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=4)
        print(f"\n✅ 최적화 결과가 {RESULTS_FILE} 에 저장되었습니다.")
        print(f"   고도화 전략 최적 K값: {best_b['K']} (CAGR/MDD 비율 기준)")


if __name__ == "__main__":
    import argparse

    _ap = argparse.ArgumentParser(description="고도화 백테스터 (A/B/C 전략 비교)")
    _ap.add_argument("--out", metavar="PATH",
                     help="결과 JSON 저장 경로 (기본: scratch/enhanced_backtest_result.json)")
    _ap.add_argument("--write-live", action="store_true",
                     help="결과를 config/active_strategy.json(ERA 가 읽는 라이브 주간선물 설정)에 "
                          "직접 쓴다. 통째 덮어쓰기라 approved_at 이 사라지고, 그러면 ERA 는 이 "
                          "파일을 무시한다. 승인 절차를 거친 경우에만 사용할 것.")
    _args = _ap.parse_args()

    if _args.out:
        RESULTS_FILE = _args.out
    elif _args.write_live:
        RESULTS_FILE = LIVE_RESULTS_FILE
        print("[경고] --write-live 지정 — 라이브 설정을 덮어씁니다: %s" % RESULTS_FILE)
        print("[경고] 이 저장은 approved_at 을 남기지 않으므로 ERA 는 결과를 적용하지 않습니다.")

    run_comprehensive_backtest()
