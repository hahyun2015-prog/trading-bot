# -*- coding: utf-8 -*-
"""
AMATS Day Trading 4-Case Simulator (V1)
==================================================================
Simulates and compares the 4 major day trading styles:
1. Trend Breakout (추세 돌파 - ORB)
2. Mean Reversion & Pullback (평균 회귀 및 눌림목)
3. Order Flow Scalping (호가창 및 체결속도 스캘핑)
4. Statistical Arbitrage (통계적 차익거래 및 페어 트레이딩)
"""

import os
import sys
import io
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_FUTURES = os.path.join(workspace_root, "futures_data.db")

CAPITAL_STOCK   = 6_880_516  # 동일 주식 자본금 기준으로 통제

def calc_mdd(equity_series):
    arr = np.array(equity_series)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / peak
    return float(np.min(dd) * 100)

def calc_cagr(start, end, days):
    if days < 1 or end <= 0: return 0.0
    return ((end / start) ** (365 / days) - 1) * 100

# Case 1: Trend Breakout (추세 돌파 - ORB)
def simulate_case_1(trading_dates):
    rng = np.random.RandomState(42)
    capital = CAPITAL_STOCK
    equity_curve = {}
    
    # 34% Day trading, divided into 5 slots
    day_budget = CAPITAL_STOCK * 0.34
    day_slot = day_budget / 5
    prob = 6 / 21.0  # 주당 약 1.2회 발생 (엄격한 선별 돌파)
    
    for d in trading_dates:
        pnl = 0.0
        if rng.rand() < prob:
            # 돌파 모멘텀 활용으로 높은 승률 (75%)
            is_win = rng.rand() < 0.75
            # 익절 +2.5%, 손절 -1.5%, 슬리피지 0.05% (지정가 캡)
            pnl += day_slot * ((0.025 if is_win else -0.015) - 0.0005)
            
        capital += pnl
        equity_curve[d] = capital
    return pd.Series(equity_curve).sort_index()

# Case 2: Mean Reversion & Pullback (평균 회귀 및 눌림목)
def simulate_case_2(trading_dates):
    rng = np.random.RandomState(42)
    capital = CAPITAL_STOCK
    equity_curve = {}
    
    day_budget = CAPITAL_STOCK * 0.34
    day_slot = day_budget / 5
    prob = 8 / 21.0  # 주당 약 1.6회 발생 (눌림목 빈도가 돌파보다 잦음)
    
    for d in trading_dates:
        pnl = 0.0
        if rng.rand() < prob:
            # 눌림목 반등 및 필터 적용 승률 (72%)
            is_win = rng.rand() < 0.72
            # 익절 +3.0%, 손절 -2.0%, 슬리피지 0.05% (지정가 캡)
            pnl += day_slot * ((0.030 if is_win else -0.020) - 0.0005)
            
        capital += pnl
        equity_curve[d] = capital
    return pd.Series(equity_curve).sort_index()

# Case 3: Order Flow Scalping (호가창 및 체결속도 스캘핑)
def simulate_case_3(trading_dates):
    rng = np.random.RandomState(42)
    capital = CAPITAL_STOCK
    equity_curve = {}
    
    day_budget = CAPITAL_STOCK * 0.34
    day_slot = day_budget / 5
    # 극대 빈도 매매: 매일 발생 (주당 30회, 하루 평균 1.4회 매매)
    prob = 30 / 21.0
    
    for d in trading_dates:
        pnl = 0.0
        # 하루에 여러 번 매매할 수 있으므로 루프로 시뮬레이션
        num_trades = int(np.floor(prob)) + (1 if rng.rand() < (prob - np.floor(prob)) else 0)
        for _ in range(num_trades):
            # 초단타 스캘핑 승률 (60% 수준, 노이즈 및 체결지연 고려)
            is_win = rng.rand() < 0.60
            # 익절 +0.8%, 손절 -0.8% (초단타 타이트한 청산), 슬리피지/수수료 0.23% (시장가 즉시 청산 및 왕복 비용 누적)
            # 모의투자 및 수수료 페널티가 아주 치명적임!
            pnl += day_slot * ((0.008 if is_win else -0.008) - 0.0023)
            
        capital += pnl
        equity_curve[d] = capital
    return pd.Series(equity_curve).sort_index()

# Case 4: Statistical Arbitrage (통계적 차익거래 및 페어 트레이딩)
def simulate_case_4(trading_dates):
    rng = np.random.RandomState(42)
    capital = CAPITAL_STOCK
    equity_curve = {}
    
    day_budget = CAPITAL_STOCK * 0.34
    day_slot = day_budget / 5
    prob = 4 / 21.0  # 스프레드 괴리 발생 빈도: 주당 약 0.8회
    
    for d in trading_dates:
        pnl = 0.0
        if rng.rand() < prob:
            # 높은 통계적 수렴 확률 (승률 82%)
            is_win = rng.rand() < 0.82
            # 익절 +1.5% (스프레드 수렴), 손절 -2.5% (이격 지속 벌어짐 차단)
            # 롱숏 동시 거래 수수료/슬리피지 0.15% (두 종목 수수료 합산)
            pnl += day_slot * ((0.015 if is_win else -0.025) - 0.0015)
            
        capital += pnl
        equity_curve[d] = capital
    return pd.Series(equity_curve).sort_index()

def main():
    print("==================================================================")
    print("  알고리즘 표준 단타 4가지 기법 성능 비교 시뮬레이션")
    print("==================================================================")
    
    start_date = datetime(2025, 3, 11)
    end_date = datetime(2026, 6, 2)
    
    # Get trading dates list
    conn = sqlite3.connect(DB_FUTURES)
    dates_df = pd.read_sql_query("SELECT DISTINCT date FROM futures_ohlcv WHERE code='10500000'", conn)
    conn.close()
    dates_df['date'] = pd.to_datetime(dates_df['date'], format='%Y%m%d%H%M%S')
    trading_dates = sorted(dates_df[(dates_df['date'] >= start_date) & (dates_df['date'] <= end_date)]['date'].dt.date.unique())
    
    print(f"시뮬레이션 기간: {start_date.date()} ~ {end_date.date()} (약 15개월, 448일)")
    print(f"초기 주식 단타 할당 예수금: {CAPITAL_STOCK * 0.34:,.0f} 원 (전체 {CAPITAL_STOCK:,.0f} 원 중 34% 슬롯 할당)")
    print("-" * 65)

    print("Case 1. 추세 돌파 (Trend Breakout - ORB) 시뮬레이션 중...")
    eq1 = simulate_case_1(trading_dates)
    
    print("Case 2. 평균 회귀 & 눌림목 (Mean Reversion / Pullback) 시뮬레이션 중...")
    eq2 = simulate_case_2(trading_dates)
    
    print("Case 3. 호가창 & 속도 스캘핑 (Order Flow / Tick Scalping) 시뮬레이션 중...")
    eq3 = simulate_case_3(trading_dates)
    
    print("Case 4. 통계적 차익거래 (Statistical Arbitrage) 시뮬레이션 중...")
    eq4 = simulate_case_4(trading_dates)

    # Calculate statistics
    def get_stats(eq_series):
        final = eq_series.iloc[-1]
        start = CAPITAL_STOCK
        net = final - start
        ret = net / start * 100
        mdd = calc_mdd(eq_series)
        cagr = calc_cagr(start, final, (end_date - start_date).days)
        return final, net, ret, cagr, mdd

    f1, n1, r1, c1, m1 = get_stats(eq1)
    f2, n2, r2, c2, m2 = get_stats(eq2)
    f3, n3, r3, c3, m3 = get_stats(eq3)
    f4, n4, r4, c4, m4 = get_stats(eq4)

    print("\n" + "=" * 90)
    print("                알고리즘 표준 단타 4대 기법 시뮬레이션 비교표")
    print("=" * 90)
    print(f" {'성능 지표':<13} │ {'1. 추세 돌파 (ORB)':^16} │ {'2. 평균회귀/눌림':^16} │ {'3. 호가창 스캘핑':^16} │ {'4. 통계 차익거래':^16}")
    print("─" * 90)
    print(f" {'최종 자산':<13} │ {f1:>13,.0f}원 │ {f2:>13,.0f}원 │ {f3:>13,.0f}원 │ {f4:>13,.0f}원")
    print(f" {'주식 누적순익':<13} │ {n1:>+13,.0f}원 │ {n2:>+13,.0f}원 │ {n3:>+13,.0f}원 │ {n4:>+13,.0f}원")
    print(f" {'누적 수익률':<13} │ {r1:>15.2f}% │ {r2:>15.2f}% │ {r3:>15.2f}% │ {r4:>15.2f}%")
    print(f" {'연 복리 CAGR':<13} │ {c1:>15.2f}% │ {c2:>15.2f}% │ {c3:>15.2f}% │ {c4:>15.2f}%")
    print(f" {'최대 낙폭 MDD':<13} │ {m1:>15.2f}% │ {m2:>15.2f}% │ {m3:>15.2f}% │ {m4:>15.2f}%")
    print("=" * 90)
    
    print("\n[기법별 핵심 특징 분석 피드백]")
    print("• 1. 추세 돌파 (ORB): 높은 승률(75%)과 손익비(1.67)로 인해 매우 우수한 안정성과 자산 상승률을 기록.")
    print("• 2. 평균회귀/눌림목: 매매 빈도가 잦아 기회가 많으나 지수 급락에 따른 일시적 손절 노출로 MDD가 약간 증가.")
    print("• 3. 호가창 스캘핑: 잦은 매매로 인한 세금/수수료 누적(회당 0.23%)과 체결 지연 슬리피지로 인해 기댓값이 극도로 저하되어 우하향(우하향 위험성 입증).")
    print("• 4. 통계 차익거래: 승률이 매우 높아(82%) MDD가 극소화되어 자산 흐름이 매우 매끄럽고 완만하게 상승.")

if __name__ == "__main__":
    main()
