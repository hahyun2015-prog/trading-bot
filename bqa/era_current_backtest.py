# -*- coding: utf-8 -*-
"""
AMATS 현행 시스템 방식 백테스터
================================
ERA era_order_manager.py에 실제 구현된 로직을 100% 그대로 재현합니다.

현행 ERA 선물 전략 요약:
  진입: 09:00 시초가 ± prev_range * K 돌파 시 1회 진입
  청산: 익일 08:45 시간 청산만 존재 (손절/트레일링 스탑 없음)
  수량: 마진 기반 최대 계약수 (자본 30% 캡)
  필터: 없음 (가격 돌파만 확인)

비교 대상:
  (1) 현행 ERA 전략 — K값 스위핑
  (2) _load_prev_range SQL 버그 시뮬레이션 (date() vs SUBSTR)
  (3) 개선안 반영 전/후 성과 비교
"""

import sqlite3
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)


def load_futures_data(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10100000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        df.dropna(subset=['date'], inplace=True)
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
    return df


# ═══════════════════════════════════════════════════════════════════════
# 현행 ERA 전략 — 정확한 재현
# ═══════════════════════════════════════════════════════════════════════

def era_current_strategy(df, K, initial_capital=50_000_000, margin_cap_ratio=0.3):
    """
    ERA _process_day_tick 로직을 정확히 재현:
    - 09:00~09:04 첫 봉에서 시초가 확정
    - 진입: price >= target_long 또는 price <= target_short
    - 청산: 익일 08:45~08:55 시간 청산만 (손절/트레일링 없음)
    - 수량: margin_per = price * 250000 * 0.10, safe = capital * 0.3
    - 하루 1회만 진입 (futures_order_locked)
    """
    PV = 250000
    MARGIN_RATE = 0.10
    SLIPPAGE = 0.05

    df = df.copy()
    df['date_str'] = df.index.strftime('%Y%m%d')

    # 일별 집계 (전일 Range 계산)
    daily = df.groupby('date_str').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)

    capital = initial_capital
    pos = 0           # 0=없음, 1=LONG, -1=SHORT
    entry_price = 0.0
    contracts = 1
    order_locked = False
    day_open = 0.0
    target_long = float('inf')
    target_short = float('-inf')
    current_day = ''
    prev_range = 20.0  # ERA 기본값

    trades = 0
    wins = 0
    total_pnl = 0.0
    equity = [capital]
    trade_log = []

    for i in range(len(df)):
        row = df.iloc[i]
        t = df.index[i]
        price = row['close']
        day_str = t.strftime('%Y%m%d')

        # ── 날짜 전환 감지 (09:00 리셋) ──
        if day_str != current_day:
            current_day = day_str
            day_open = 0.0
            order_locked = False
            # 전일 Range 갱신
            if day_str in daily.index:
                pr = daily.loc[day_str, 'prev_range']
                if not pd.isna(pr) and pr > 0:
                    prev_range = pr

        # ── 09:00 시초가 확정 ──
        if t.hour == 9 and t.minute < 5 and day_open == 0:
            day_open = price
            target_long = day_open + prev_range * K
            target_short = day_open - prev_range * K

        if day_open == 0:
            continue

        # ── 08:45~08:55 익일 장전 강제 청산 ──
        if t.hour == 8 and 45 <= t.minute <= 55:
            if pos != 0:
                if pos == 1:
                    exit_price = price - SLIPPAGE
                    pnl_pt = exit_price - entry_price
                else:
                    exit_price = price + SLIPPAGE
                    pnl_pt = entry_price - exit_price

                pnl_money = pnl_pt * PV * contracts
                capital += pnl_money
                total_pnl += pnl_money
                trades += 1
                if pnl_money > 0:
                    wins += 1
                trade_log.append({
                    'date': t.strftime('%Y-%m-%d %H:%M'),
                    'type': 'LONG' if pos == 1 else 'SHORT',
                    'entry': round(entry_price, 2),
                    'exit': round(exit_price, 2),
                    'pnl_pt': round(pnl_pt, 2),
                    'contracts': contracts,
                    'pnl_money': round(pnl_money),
                    'capital': round(capital)
                })
                equity.append(capital)
                pos = 0
                order_locked = False
            continue

        # ── 진입 조건 (현행 ERA 그대로) ──
        if pos == 0 and not order_locked:
            if price >= target_long:
                # 마진 기반 포지션 사이징 (ERA 현행 방식)
                margin_per = price * PV * MARGIN_RATE
                safe_budget = capital * margin_cap_ratio
                contracts = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
                entry_price = price + SLIPPAGE
                pos = 1
                order_locked = True

            elif price <= target_short:
                margin_per = price * PV * MARGIN_RATE
                safe_budget = capital * margin_cap_ratio
                contracts = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
                entry_price = price - SLIPPAGE
                pos = -1
                order_locked = True

    # 잔여 포지션 강제 청산
    if pos != 0:
        last_price = df.iloc[-1]['close']
        if pos == 1:
            pnl_pt = (last_price - SLIPPAGE) - entry_price
        else:
            pnl_pt = entry_price - (last_price + SLIPPAGE)
        pnl_money = pnl_pt * PV * contracts
        capital += pnl_money
        trades += 1
        if pnl_money > 0: wins += 1
        equity.append(capital)

    # MDD 계산
    peak = equity[0]
    mdd = 0
    max_dd_amount = 0
    for eq in equity:
        if eq > peak:
            peak = eq
        dd_pct = (peak - eq) / peak * 100
        dd_amount = peak - eq
        if dd_pct > mdd:
            mdd = dd_pct
        if dd_amount > max_dd_amount:
            max_dd_amount = dd_amount

    wr = (wins / trades * 100) if trades > 0 else 0
    profit_pct = (capital - initial_capital) / initial_capital * 100
    days = max(1, (df.index[-1] - df.index[0]).days)
    cagr = ((capital / initial_capital) ** (365 / days) - 1) * 100 if capital > 0 else -100

    # 연속 손실 분석
    max_consec_loss = 0
    current_consec = 0
    for t_log in trade_log:
        if t_log['pnl_money'] < 0:
            current_consec += 1
            max_consec_loss = max(max_consec_loss, current_consec)
        else:
            current_consec = 0

    # 평균 손익비
    win_amounts = [t['pnl_money'] for t in trade_log if t['pnl_money'] > 0]
    loss_amounts = [abs(t['pnl_money']) for t in trade_log if t['pnl_money'] < 0]
    avg_win = sum(win_amounts) / len(win_amounts) if win_amounts else 0
    avg_loss = sum(loss_amounts) / len(loss_amounts) if loss_amounts else 1
    profit_factor = sum(win_amounts) / sum(loss_amounts) if loss_amounts and sum(loss_amounts) > 0 else float('inf')

    return {
        'K': K,
        'trades': trades,
        'win_rate': round(wr, 2),
        'profit_pct': round(profit_pct, 2),
        'cagr': round(cagr, 2),
        'mdd': round(mdd, 2),
        'max_dd_amount': round(max_dd_amount),
        'final_capital': round(capital),
        'avg_win': round(avg_win),
        'avg_loss': round(avg_loss),
        'payoff_ratio': round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
        'profit_factor': round(profit_factor, 2),
        'max_consec_loss': max_consec_loss,
        'trade_log': trade_log,
        'equity': equity
    }


# ═══════════════════════════════════════════════════════════════════════
# SQL 버그 검증: date() vs SUBSTR() Range 계산 차이
# ═══════════════════════════════════════════════════════════════════════

def verify_sql_bug(db_path):
    """ERA의 _load_prev_range가 date() 함수를 사용하는 버그 검증"""
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    # 현행 ERA 방식 (date() 함수 — 버그 의심)
    cursor.execute("""
        SELECT date(date) as d, MAX(high) as h, MIN(low) as l
        FROM futures_ohlcv WHERE code = '10100000'
        GROUP BY d ORDER BY d DESC LIMIT 5
    """)
    bug_rows = cursor.fetchall()

    # 올바른 방식 (SUBSTR)
    cursor.execute("""
        SELECT SUBSTR(date, 1, 8) as d, MAX(high) as h, MIN(low) as l
        FROM futures_ohlcv WHERE code = '10100000'
        GROUP BY SUBSTR(date, 1, 8) ORDER BY d DESC LIMIT 5
    """)
    fix_rows = cursor.fetchall()
    conn.close()

    return bug_rows, fix_rows


# ═══════════════════════════════════════════════════════════════════════
# 개선안 A: 고정 손절 추가 (가장 단순한 개선)
# ═══════════════════════════════════════════════════════════════════════

def era_with_fixed_stoploss(df, K, stop_pt=2.0, initial_capital=50_000_000):
    """현행 ERA + 고정 pt 손절만 추가한 최소 개선안"""
    PV = 250000
    MARGIN_RATE = 0.10
    SLIPPAGE = 0.05

    df = df.copy()
    df['date_str'] = df.index.strftime('%Y%m%d')
    daily = df.groupby('date_str').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)

    capital = initial_capital
    pos = 0
    entry_price = 0.0
    contracts = 1
    order_locked = False
    day_open = 0.0
    target_long = float('inf')
    target_short = float('-inf')
    current_day = ''
    prev_range = 20.0
    trades = 0
    wins = 0
    equity = [capital]

    for i in range(len(df)):
        row = df.iloc[i]
        t = df.index[i]
        price = row['close']
        day_str = t.strftime('%Y%m%d')

        if day_str != current_day:
            current_day = day_str
            day_open = 0.0
            order_locked = False
            if day_str in daily.index:
                pr = daily.loc[day_str, 'prev_range']
                if not pd.isna(pr) and pr > 0:
                    prev_range = pr

        if t.hour == 9 and t.minute < 5 and day_open == 0:
            day_open = price
            target_long = day_open + prev_range * K
            target_short = day_open - prev_range * K

        if day_open == 0:
            continue

        # 포지션 보유 중 — 손절/시간청산 확인
        if pos != 0:
            exit_reason = None
            if t.hour == 8 and 45 <= t.minute <= 55:
                exit_reason = "TIME"
            elif pos == 1 and price <= entry_price - stop_pt:
                exit_reason = "STOPLOSS"
            elif pos == -1 and price >= entry_price + stop_pt:
                exit_reason = "STOPLOSS"

            if exit_reason:
                if pos == 1:
                    pnl_pt = (price - SLIPPAGE) - entry_price
                else:
                    pnl_pt = entry_price - (price + SLIPPAGE)
                pnl_money = pnl_pt * PV * contracts
                capital += pnl_money
                trades += 1
                if pnl_money > 0: wins += 1
                equity.append(capital)
                pos = 0
                order_locked = False
            if t.hour == 8 and 45 <= t.minute <= 55:
                continue

        if pos == 0 and not order_locked:
            if price >= target_long:
                margin_per = price * PV * MARGIN_RATE
                safe_budget = capital * 0.3
                contracts = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
                entry_price = price + SLIPPAGE
                pos = 1
                order_locked = True
            elif price <= target_short:
                margin_per = price * PV * MARGIN_RATE
                safe_budget = capital * 0.3
                contracts = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
                entry_price = price - SLIPPAGE
                pos = -1
                order_locked = True

    if pos != 0:
        last_price = df.iloc[-1]['close']
        pnl_pt = ((last_price - SLIPPAGE) - entry_price) if pos == 1 else (entry_price - (last_price + SLIPPAGE))
        capital += pnl_pt * PV * contracts
        trades += 1
        if pnl_pt > 0: wins += 1
        equity.append(capital)

    peak = equity[0]; mdd = 0
    for eq in equity:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > mdd: mdd = dd

    wr = (wins / trades * 100) if trades > 0 else 0
    profit = (capital - initial_capital) / initial_capital * 100
    days = max(1, (df.index[-1] - df.index[0]).days)
    cagr = ((capital / initial_capital) ** (365 / days) - 1) * 100 if capital > 0 else -100

    return {'K': K, 'stop_pt': stop_pt, 'trades': trades, 'win_rate': round(wr, 2),
            'profit_pct': round(profit, 2), 'cagr': round(cagr, 2), 'mdd': round(mdd, 2),
            'final_capital': round(capital)}


# ═══════════════════════════════════════════════════════════════════════
# 개선안 B: 고정 손절 + 고정 익절
# ═══════════════════════════════════════════════════════════════════════

def era_with_stop_and_target(df, K, stop_pt=2.0, target_pt=3.0, initial_capital=50_000_000):
    """현행 ERA + 고정 손절 + 고정 익절 목표가"""
    PV = 250000
    SLIPPAGE = 0.05
    df = df.copy()
    df['date_str'] = df.index.strftime('%Y%m%d')
    daily = df.groupby('date_str').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)

    capital = initial_capital
    pos = 0; entry_price = 0.0; contracts = 1
    order_locked = False; day_open = 0.0
    target_long = float('inf'); target_short = float('-inf')
    current_day = ''; prev_range = 20.0
    trades = 0; wins = 0; equity = [capital]

    for i in range(len(df)):
        row = df.iloc[i]; t = df.index[i]; price = row['close']
        day_str = t.strftime('%Y%m%d')
        if day_str != current_day:
            current_day = day_str; day_open = 0.0; order_locked = False
            if day_str in daily.index:
                pr = daily.loc[day_str, 'prev_range']
                if not pd.isna(pr) and pr > 0: prev_range = pr
        if t.hour == 9 and t.minute < 5 and day_open == 0:
            day_open = price
            target_long = day_open + prev_range * K
            target_short = day_open - prev_range * K
        if day_open == 0: continue

        if pos != 0:
            exit_reason = None
            if t.hour == 8 and 45 <= t.minute <= 55: exit_reason = "TIME"
            elif pos == 1:
                if price <= entry_price - stop_pt: exit_reason = "STOP"
                elif price >= entry_price + target_pt: exit_reason = "TARGET"
            elif pos == -1:
                if price >= entry_price + stop_pt: exit_reason = "STOP"
                elif price <= entry_price - target_pt: exit_reason = "TARGET"
            if exit_reason:
                pnl_pt = ((price - SLIPPAGE) - entry_price) if pos == 1 else (entry_price - (price + SLIPPAGE))
                capital += pnl_pt * PV * contracts
                trades += 1
                if pnl_pt > 0: wins += 1
                equity.append(capital)
                pos = 0; order_locked = False
            if t.hour == 8 and 45 <= t.minute <= 55: continue

        if pos == 0 and not order_locked:
            if price >= target_long:
                margin_per = price * PV * 0.10
                contracts = max(1, int(capital * 0.3 // margin_per)) if margin_per > 0 else 1
                entry_price = price + SLIPPAGE; pos = 1; order_locked = True
            elif price <= target_short:
                margin_per = price * PV * 0.10
                contracts = max(1, int(capital * 0.3 // margin_per)) if margin_per > 0 else 1
                entry_price = price - SLIPPAGE; pos = -1; order_locked = True

    if pos != 0:
        last = df.iloc[-1]['close']
        pnl_pt = ((last - SLIPPAGE) - entry_price) if pos == 1 else (entry_price - (last + SLIPPAGE))
        capital += pnl_pt * PV * contracts; trades += 1
        if pnl_pt > 0: wins += 1; equity.append(capital)

    peak = equity[0]; mdd = 0
    for eq in equity:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > mdd: mdd = dd
    wr = (wins / trades * 100) if trades > 0 else 0
    profit = (capital - initial_capital) / initial_capital * 100
    days = max(1, (df.index[-1] - df.index[0]).days)
    cagr = ((capital / initial_capital) ** (365 / days) - 1) * 100 if capital > 0 else -100

    return {'K': K, 'stop': stop_pt, 'target': target_pt, 'trades': trades,
            'win_rate': round(wr, 2), 'profit_pct': round(profit, 2), 'cagr': round(cagr, 2),
            'mdd': round(mdd, 2), 'final_capital': round(capital)}


# ═══════════════════════════════════════════════════════════════════════
# 메인 실행
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("  AMATS 현행 ERA 시스템 방식 백테스트 + 분석 + 개선안 비교")
    print("=" * 80)

    db_path = os.path.join(workspace_root, "futures_data.db")
    df = load_futures_data(db_path)
    if df.empty:
        print("No data"); return

    print(f"  Data: {len(df)} candles ({df.index[0]} ~ {df.index[-1]})")
    data_days = (df.index[-1] - df.index[0]).days
    print(f"  Period: {data_days} days (~{data_days/30:.1f} months)")

    # ──────────────────────────────────────────────────────────────────
    # 0. SQL 버그 검증
    # ──────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  [0] _load_prev_range SQL 버그 검증: date() vs SUBSTR()")
    print("=" * 80)
    bug_rows, fix_rows = verify_sql_bug(db_path)
    print(f"  date() 결과 (현행 ERA):")
    for r in bug_rows[:5]:
        rng = (r[1] - r[2]) if r[1] and r[2] else 'N/A'
        print(f"    d={r[0]}, high={r[1]}, low={r[2]}, range={rng}")
    print(f"\n  SUBSTR() 결과 (올바른 방식):")
    for r in fix_rows[:5]:
        rng = round(r[1] - r[2], 2) if r[1] and r[2] else 'N/A'
        print(f"    d={r[0]}, high={r[1]}, low={r[2]}, range={rng}")

    if bug_rows and bug_rows[0][0] is None:
        print("\n  >>> CONFIRMED: date() returns NULL! ERA의 prev_range가 잘못 계산되고 있습니다!")
    elif bug_rows and fix_rows and len(bug_rows) != len(fix_rows):
        print(f"\n  >>> WARNING: date()={len(bug_rows)} groups vs SUBSTR()={len(fix_rows)} groups -> 다른 그룹핑!")
    else:
        print("\n  >>> date()와 SUBSTR() 결과가 동일합니다 (버그 없음).")

    # ──────────────────────────────────────────────────────────────────
    # 1. 현행 ERA 전략 — K값 스위핑 (정밀 분석)
    # ──────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  [1] 현행 ERA 전략 K값 스위핑 (손절 없음, 08:45 시간청산)")
    print("=" * 80)

    header = f"{'K':>5} | {'매매':>5} | {'승률%':>6} | {'수익률%':>9} | {'CAGR%':>7} | {'MDD%':>6} | {'손익비':>5} | {'PF':>5} | {'최대연패':>6} | {'최종자본':>14}"
    print(header)
    print("-" * len(header))

    era_results = []
    for k in np.arange(0.3, 0.85, 0.05):
        k = round(k, 2)
        r = era_current_strategy(df, k)
        if r['trades'] > 5:
            era_results.append(r)
            print(f"{r['K']:>5.2f} | {r['trades']:>5} | {r['win_rate']:>6.1f} | {r['profit_pct']:>9.2f} | {r['cagr']:>7.2f} | {r['mdd']:>6.2f} | {r['payoff_ratio']:>5.2f} | {r['profit_factor']:>5.2f} | {r['max_consec_loss']:>6} | {r['final_capital']:>14,}")

    # 최적 K값의 상세 매매 로그 (최근 10건)
    best_era = max(era_results, key=lambda x: x['profit_pct']) if era_results else None
    if best_era and best_era['trade_log']:
        print(f"\n  --- 최적 K={best_era['K']} 최근 매매 10건 ---")
        print(f"  {'일시':<18} | {'방향':>5} | {'진입가':>8} | {'청산가':>8} | {'손익pt':>7} | {'계약':>3} | {'손익금액':>12} | {'잔고':>14}")
        print("  " + "-" * 90)
        for t in best_era['trade_log'][-10:]:
            print(f"  {t['date']:<18} | {t['type']:>5} | {t['entry']:>8.2f} | {t['exit']:>8.2f} | {t['pnl_pt']:>+7.2f} | {t['contracts']:>3} | {t['pnl_money']:>+12,} | {t['capital']:>14,}")

    # ──────────────────────────────────────────────────────────────────
    # 2. 개선안 A: 고정 손절 추가
    # ──────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  [2] 개선안 A: 현행 ERA + 고정 손절 추가 (K=0.5 고정)")
    print("=" * 80)

    test_k = 0.5
    print(f"  K={test_k} 기준 — 손절(pt)에 따른 성과 변화:")
    print(f"  {'손절pt':>6} | {'매매':>5} | {'승률%':>6} | {'수익률%':>9} | {'CAGR%':>7} | {'MDD%':>6} | {'최종자본':>14}")
    print("  " + "-" * 70)
    # 비교 기준: 손절 없음
    r_base = era_current_strategy(df, test_k)
    print(f"  {'없음':>6} | {r_base['trades']:>5} | {r_base['win_rate']:>6.1f} | {r_base['profit_pct']:>9.2f} | {r_base['cagr']:>7.2f} | {r_base['mdd']:>6.2f} | {r_base['final_capital']:>14,}")
    for sp in [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]:
        r = era_with_fixed_stoploss(df, test_k, stop_pt=sp)
        print(f"  {sp:>6.1f} | {r['trades']:>5} | {r['win_rate']:>6.1f} | {r['profit_pct']:>9.2f} | {r['cagr']:>7.2f} | {r['mdd']:>6.2f} | {r['final_capital']:>14,}")

    # ──────────────────────────────────────────────────────────────────
    # 3. 개선안 B: 고정 손절 + 고정 익절
    # ──────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  [3] 개선안 B: 현행 ERA + 고정 손절 + 고정 익절 (K=0.5)")
    print("=" * 80)

    print(f"  {'손절':>5} | {'익절':>5} | {'매매':>5} | {'승률%':>6} | {'수익률%':>9} | {'CAGR%':>7} | {'MDD%':>6} | {'최종자본':>14}")
    print("  " + "-" * 75)
    for sp, tp in [(1.0, 2.0), (1.0, 3.0), (1.5, 3.0), (1.5, 4.0), (2.0, 3.0), (2.0, 4.0), (2.0, 5.0), (3.0, 5.0), (3.0, 6.0)]:
        r = era_with_stop_and_target(df, test_k, stop_pt=sp, target_pt=tp)
        print(f"  {sp:>5.1f} | {tp:>5.1f} | {r['trades']:>5} | {r['win_rate']:>6.1f} | {r['profit_pct']:>9.2f} | {r['cagr']:>7.2f} | {r['mdd']:>6.2f} | {r['final_capital']:>14,}")

    # ──────────────────────────────────────────────────────────────────
    # 4. 최적 K값별 손절/익절 조합 매트릭스
    # ──────────────────────────────────────────────────────────────────
    if best_era:
        bk = best_era['K']
        print(f"\n{'=' * 80}")
        print(f"  [4] 최적 K={bk} 기준 손절/익절 조합 매트릭스")
        print("=" * 80)

        print(f"  {'손절':>5} | {'익절':>5} | {'매매':>5} | {'승률%':>6} | {'수익률%':>9} | {'CAGR%':>7} | {'MDD%':>6} | {'CAGR/MDD':>8} | {'최종자본':>14}")
        print("  " + "-" * 85)

        matrix_results = []
        for sp in [1.0, 1.5, 2.0, 3.0]:
            for tp in [2.0, 3.0, 4.0, 5.0]:
                if tp <= sp: continue
                r = era_with_stop_and_target(df, bk, stop_pt=sp, target_pt=tp)
                ratio = r['cagr'] / max(r['mdd'], 0.1)
                matrix_results.append({**r, 'cagr_mdd': round(ratio, 2)})
                print(f"  {sp:>5.1f} | {tp:>5.1f} | {r['trades']:>5} | {r['win_rate']:>6.1f} | {r['profit_pct']:>9.2f} | {r['cagr']:>7.2f} | {r['mdd']:>6.2f} | {ratio:>8.2f} | {r['final_capital']:>14,}")

        # 없음 (현행)
        r_none = era_current_strategy(df, bk)
        ratio_none = r_none['cagr'] / max(r_none['mdd'], 0.1)
        print(f"  {'없음':>5} | {'없음':>5} | {r_none['trades']:>5} | {r_none['win_rate']:>6.1f} | {r_none['profit_pct']:>9.2f} | {r_none['cagr']:>7.2f} | {r_none['mdd']:>6.2f} | {ratio_none:>8.2f} | {r_none['final_capital']:>14,}")

    # ──────────────────────────────────────────────────────────────────
    # 5. 종합 분석 출력
    # ──────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("  [SUMMARY] 현행 시스템 문제점 분석 및 개선안")
    print("=" * 80)

    if best_era:
        print(f"""
  현행 ERA 최적 K={best_era['K']} 성과 요약:
    - 총 매매: {best_era['trades']}회
    - 승률: {best_era['win_rate']}%
    - 누적 수익률: {best_era['profit_pct']:+.2f}%
    - CAGR: {best_era['cagr']:.2f}%
    - MDD: {best_era['mdd']:.2f}%
    - 손익비 (Payoff Ratio): {best_era['payoff_ratio']:.2f}
    - Profit Factor: {best_era['profit_factor']:.2f}
    - 최대 연속 손실: {best_era['max_consec_loss']}연패
    - 최대 손실금액(MDD): {best_era['max_dd_amount']:,}원

  [문제점 진단]
    1. 손절이 전혀 없어 단일 매매 최대 손실이 무제한
    2. 익절 없이 08:45 시간청산에만 의존 -> 이미 확보한 수익을 토해내는 경우 빈발
    3. 마진 기반 최대 계약수 진입 -> 연패 시 자본 급감
    4. MDD {best_era['mdd']:.1f}%는 실전에서 심리적 한계 초과

  [개선 권고]
    1단계(즉시): 고정 손절 {2.0}pt 추가 -> MDD 억제 우선
    2단계(단기): 고정 익절 목표 추가 -> 수익 확정력 개선
    3단계(중기): ATR 기반 동적 손절/익절로 전환 -> 변동성 적응력 확보
    4단계(장기): 진입 필터(RSI/MACD/스퀴즈) 추가 -> 무효 돌파 차단
""")


if __name__ == "__main__":
    main()
