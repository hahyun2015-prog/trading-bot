import sqlite3
import pandas as pd
import numpy as np
import os
import sys
import argparse

class KalmanFilter1D:
    def __init__(self, Q=0.0001, R=0.5):
        self.Q = Q
        self.R = R
        self.x = None
        self.P = 1.0

    def update(self, z):
        if self.x is None:
            self.x = z
            return self.x
        self.P = self.P + self.Q
        K = self.P / (self.P + self.R)
        self.x = self.x + K * (z - self.x)
        self.P = (1 - K) * self.P
        return self.x

# Constants
POINT_VALUE  = 250_000
MARGIN_RATE  = 0.10
MARGIN_CAP   = 0.30
SLIP_FEE_PT  = 0.05
INIT_CAPITAL = 50_000_000

def clean_ohlcv_outliers(df):
    """era_order_manager.py의 _clean_futures_ohlcv_outliers와 동일한 로직 — 이웃 5분봉으로 확인되지
    않는 단발성 고/저가 이상치(Kiwoom 서버 히스토리 데이터 자체의 오류 틱 포함, 2026-06-23 사례)를
    시가/종가 범위로 정정. date 오름차순 정렬된 DataFrame(open/high/low/close 필요)을 받아 반환."""
    n = len(df)
    if n < 7:
        return df
    opens = df['open'].to_numpy(dtype=float)
    highs = df['high'].to_numpy(dtype=float)
    lows = df['low'].to_numpy(dtype=float)
    closes = df['close'].to_numpy(dtype=float)
    fixed_high = highs.copy()
    fixed_low = lows.copy()
    fix_count = 0
    window = 3
    wick_pct = 0.02
    confirm_pct = 0.01
    for i in range(n):
        ref = opens[i] if opens[i] > 0 else closes[i]
        if ref <= 0:
            continue
        body_hi = max(opens[i], closes[i])
        body_lo = min(opens[i], closes[i])
        lo_i, hi_i = max(0, i - window), min(n, i + window + 1)
        neighbors = None

        wick_lo = body_lo - lows[i]
        if wick_lo > 0 and wick_lo / ref > wick_pct:
            neighbors = [v for j in range(lo_i, hi_i) if j != i for v in (lows[j], highs[j])]
            if neighbors and not any(abs(lows[i] - nv) / ref < confirm_pct for nv in neighbors):
                fixed_low[i] = body_lo
                fix_count += 1

        wick_hi = highs[i] - body_hi
        if wick_hi > 0 and wick_hi / ref > wick_pct:
            if neighbors is None:
                neighbors = [v for j in range(lo_i, hi_i) if j != i for v in (lows[j], highs[j])]
            if neighbors and not any(abs(highs[i] - nv) / ref < confirm_pct for nv in neighbors):
                fixed_high[i] = body_hi
                fix_count += 1

    if fix_count > 0:
        print(f"[이상치 필터] 이웃봉으로 확인되지 않는 단발성 고/저가 이상치 {fix_count}건을 시가/종가 범위로 정정했습니다.")
        df = df.copy()
        df['high'] = fixed_high
        df['low'] = fixed_low
    return df


def load_futures_data(code='10100000'):
    db_path = "c:\\Antigravity\\AI_T_Agent\\futures_data.db"
    if not os.path.exists(db_path):
        print(f"[-] Database not found at: {db_path}")
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    query = f"SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '{code}' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    if not df.empty:
        df = df.reset_index(drop=True)
        df = clean_ohlcv_outliers(df)
        df['date_day'] = df['date'].str[:8]
        df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        df.set_index('dt', inplace=True)
    return df

def run_production_rolling_k(df):
    # Re-implement the 20-day rolling optimization baseline to run in the same environment
    daily = df.groupby('date_day').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).reset_index()
    
    grouped_days = df.groupby('date_day')
    sorted_days = sorted(grouped_days.groups.keys())
    
    # 20-day rolling optimization to find best K-value for each day
    optimized_k_map = {}
    for i in range(len(sorted_days)):
        day_key = sorted_days[i]
        if i < 20:
            optimized_k_map[day_key] = 0.5
            continue
            
        last_20_days = sorted_days[i-20:i]
        k_candidates = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]
        best_candidate_k = 0.5
        max_profit = float('-inf')
        
        for cand_k in k_candidates:
            total_profit = 0.0
            for day in last_20_days:
                day_candles = grouped_days.get_group(day)
                if day_candles.empty: continue
                
                idx_daily = daily[daily['date_day'] == day].index[0]
                prev_day_row = daily.iloc[idx_daily - 1]
                pr = prev_day_row['high'] - prev_day_row['low']
                if pr <= 0: continue
                
                day_open_val = day_candles.iloc[0]['open']
                tgt_l_val = day_open_val + pr * cand_k
                tgt_s_val = day_open_val - pr * cand_k
                
                pos_val = 0
                entry_val = 0.0
                trade_count_val = 0
                
                for _, r in day_candles.iterrows():
                    c_high, c_low, c_close = r['high'], r['low'], r['close']
                    
                    if r['date'] == day_candles.iloc[-1]['date']:
                        if pos_val != 0:
                            pnl = (c_close - entry_val if pos_val == 1 else entry_val - c_close) - 0.10
                            total_profit += pnl
                        break
                        
                    if pos_val == 1:
                        if c_low <= entry_val - 5.0:
                            total_profit += (-5.0 - 0.10)
                            pos_val = 0
                        elif c_high >= entry_val + 10.0:
                            total_profit += (10.0 - 0.10)
                            pos_val = 0
                    elif pos_val == -1:
                        if c_high >= entry_val + 5.0:
                            total_profit += (-5.0 - 0.10)
                            pos_val = 0
                        elif c_low <= entry_val - 10.0:
                            total_profit += (10.0 - 0.10)
                            pos_val = 0
                    else:
                        if trade_count_val < 4:
                            if c_high >= tgt_l_val:
                                pos_val = 1
                                entry_val = tgt_l_val
                                trade_count_val += 1
                            elif c_low <= tgt_s_val:
                                pos_val = -1
                                entry_val = tgt_s_val
                                trade_count_val += 1
                                
            if total_profit > max_profit:
                max_profit = total_profit
                best_candidate_k = cand_k
                
        optimized_k_map[day_key] = best_candidate_k

    # Backtest Execution
    cap = float(INIT_CAPITAL)
    equity = [cap]
    pnls = []
    wins = 0
    
    first_price = df['close'].iloc[0]
    margin_per = first_price * POINT_VALUE * MARGIN_RATE
    safe_budget = INIT_CAPITAL * MARGIN_CAP
    CONTRACTS = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
    
    for i in range(1, len(sorted_days)):
        day_key = sorted_days[i]
        prev_day_key = sorted_days[i-1]
        
        prev_day_data = daily[daily['date_day'] == prev_day_key]
        if prev_day_data.empty: continue
        pr = prev_day_data.iloc[0]['high'] - prev_day_data.iloc[0]['low']
        if pr <= 0: continue
        
        K = optimized_k_map.get(day_key, 0.5)
        day_candles = grouped_days.get_group(day_key).sort_values('date')
        if day_candles.empty: continue
        
        day_open = day_candles.iloc[0]['open']
        tgt_l = day_open + pr * K
        tgt_s = day_open - pr * K
        
        pos = 0
        entry_price = 0.0
        trade_count = 0
        
        for idx, row in day_candles.iterrows():
            c_high, c_low, c_close = row['high'], row['low'], row['close']
            is_last = (idx == day_candles.index[-1])
            
            if is_last:
                if pos != 0:
                    exit_p = c_close - SLIP_FEE_PT if pos == 1 else c_close + SLIP_FEE_PT
                    gain = ((exit_p - entry_price) * pos - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                break
                
            if pos == 1:
                if c_low <= entry_price - 5.0: # Stop Loss 5pt
                    exit_p = entry_price - 5.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                elif c_high >= entry_price + 10.0: # Take Profit 10pt
                    exit_p = entry_price + 10.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            elif pos == -1:
                if c_high >= entry_price + 5.0: # Stop Loss 5pt
                    exit_p = entry_price + 5.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                elif c_low <= entry_price - 10.0: # Take Profit 10pt
                    exit_p = entry_price - 10.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            else:
                if trade_count < 4:
                    if c_high >= tgt_l:
                        pos = 1
                        entry_price = tgt_l + SLIP_FEE_PT
                        trade_count += 1
                    elif c_low <= tgt_s:
                        pos = -1
                        entry_price = tgt_s - SLIP_FEE_PT
                        trade_count += 1

    total = len(pnls)
    if total == 0: return None
    equity = np.array(equity)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (peaks - equity) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0
    
    return {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf}

def run_kalman_breakout_fair(df, Q=0.0001, R=0.5, multiplier=1.0):
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    # Run Kalman Filter
    kf = KalmanFilter1D(Q=Q, R=R)
    kf_prices = np.zeros(len(closes))
    for i in range(len(closes)):
        kf_prices[i] = kf.update(closes[i])
        
    df['kf'] = kf_prices
    df['error'] = df['close'] - df['kf']
    df['std_error'] = df['error'].rolling(window=20).std()
    df['std_error'] = df['std_error'].fillna(0.5)
    
    df['band'] = df['std_error'] * multiplier
    df['upper_band'] = df['kf'] + df['band']
    df['lower_band'] = df['kf'] - df['band']
    
    upper_bands = df['upper_band'].values
    lower_bands = df['lower_band'].values
    
    # Backtest Execution
    cap = float(INIT_CAPITAL)
    equity = [cap]
    pnls = []
    wins = 0
    
    first_price = df['close'].iloc[0]
    margin_per = first_price * POINT_VALUE * MARGIN_RATE
    safe_budget = INIT_CAPITAL * MARGIN_CAP
    CONTRACTS = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
    
    grouped_days = df.groupby('date_day')
    sorted_days = sorted(grouped_days.groups.keys())
    
    for day in sorted_days:
        day_candles = grouped_days.get_group(day).sort_values('date')
        if len(day_candles) < 5:
            continue
            
        pos = 0
        entry_price = 0.0
        trade_count = 0
        
        for idx, row in day_candles.iterrows():
            loc = df.index.get_loc(idx)
            c_high, c_low, c_close = row['high'], row['low'], row['close']
            c_upper, c_lower = upper_bands[loc], lower_bands[loc]
            is_last = (idx == day_candles.index[-1])
            
            if is_last:
                if pos != 0:
                    exit_p = c_close - SLIP_FEE_PT if pos == 1 else c_close + SLIP_FEE_PT
                    gain = ((exit_p - entry_price) * pos - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                break
                
            if pos == 1:
                # Stop Loss 5.0pt
                if c_low <= entry_price - 5.0:
                    exit_p = entry_price - 5.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                # Take Profit 10.0pt
                elif c_high >= entry_price + 10.0:
                    exit_p = entry_price + 10.0 - SLIP_FEE_PT
                    gain = ((exit_p - entry_price) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            elif pos == -1:
                # Stop Loss 5.0pt
                if c_high >= entry_price + 5.0:
                    exit_p = entry_price + 5.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
                # Take Profit 10.0pt
                elif c_low <= entry_price - 10.0:
                    exit_p = entry_price - 10.0 + SLIP_FEE_PT
                    gain = ((entry_price - exit_p) - SLIP_FEE_PT * 2) * POINT_VALUE * CONTRACTS
                    cap += gain
                    pnls.append(gain)
                    equity.append(cap)
                    wins += (gain > 0)
                    pos = 0
            else:
                if trade_count < 4:
                    if c_high >= c_upper:
                        pos = 1
                        entry_price = c_upper + SLIP_FEE_PT
                        trade_count += 1
                    elif c_low <= c_lower:
                        pos = -1
                        entry_price = c_lower - SLIP_FEE_PT
                        trade_count += 1

    total = len(pnls)
    if total == 0: return None
    equity = np.array(equity)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (peaks - equity) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0
    
    return {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf}


def run_kalman_live_replica(df, Q=0.0001, R=0.5, mult=1.0, kf_sl_mult=2.0, atr_cutoff=0.5,
                             margin_cap=0.30, reentry_k=0.5, kf_window=40, std_window=20,
                             trend_q=0.001, trend_r=1.0, max_contracts=15, point_value=250_000,
                             enable_reentry_filter=True, slip_fee_pt=0.05, commission_rate=0.000065,
                             kf_ts_trigger_mult=1.2, kf_ts_callback_mult=0.3, kf_ts_floor=0.3,
                             hard_cap=15.0,
                             slip_entry_pt=None, slip_exit_sl_pt=None,
                             slip_exit_normal_pt=None, slip_exit_force_pt=None,
                             min_std_error_entry=0.0,
                             dynamic_cap_mult=None, dynamic_cap_min=None, dynamic_cap_max=None,
                             disable_trend_filter=False, trend_bar_minutes=60,
                             tp_sigma_mult=3.0,
                             tier_mult1=2.0, tier_mult2=4.0,
                             tier_cb_frac1=0.6, tier_cb_frac2=0.4,
                             tier_lock1=2.0, tier_lock2=5.0,
                             session_range_mult=1.2,
                             force_close_hour=8, force_close_minute=45, force_close_window_min=10,
                             trend_ts_trigger_mult=None, trend_ts_callback_mult=None,
                             notrend_ts_trigger_mult=None, notrend_ts_callback_mult=None,
                             gap_guard_mult=None, realistic_gap_fill=False,
                             reset_kf_daily=False, trim_std_outliers=0,
                             entry_start_hour=9, entry_start_minute=0,
                             trend_tp_sigma_mult=None):
    """
    era_order_manager.py의 실전 주간선물 칼만 전략(update_kalman_targets / _process_day_tick)을
    최대한 동일하게 재현한 백테스트. (2026-07-01 기준 최종 라이브 로직과 일치화됨)

    진입/청산 판정은 5분봉의 고가·저가로 "그 봉 안에서 가격이 닿았는지"를 체크합니다
    (실시간 틱 감시의 근사치이며, 종가만으로 판정하지 않습니다).

    실전과 동일하게 반영한 요소:
      - 매 5분봉마다 직전 kf_window(40)개 봉으로 칼만필터를 새로 추정(재시드) -> KF가, 최근 std_window(20)봉 오차표준편차
      - 60분봉 리샘플 장기 칼만(q=trend_q, r=trend_r)으로 추세(UP/DOWN/NEUTRAL) 필터 -> 역추세 진입 차단
      - 손절 = min(max(min(kf_sl_mult * std_error, 1.2 * ATR14), max(1.5*std_error, 2.0)), 15.0)pt
        (하이브리드 동적, ATR14는 전일까지 일별 14일 롤링. 플로어는 std_error 연동형, 절대 상한 15.0pt 하드캡)
      - 익절 = KF가 ± 3*std_error (3-Sigma), 도달 전 kf_ts_trigger_mult*std_error 수익 시
        트레일링(max(kf_ts_callback_mult*std_error, kf_ts_floor) 되돌림) 가동
      - ATR14 < atr_cutoff(기본 0.5pt)면 신규 진입 차단, 진입은 09:00 이후만 허용
      - 3회 연속 손절(SL) 시 해당 거래일 신규 진입 전면 차단 (Circuit Breaker, 익절/트레일링 청산 시 카운터 리셋)
      - 강제청산: 08:45~08:55(익일 장전), 15:35~15:45 사이 당일 변동폭(고가-저가) > max(1.2*ATR14, 15.0)pt
      - 재진입 휩소 방지: 직전 청산가 기준 전일Range*reentry_k 단위 폭의 구간에서는 재진입 차단
        (실전은 이 구간폭 계산에 strategy_type과 무관하게 self.futures_best_k를 그대로 사용합니다.
         reentry_k에 현재 active_strategy.json의 best_k 값을 넣어 호출하면 가장 정확합니다.)

    상황별 차등 슬리피지 모델 (2026-07-07 도입):
      - slip_entry_pt:       돌파 진입 시 편도 슬리피지 (기본 1.5pt). 시장가 추격 진입으로 인한 불리한 체결.
      - slip_exit_sl_pt:     손절(SL) 청산 시 편도 슬리피지 (기본 3.0pt). 급변동 중 시장가 손절이므로 가장 큼.
      - slip_exit_normal_pt: 익절/트레일링 청산 시 편도 슬리피지 (기본 0.5pt). 유리한 방향 이동 중 청산.
      - slip_exit_force_pt:  강제청산(시간/변동성) 시 편도 슬리피지 (기본 2.0pt). 불리한 타이밍의 시장가 청산.
      구식 slip_fee_pt 인자는 하위호환용으로 유지: 새 인자가 모두 None이면 기존처럼 고정 상수 적용.

    저변동성 필터 + 동적 손절 상한 (2026-07-09 도입, 둘 다 기본값은 비활성화되어 기존 동작과 100% 동일):
      - min_std_error_entry: 이 값보다 std_error가 작으면 신규 진입 자체를 건너뜀 (기본 0.0=비활성).
        2025년 저변동성 구간(std_error 중앙값 0.60pt) 분석 결과, 3*std_error 익절목표가 절대 손절
        플로어(max(1.5*std_error,2.0)=2.0pt대)보다 작아 손익비가 원천적으로 불리했음이 확인되어
        이런 국면 자체를 거르는 필터로 추가.
      - dynamic_cap_mult/min/max: 지정하면 고정 hard_cap 대신
        effective_cap = clip(dynamic_cap_mult*std_error, dynamic_cap_min, dynamic_cap_max)를 사용.
        2026년 고변동성 구간(std_error 평균 5.82pt)에서는 3*std_error 익절이 고정 15pt 손절캡을
        평균 1.16배 웃돌아 손익비가 유리해졌는데, 이 비율을 변동성 규모와 무관하게 일정하게
        유지하려는 목적. dynamic_cap_mult가 None이면 기존처럼 고정 hard_cap 그대로 사용.

    추세 확인 시 트레일링 완화 (2026-07-10 도입, 기본값 None=비활성 -> 기존 동작과 100% 동일):
      - trend_ts_trigger_mult/trend_ts_callback_mult: 둘 중 하나라도 지정하면, 포지션 방향과
        장기추세(trend)가 일치할 때(LONG인데 UP, SHORT인데 DOWN)만 kf_ts_trigger_mult/
        kf_ts_callback_mult 대신 이 값을 사용. 불일치(NEUTRAL 포함)면 기존 kf_ts_* 값 그대로.
        2026-07-10 실거래 로그 분석: 활성 config(kf_ts_trigger_mult=0.3, callback_mult=0.2)의
        구조상 트레일링 발동 직후 반전되는 거래는 최소 66.7%(=0.2/0.3)의 피크 수익을 반납하도록
        설계돼 있고, 실측 승리 거래 다수가 이 이론치 부근에서 반납됨을 확인. 같은 날 LONG 4연속
        거래가 추세 지속 중 조기 트레일링 청산 -> 더 비싼 가격 재진입을 반복(재진입 갭 합산
        약 16.4pt)한 패턴을 근거로, 추세 확인 구간만 선택적으로 트레일링을 완화해보기 위해 추가.
        (2026-07-10 백테스트 결과: 전체기간/최근90일 모두 PF·MDD 악화로 기각됨 — 아래
        notrend_ts_*의 대조군으로 코드는 남겨둠)
      - notrend_ts_trigger_mult/notrend_ts_callback_mult: 정반대 방향 검증용. 포지션 방향과
        추세가 "불일치"(NEUTRAL 포함, 보유 중 추세가 역전된 경우 포함)할 때만 kf_ts_trigger_mult/
        kf_ts_callback_mult 대신 이 값을 사용 — trend_ts_*가 기각된 뒤, "추세 엣지가 없는
        구간에서 더 타이트하게 조이면 개선되는가"를 대조군(전 구간 일괄 타이트화)과 함께
        검증하기 위해 추가.

    개장 갭 추격 방지 가드 (2026-07-10 도입, 기본 None/False -> 기존 동작과 100% 동일):
      - gap_guard_mult: 진입 게이트가 열리는 순간 이미 target을 gap_guard_mult*std_error 이상
        초과해 있으면 그 진입을 건너뜀. era_order_manager.py 실거래 로그 분석(2026-07-10 확인):
        전일 종가 대비 당일 08:40 시가가 +50pt(+4.2%) 갭이었는데 Q=0.00005로 느리게 적응하는
        칼만 필터가 못 따라잡아 09:00 첫 진입이 방금 재계산된 target_long(~1187pt)보다
        37~38pt 위(1224.84pt)에서 체결됨 — DB에 08:45~15:45 구간만 데이터가 있고 그 앞뒤
        구간(야간)엔 전체 데이터셋에 걸쳐 하루도 데이터가 없어 매일 이 갭 추격 위험에 노출됨.
      - realistic_gap_fill: True면 진입 체결가를 기존 target±SLIP_ENTRY 고정폭 대신
        max(c_open, target)±SLIP_ENTRY로 근사(봉 시가 자체가 이미 target을 넘어서 있으면
        그 시가 기준 체결, 아니면 기존과 동일) — 기존 백테스트는 갭 진입도 항상 target 근처
        소폭 슬리피지로 체결된 것으로 가정해 진입비용을 과소평가하고 있었음. c_high/c_low(봉
        중간 스파이크)까지 체결가로 쓰면 정상적인 장중 돌파 진입까지 전부 봉의 최고/최저가로
        체결된 것처럼 과도하게 불리해지므로, 갭이 실제로 발생하는 지점인 시가만 반영.
        [미완성 — 2026-07-10 검증 결과 사용 보류] 전체 진입신호의 90.2%가 "시가가 target을
        이미 넘어선" 것으로 잡히는데(scratch/gap_frequency_diagnosis.py), 그중 절대다수는
        5분봉 해상도 자체의 정상적 봉간 지연(중앙값 gap/std_error=1.44)이지 진짜 이례적 갭이
        아님. gap_guard_mult는 "진입 여부"만 걸러줄 뿐 "체결가 공식"엔 문턱이 없어서, guard를
        통과한(=작은 gap의) 정상 진입들도 여전히 max(c_open,target) 체결가를 적용받아 부당하게
        불리해짐 — 결과적으로 PF가 21→0.5 수준으로 붕괴하는 오해의 소지가 큰 결과가 나온다
        (실측 확인됨, gap_guard 병용해도 해결 안 됨). 이 옵션을 실제로 쓰려면 체결가 공식
        자체에 크기 문턱(예: gap/std_error > 4.0일 때만 open 기준, 아니면 기존 target 기준)을
        먼저 넣어야 함 — 현재 구현 상태로는 사용하지 말 것.

    개장 직후 칼만 윈도우 오염 대응 (2026-07-11 도입, 기본값 False/0 -> 기존 동작과 100% 동일):
      - reset_kf_daily: True면 kf_window(40봉) 산출 시 전일 봉으로 못 넘어가도록 그날 첫 봉부터만
        사용(그래서 개장 직후엔 40봉보다 훨씬 적은 봉으로 계산됨). 실측 확인(2026-07-11):
        09:00 시점 "최근 40봉" 중 90%(36/40)가 전일 데이터였고, 그날 데이터만으로 100% 채워지는
        건 정오(12:00)였음 — kf_q=0.00005로 원래도 느리게 적응하는 필터가 이중으로 전일 수준에
        묶여 있어 개장 갭을 못 따라잡는 구조적 원인.
      - trim_std_outliers: N(정수)을 주면 std_error 계산 구간(std_window)에서 절댓값이 가장 큰
        잔차 N개를 제외하고 계산(체결가·target·추세는 영향 없음, std_error 기반 SL/TP/트레일링
        문턱만 갭 봉 하나의 왜곡 영향을 덜 받게 함). 0이면 비활성.
        (2026-07-11 검증: reset_kf_daily는 전체/최근90일/최근30일 전 구간에서 PF가 절반 수준으로
        악화돼 기각. trim_std_outliers=1은 세 구간 모두 소폭 개선돼 채택 후보.)

    진입 시작 시각 지연 (2026-07-11 도입, 기본 09:00 -> 기존과 100% 동일):
      - entry_start_hour/entry_start_minute: 신규 진입 게이트가 열리는 시각. kf_window(40봉)
        윈도우가 개장 시점엔 전일 데이터 위주(위 reset_kf_daily 설명 참조, 09:00엔 90%가 전일
        데이터, 정오는 돼야 100% 당일 데이터)라, 진입 시작을 늦춰서 이 오염 구간을 그냥 건너뛰면
        어떤지 검증하기 위해 추가.

    추세구간 익절폭 확대 (2026-07-11 도입, 기본 None -> 기존 동작과 100% 동일):
      - trend_tp_sigma_mult: 포지션 방향과 장기추세(trend)가 일치할 때만 3-Sigma 익절 목표가를
        tp_sigma_mult 대신 이 배수로 계산(더 크게 주면 목표가가 더 멀어짐 -> 트레일링이 조기
        확정시키기 전에 추세를 더 오래 타도록 유도). 불일치(NEUTRAL 포함) 시 기존 tp_sigma_mult
        그대로. trend_ts_trigger_mult(트레일링 트리거 자체를 늦추는 방식)는 이미 2026-07-10에
        기각됐는데, 그건 목표가는 그대로 두고 트레일링만 살짝 늦춘 정도라 실제로 3-Sigma
        목표가(기본 4*std_error)에 도달하기엔 부족했을 가능성이 있어, 목표가 자체를 늘리는
        이 방식을 별도로 검증한다.
    """
    n = len(df)
    if n < kf_window + 10:
        return None

    opens = df['open'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    closes = df['close'].values.astype(float)
    day_keys = df['date_day'].values
    dt_index = df.index

    # --- 일별 ATR 사전 계산: 전일까지의 데이터만 사용, era_order_manager.py의 update_futures_dynamic_sl_tp와
    #     동일한 1차원 칼만필터(Q=0.002, R=0.2) 기반 ATR 산출 (단순 rolling(14).mean()이 아님 — 라이브와 동일하게 맞춤) ---
    daily = df.groupby('date_day').agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last')).reset_index()
    daily['prev_close'] = daily['close'].shift(1)
    tr = np.maximum(daily['high'] - daily['low'],
                     np.maximum((daily['high'] - daily['prev_close']).abs(),
                                (daily['low'] - daily['prev_close']).abs())).fillna(daily['high'] - daily['low'])
    kf_atr_path = np.empty(len(tr))
    kf_atr, P_atr, Q_atr, R_atr = None, 1.0, 0.002, 0.2
    for j, tr_val in enumerate(tr.values):
        if kf_atr is None:
            kf_atr = tr_val
        else:
            P_atr = P_atr + Q_atr
            K_atr = P_atr / (P_atr + R_atr)
            kf_atr = kf_atr + K_atr * (tr_val - kf_atr)
            P_atr = (1 - K_atr) * P_atr
        kf_atr_path[j] = kf_atr
    daily['range'] = daily['high'] - daily['low']
    atr_map, prev_range_map = {}, {}
    day_list = daily['date_day'].tolist()
    for i, dkey in enumerate(day_list):
        if i == 0:
            atr_map[dkey] = 2.0
            prev_range_map[dkey] = 0.0
        else:
            v = kf_atr_path[i - 1]
            atr_map[dkey] = float(v) if pd.notna(v) and v > 0 else 2.0
            prev_range_map[dkey] = float(daily['range'].iloc[i - 1])

    # --- 장기 추세필터용 N분 버킷 ID (epoch 시간 기준 정수 버킷) ---
    # (2026-07-09: era_order_manager.py의 update_kalman_targets()가 실제로는 15분봉으로
    #  리샘플링하는데 함수 docstring엔 "60분봉"으로 잘못 적혀 있어 혼선이 있었음 —
    #  trend_bar_minutes로 파라미터화해서 라이브(15분)와 정확히 맞춰 검증 가능하게 함)
    bucket60 = (df.index.astype('datetime64[ns]').astype(np.int64) // 10**9 // (trend_bar_minutes * 60))

    MARGIN_RATE, INIT_CAPITAL = 0.10, 50_000_000
    # 상황별 차등 슬리피지 (새 인자가 하나라도 지정되면 차등모델 활성화, 아니면 기존 고정 상수)
    _any_new_slip = any(v is not None for v in (slip_entry_pt, slip_exit_sl_pt, slip_exit_normal_pt, slip_exit_force_pt))
    SLIP_ENTRY      = slip_entry_pt       if slip_entry_pt       is not None else (1.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_SL    = slip_exit_sl_pt     if slip_exit_sl_pt     is not None else (3.0 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_NORMAL= slip_exit_normal_pt if slip_exit_normal_pt is not None else (0.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_FORCE = slip_exit_force_pt  if slip_exit_force_pt  is not None else (2.0 if _any_new_slip else slip_fee_pt)

    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0

    first_price = closes[0]
    margin_per = first_price * point_value * MARGIN_RATE
    safe_budget = INIT_CAPITAL * margin_cap
    contracts = max(1, min(max_contracts, int(safe_budget // margin_per))) if margin_per > 0 else 1

    pos, entry_price, peak_price = 0, 0.0, 0.0
    day_high, day_low, cur_day = -np.inf, np.inf, None
    day_start_idx = 0
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    tp_long, tp_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0
    consec_losses = 0

    def reentry_ok(direction, price):
        exit_price = last_long_exit if direction == 1 else last_short_exit
        if exit_price <= 0:
            return True
        unit = prev_range * reentry_k
        if unit <= 0:
            unit = 0.5
        if direction == 1:
            lo, hi = exit_price - unit * 0.5, exit_price + unit * 0.2
        else:
            lo, hi = exit_price - unit * 0.2, exit_price + unit * 0.5
        return not (lo < price < hi)

    for i in range(n):
        day_key = day_keys[i]
        ts = dt_index[i]

        if day_key != cur_day:
            cur_day = day_key
            day_start_idx = i
            day_high, day_low = highs[i], lows[i]
            atr14 = atr_map.get(day_key, 2.0)
            prev_range = prev_range_map.get(day_key, 0.0)
            consec_losses = 0  # 신규 거래일 시작 시 연속손실 카운터 리셋 (실전 세션 리셋과 동일)
            # 실전은 매일 08:40 주간 세션 리셋 시 직전 청산가를 0으로 초기화함(era_order_manager.py
            # _do_daily_reset) — 휩소 필터 구간이 날짜를 넘어 영구히 남지 않도록 동일하게 반영
            last_long_exit, last_short_exit = 0.0, 0.0
        else:
            day_high = max(day_high, highs[i])
            day_low = min(day_low, lows[i])

        hour, minute = ts.hour, ts.minute
        force_close = (hour == force_close_hour and
                        force_close_minute <= minute <= force_close_minute + force_close_window_min)
        # 장마감 전 무조건 강제청산 (era_order_manager.py와 동일화, 2026-07-24 — 기존엔 변동폭이
        # session_range_threshold를 넘을 때만 청산했으나, 오버나잇 갭이 손절선을 그냥 건너뛰는
        # 사례(2026-06-11→12, 의도한 캡 9.29pt인데 실현 -114.53pt) 실측 확인돼 조건 없이 항상
        # 청산하도록 변경. 분기별 백테스트로 7개 구간 전부 baseline 대비 악화 없음을 검증함.
        vol_force_close = (hour == 15 and 35 <= minute <= 45)

        # --- 칼만 타점/추세 재추정 (직전 i-1까지의 데이터로, 매 새 봉마다 갱신) ---
        # reset_kf_daily=True면 window_start가 전일로 못 넘어가도록 day_start_idx에서 clip되고,
        # 개장 직후엔 최소 2봉만 있어도 계산(전일 수준에 안 묶임). False면 기존과 완전히 동일하게
        # i>=kf_window 게이트 + 고정폭 window_start=i-kf_window 그대로 사용.
        if reset_kf_daily:
            window_start = max(day_start_idx, i - kf_window)
            enough_data = (i - window_start) >= 2
        else:
            window_start = i - kf_window
            enough_data = i >= kf_window
        if enough_data:
            window_closes = closes[window_start:i]
            x, P = None, 1.0
            kf_path = np.empty(len(window_closes))
            for j, z in enumerate(window_closes):
                if x is None:
                    x = z
                else:
                    P = P + Q
                    K = P / (P + R)
                    x = x + K * (z - x)
                    P = (1 - K) * P
                kf_path[j] = x
            kf_price = kf_path[-1]
            errs = window_closes - kf_path
            std_slice = errs[-std_window:]
            if trim_std_outliers > 0 and len(std_slice) > trim_std_outliers:
                order = np.argsort(np.abs(std_slice))
                std_slice = std_slice[order[:-trim_std_outliers]]
            std_error = np.std(std_slice)
            if not np.isfinite(std_error) or std_error <= 0:
                std_error = 0.5
            band = std_error * mult
            target_long, target_short = kf_price + band, kf_price - band
            tp_long, tp_short = kf_price + tp_sigma_mult * std_error, kf_price - tp_sigma_mult * std_error

            lb_start = max(0, i - 300)
            wb, wc = bucket60[lb_start:i], closes[lb_start:i]
            trend = "NEUTRAL"
            if len(wb) >= 5:
                rev_b, rev_c = wb[::-1], wc[::-1]
                _, first_idx = np.unique(rev_b, return_index=True)
                long_closes = rev_c[first_idx]
                if len(long_closes) >= 5:
                    xl, Pl = None, 1.0
                    kf_long_path = np.empty(len(long_closes))
                    for j, zl in enumerate(long_closes):
                        if xl is None:
                            xl = zl
                        else:
                            Pl = Pl + trend_q
                            Kl = Pl / (Pl + trend_r)
                            xl = xl + Kl * (zl - xl)
                            Pl = (1 - Kl) * Pl
                        kf_long_path[j] = xl
                    slope = kf_long_path[-1] - kf_long_path[-2]
                    trend = "UP" if slope > 0.01 else ("DOWN" if slope < -0.01 else "NEUTRAL")

        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]

        if pos != 0:
            sl_floor = max(1.5 * std_error, 2.0)
            sl_limit = max(min(kf_sl_mult * std_error, 1.2 * atr14), sl_floor)
            if dynamic_cap_mult is not None:
                effective_cap = dynamic_cap_mult * std_error
                if dynamic_cap_min is not None:
                    effective_cap = max(effective_cap, dynamic_cap_min)
                if dynamic_cap_max is not None:
                    effective_cap = min(effective_cap, dynamic_cap_max)
            else:
                effective_cap = hard_cap
            sl_limit = min(sl_limit, effective_cap)  # 절대적인 최대 손절폭 상한(Hard Cap, 고정 또는 동적)
            exit_price, is_sl, is_force = None, False, False

            # 추세 확인(포지션 방향과 trend 일치) 시에만 트레일링 트리거/콜백을 trend_ts_* 값으로 교체.
            # 둘 다 None이면 기존 kf_ts_trigger_mult/kf_ts_callback_mult 그대로 -> 기존 동작과 100% 동일.
            _trend_confirmed = (pos == 1 and trend == "UP") or (pos == -1 and trend == "DOWN")
            eff_trigger_mult = trend_ts_trigger_mult if (_trend_confirmed and trend_ts_trigger_mult is not None) else kf_ts_trigger_mult
            eff_callback_mult = trend_ts_callback_mult if (_trend_confirmed and trend_ts_callback_mult is not None) else kf_ts_callback_mult
            if not _trend_confirmed:
                if notrend_ts_trigger_mult is not None:
                    eff_trigger_mult = notrend_ts_trigger_mult
                if notrend_ts_callback_mult is not None:
                    eff_callback_mult = notrend_ts_callback_mult

            # 추세 확인 시에만 3-Sigma 목표가를 trend_tp_sigma_mult로 확대 (기본 None -> 기존과 동일)
            if _trend_confirmed and trend_tp_sigma_mult is not None:
                eff_tp_long = kf_price + trend_tp_sigma_mult * std_error
                eff_tp_short = kf_price - trend_tp_sigma_mult * std_error
            else:
                eff_tp_long, eff_tp_short = tp_long, tp_short

            if pos == 1:
                peak_price = max(peak_price, c_high)
                if force_close or vol_force_close:
                    exit_price = c_close
                    is_force = True
                elif (c_low - entry_price) <= -sl_limit:
                    exit_price, is_sl = entry_price - sl_limit, True
                elif c_high >= eff_tp_long:
                    exit_price = eff_tp_long
                elif (peak_price - entry_price) >= eff_trigger_mult * std_error:
                    # 계단식 Lock-In (2026-07-09: era_order_manager.py 실거래 트레일링 로직과 동일화 —
                    # 기존엔 단일단계 트레일링만 구현되어 있어 실거래 계단식 이익보전과 불일치했음)
                    max_pnl = peak_price - entry_price
                    # (2026-07-15 수정) trigger_mult가 작으면 tier 발동폭이 tier_lock보다 작아져, 그 이익폭에
                    # 아직 도달 못한 상태에서 ts_price가 entry+lock으로 뛰어올라 c_low와 자명하게 교차 ->
                    # 실제로 가본 적 없는 가격에 즉시 청산되는 버그가 있었음(era_order_manager.py와 동일 수정).
                    ts_tier2 = max(tier_mult2 * eff_trigger_mult * std_error, tier_lock2)
                    ts_tier1 = max(tier_mult1 * eff_trigger_mult * std_error, tier_lock1)
                    active_cb_mult = eff_callback_mult
                    if max_pnl >= ts_tier2:
                        active_cb_mult = eff_callback_mult * tier_cb_frac2
                    elif max_pnl >= ts_tier1:
                        active_cb_mult = eff_callback_mult * tier_cb_frac1
                    ts_price = peak_price - max(active_cb_mult * std_error, max(kf_ts_floor, 0.5))
                    if max_pnl >= ts_tier2:
                        ts_price = max(ts_price, entry_price + tier_lock2)
                    elif max_pnl >= ts_tier1:
                        ts_price = max(ts_price, entry_price + tier_lock1)
                    if c_low <= ts_price:
                        exit_price = ts_price
            else:
                peak_price = min(peak_price, c_low)
                if force_close or vol_force_close:
                    exit_price = c_close
                    is_force = True
                elif (entry_price - c_high) <= -sl_limit:
                    exit_price, is_sl = entry_price + sl_limit, True
                elif c_low <= eff_tp_short:
                    exit_price = eff_tp_short
                elif (entry_price - peak_price) >= eff_trigger_mult * std_error:
                    max_pnl = entry_price - peak_price
                    ts_tier2 = max(tier_mult2 * eff_trigger_mult * std_error, tier_lock2)
                    ts_tier1 = max(tier_mult1 * eff_trigger_mult * std_error, tier_lock1)
                    active_cb_mult = eff_callback_mult
                    if max_pnl >= ts_tier2:
                        active_cb_mult = eff_callback_mult * tier_cb_frac2
                    elif max_pnl >= ts_tier1:
                        active_cb_mult = eff_callback_mult * tier_cb_frac1
                    ts_price = peak_price + max(active_cb_mult * std_error, max(kf_ts_floor, 0.5))
                    if max_pnl >= ts_tier2:
                        ts_price = min(ts_price, entry_price - tier_lock2)
                    elif max_pnl >= ts_tier1:
                        ts_price = min(ts_price, entry_price - tier_lock1)
                    if c_high >= ts_price:
                        exit_price = ts_price

            if exit_price is not None:
                # 상황별 차등 슬리피지: 청산유형에 따라 편도 슬리피지 결정
                exit_slip = SLIP_EXIT_SL if is_sl else (SLIP_EXIT_FORCE if is_force else SLIP_EXIT_NORMAL)
                raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                commission_cost = entry_price * point_value * commission_rate * 2 * contracts  # 왕복(편도 x2)
                gain = (raw_pnl - exit_slip) * point_value * contracts - commission_cost
                cap += gain
                equity.append(cap)
                pnls.append(gain)
                wins += int(gain > 0)
                if is_sl:
                    consec_losses += 1
                else:
                    consec_losses = 0
                if pos == 1:
                    last_long_exit = exit_price
                else:
                    last_short_exit = exit_price
                pos, entry_price, peak_price = 0, 0.0, 0.0
            continue

        # --- 신규 진입 (entry_start_hour:entry_start_minute 이후, 기본 09:00 -> 기존과 동일.
        #     ATR 컷오프, 3연속손실 서킷브레이커, 추세역행/휩소 필터) ---
        if force_close or vol_force_close or (hour, minute) < (entry_start_hour, entry_start_minute) or i < kf_window:
            continue
        if consec_losses >= 3:
            continue
        if atr14 < atr_cutoff:
            continue
        if std_error < min_std_error_entry:  # 저변동성 국면 필터 (기본 0.0=비활성)
            continue

        if c_high >= target_long:
            open_gap = max(c_open - target_long, 0.0)  # 봉 시가 자체가 target을 이미 넘어선 폭(갭)
            if gap_guard_mult is not None and open_gap > gap_guard_mult * std_error:
                pass  # 갭으로 target을 너무 크게 넘어선 진입은 건너뜀
            elif not disable_trend_filter and trend == "DOWN":
                continue
            elif not enable_reentry_filter or reentry_ok(1, target_long):
                fill = (max(c_open, target_long) + SLIP_ENTRY) if realistic_gap_fill else (target_long + SLIP_ENTRY)
                pos, entry_price, peak_price = 1, fill, fill
        elif c_low <= target_short:
            open_gap = max(target_short - c_open, 0.0)
            if gap_guard_mult is not None and open_gap > gap_guard_mult * std_error:
                pass  # 갭으로 target을 너무 크게 넘어선 진입은 건너뜀
            elif not disable_trend_filter and trend == "UP":
                continue
            elif not enable_reentry_filter or reentry_ok(-1, target_short):
                fill = (min(c_open, target_short) - SLIP_ENTRY) if realistic_gap_fill else (target_short - SLIP_ENTRY)
                pos, entry_price, peak_price = -1, fill, fill

    total = len(pnls)
    if total == 0:
        return None
    equity_arr = np.array(equity)
    peaks = np.maximum.accumulate(equity_arr)
    drawdowns = (peaks - equity_arr) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0

    # 손익비 진단용: 원화 손익을 계약수*포인트가치로 나눠 pt 환산 (수수료/슬리피지 반영된 실현 pt)
    wins_list = [p for p in pnls if p > 0]
    losses_list = [p for p in pnls if p < 0]
    denom = point_value * contracts
    avg_win_pt = (sum(wins_list) / len(wins_list) / denom) if wins_list else 0.0
    avg_loss_pt = (sum(losses_list) / len(losses_list) / denom) if losses_list else 0.0
    loss_win_ratio = (abs(avg_loss_pt) / avg_win_pt) if avg_win_pt > 0 else None

    return {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf,
            'contracts': contracts, 'avg_win_pt': avg_win_pt, 'avg_loss_pt': avg_loss_pt,
            'loss_win_ratio': loss_win_ratio}


def run_chandelier_live_replica(df, Q=0.0001, R=0.5, mult=1.0, atr_cutoff=0.5,
                                 margin_cap=0.30, reentry_k=0.5, kf_window=40, std_window=20,
                                 trend_q=0.001, trend_r=1.0, max_contracts=15, point_value=250_000,
                                 enable_reentry_filter=True, slip_fee_pt=0.05, commission_rate=0.000065,
                                 chandelier_mult=0.3, chandelier_hard_cap=60.0,
                                 slip_entry_pt=None, slip_exit_sl_pt=None,
                                 slip_exit_normal_pt=None, slip_exit_force_pt=None,
                                 min_std_error_entry=0.0,
                                 disable_trend_filter=False, trend_bar_minutes=15,
                                 consecutive_loss_limit=5,
                                 force_close_hour=8, force_close_minute=45, force_close_window_min=10,
                                 trim_std_outliers=0,
                                 entry_start_hour=9, entry_start_minute=0,
                                 entry_end_hour=None, entry_end_minute=0,
                                 trend_completed_bars_only=False, trend_slope_threshold=0.01,
                                 return_trades=False,
                                 session_range_cap_mult=None, session_range_cap_min_bars=6,
                                 eod_close_unconditional=True, session_range_mult=1.0,
                                 dynamic_sizing=False,
                                 regime_filter_enabled=False,
                                 profit_lock_enabled=False, profit_lock_trigger_pt=8.0,
                                 profit_lock_mult=0.10, profit_lock_be_buffer_pt=1.0,
                                 profit_lock_be_move_trigger_pt=None, profit_lock_be_stage_buffer_pt=0.0,
                                 reentry_pullback_mult=0.5, reentry_breakout_mult=0.2,
                                 hard_stop_enabled=False, hard_stop_se_mult=1.5, hard_stop_pt=None,
                                 margin_per_contract=None, margin_rate=None):
    """
    era_order_manager.py의 실전 주간선물 "샹들리에 청산"(2026-07-15 도입, futures_strategy_type=
    "chandelier")을 재현한 백테스트. run_kalman_live_replica와 진입측(칼만 타점/장기추세필터/
    ATR컷오프/재진입휩소방지)은 완전히 동일하고, 청산 로직만 다르다:

      - 샹들리에 청산은 별도의 고정/동적 손절(sl_limit)과 3-Sigma 익절을 쓰지 않고, 진입 후
        고점(LONG)/저점(SHORT) 대비 dist = min(chandelier_mult * ATR14, chandelier_hard_cap)만큼
        되돌리면 단일 공식으로 청산한다 (era_order_manager.py의 sl_limit=inf 처리와 동일 원리,
        즉 이 하나의 트레일링 스탑이 손절과 익절을 겸함).
      - 장기 추세필터(역추세 진입 차단)는 chandelier에도 그대로 적용됨(era_order_manager.py의
        use_trend_filter = is_kalman or is_chandelier).
      - 연속손실 서킷브레이커 한도는 실전 config.json 기본값(consecutive_loss_limit=5)을 그대로
        인자화함(run_kalman_live_replica는 이 값이 3으로 하드코딩돼 있어 현재 config와 어긋나
        있었음 — 별도 함수라 여기선 정확히 맞춤).
      - 장마감 전 무조건 강제청산(2026-07-24 반영)은 동일하게 적용.

    eod_close_unconditional (2026-07-25 추가, 기본 True=2026-07-24 반영분과 동일):
      - True(기본): 15:35~15:45 사이엔 포지션이 있으면 조건 없이 무조건 청산 (현재 실전 상태).
      - False: 2026-07-24 개선 이전의 원래 방식으로 되돌려서, 당일 변동폭이
        max(session_range_mult * ATR14, 15.0)를 넘을 때만 청산 (조건부, 구버전 재현용).
        오버나잇 갭 방지 개선을 껐을 때와 켰을 때를 같은 샹들리에 청산 엔진 위에서
        비교하기 위해 추가.

    return_trades (2026-07-27 추가, 기본 False=기존 동작과 100% 동일):
      - True면 반환 dict에 'trade_log' 키가 추가되어, 각 거래의 진입/청산 시각·가격·방향·손익(pt)을
        딕셔너리 리스트로 담는다. 짧은 구간(예: 최근 1주일)을 사람이 직접 대조 확인할 때 사용.

    session_range_cap_mult (2026-07-27 추가, 기본 None=기존 동작과 100% 동일):
      - dist 계산 시 사용하는 ATR14는 "전일까지의 일봉"으로 계산되는 지연 지표라서, 어제
        이전에 변동성이 컸으면 오늘 실제 흐름과 무관하게 dist가 크게 유지된다(2026-07-27
        선물매매_점검보고서 1.1절: 당일 레인지 40.64pt의 62%에 달하는 dist 때문에 +22.4pt
        평가익을 전부 반납한 사례).
      - None(기본): 추가 제한 없음 — dist = min(chandelier_mult*ATR14, chandelier_hard_cap) 그대로.
      - 값 지정 시: 오늘 세션이 session_range_cap_min_bars(기본 6=30분)만큼 진행된 뒤부터,
        dist를 session_range_cap_mult * (오늘 지금까지의 세션 레인지)로 추가 상한한다.
        즉 dist = min(chandelier_mult*ATR14, chandelier_hard_cap, session_range_cap_mult*session_range_so_far).
        어제 이전 변동성이 아무리 커도, 오늘 실제로 그만큼 움직이지 않았다면 트레일링 폭이
        오늘 흐름을 벗어나 과도하게 넓어지지 않도록 하는 안전장치. 값을 낮출수록(예: 0.4)
        더 타이트해진다.

    entry_end_hour / entry_end_minute (2026-07-27 추가, 기본 None=기존 동작과 100% 동일):
      - None(기본): 진입 종료시각 제한 없음 — era_order_manager.py의 현재 실전 동작과 동일하게
        09:10 이후라면 장마감 직전이라도 신규 진입이 허용된다.
      - 값 지정 시: (hour, minute) >= (entry_end_hour, entry_end_minute)이면 신규 진입 차단.
        기존 포지션의 청산/트레일링은 이 게이트와 무관하게 계속 동작한다. "장마감 무조건청산"
        (15:35~15:45)이 도입된 뒤로는 늦은 진입일수록 트레일링이 작동할 시간 자체가 없어
        강제청산으로 끝날 확률이 높아지는데, 그 구간을 잘라내는 효과를 검증하기 위해 추가.

    dynamic_sizing (2026-07-25 추가, 기본 False=기존 동작과 100% 동일):
      - False(기본): era_order_manager.py:4677-4679의 실제 계약수 산정 공식과 달리,
        계약수를 백테스트 시작 시점(첫 5분봉 가격) 기준으로 딱 한 번만 계산해서 끝까지
        고정한다 — 계좌가 불어나도 계약수를 늘리지 않는 보수적 근사치.
      - True: era_order_manager.py와 동일하게, 매 진입 시점마다 qty = clip(int(cap *
        margin_cap / margin_per), 1, max_contracts)로 "그 시점의 누적 자본금" 기준
        계약수를 재계산한다(margin_per = 진입가 * point_value * MARGIN_RATE). 즉 수익이
        나서 계좌가 커지면 다음 진입부터 계약수도 늘어나는 실제 복리 효과를 반영한다.

    regime_filter_enabled / profit_lock_* (2026-07-30 도입, 기본 False=기존 동작과 100% 동일):
      - regime_filter_enabled=True면 장기추세가 확실히 UP일 때만 LONG, DOWN일 때만 SHORT
        진입을 허용한다(기존 역추세 차단은 반대방향만 막았으나, 이건 NEUTRAL/횡보 진입까지 차단).
      - profit_lock_enabled=True면 era_order_manager.py의 _apply_profit_lock을 재현한다.
        1단계(선택, be_move_trigger_pt): 미실현 최대이익(MFE)이 이 값 도달 시 손절선을
        본전±be_stage_buffer_pt로 끌어올린다. 2단계(trigger_pt): MFE가 trigger_pt(기본 8)
        도달 시 트레일링 폭을 profit_lock_mult*ATR14로 좁히고 손절선을 본전±be_buffer_pt로 잠근다.
    """
    n = len(df)
    if n < kf_window + 10:
        return None

    opens = df['open'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    closes = df['close'].values.astype(float)
    day_keys = df['date_day'].values
    dt_index = df.index

    daily = df.groupby('date_day').agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last')).reset_index()
    daily['prev_close'] = daily['close'].shift(1)
    tr = np.maximum(daily['high'] - daily['low'],
                     np.maximum((daily['high'] - daily['prev_close']).abs(),
                                (daily['low'] - daily['prev_close']).abs())).fillna(daily['high'] - daily['low'])
    kf_atr_path = np.empty(len(tr))
    kf_atr, P_atr, Q_atr, R_atr = None, 1.0, 0.002, 0.2
    for j, tr_val in enumerate(tr.values):
        if kf_atr is None:
            kf_atr = tr_val
        else:
            P_atr = P_atr + Q_atr
            K_atr = P_atr / (P_atr + R_atr)
            kf_atr = kf_atr + K_atr * (tr_val - kf_atr)
            P_atr = (1 - K_atr) * P_atr
        kf_atr_path[j] = kf_atr
    daily['range'] = daily['high'] - daily['low']
    atr_map, prev_range_map = {}, {}
    day_list = daily['date_day'].tolist()
    for i, dkey in enumerate(day_list):
        if i == 0:
            atr_map[dkey] = 2.0
            prev_range_map[dkey] = 0.0
        else:
            v = kf_atr_path[i - 1]
            atr_map[dkey] = float(v) if pd.notna(v) and v > 0 else 2.0
            prev_range_map[dkey] = float(daily['range'].iloc[i - 1])

    bucket60 = (df.index.astype('datetime64[ns]').astype(np.int64) // 10**9 // (trend_bar_minutes * 60))

    MARGIN_RATE, INIT_CAPITAL = 0.10, 50_000_000

    def _margin_per(price):
        # 증거금 = 기준가격 x 승수 x 요율.
        # (2026-08-04 1차) 하루치 실측만 보고 '계약당 고정액'으로 오판했으나, 여러 날을
        # 보면 계약당 반환액이 758만~1,214만원으로 움직인다. 하루 안에서 일정했던 건
        # 기준가격이 그날 안 바뀌었기 때문이고, 실제로는 20% x 기준가격 x 승수다
        # (20% x 1036.28pt x 50,000 = 10,362,800 vs 실측 10,360,560, 오차 0.02%).
        # 공식 위탁증거금률 19.8%. 기존 0.10은 실제의 절반이라 dynamic_sizing에서
        # 계약수가 2.11배 빠르게 늘어 복리 결과가 낙관 편향된다.
        # margin_rate 미지정 시 MARGIN_RATE(0.10)로 기존 동작을 유지한다.
        if margin_per_contract is not None and margin_per_contract > 0:
            return margin_per_contract
        return price * point_value * (margin_rate if margin_rate is not None else MARGIN_RATE)
    _any_new_slip = any(v is not None for v in (slip_entry_pt, slip_exit_sl_pt, slip_exit_normal_pt, slip_exit_force_pt))
    SLIP_ENTRY      = slip_entry_pt       if slip_entry_pt       is not None else (1.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_SL    = slip_exit_sl_pt     if slip_exit_sl_pt     is not None else (3.0 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_NORMAL= slip_exit_normal_pt if slip_exit_normal_pt is not None else (0.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_FORCE = slip_exit_force_pt  if slip_exit_force_pt  is not None else (2.0 if _any_new_slip else slip_fee_pt)

    cap = float(INIT_CAPITAL)
    equity, equity_days, pnls, wins = [cap], [], [], 0

    first_price = closes[0]
    margin_per = _margin_per(first_price)
    safe_budget = INIT_CAPITAL * margin_cap
    contracts = max(1, min(max_contracts, int(safe_budget // margin_per))) if margin_per > 0 else 1
    pos_contracts = contracts  # dynamic_sizing=True면 매 진입마다 재계산, 아니면 위 고정값 그대로

    pos, entry_price, peak_price = 0, 0.0, 0.0
    entry_std_error = 0.5   # (2026-08-04) 하드 초기손절용 — 진입 시점 std_error 스냅샷
    entry_time = None
    trade_log = []
    day_high, day_low, cur_day = -np.inf, np.inf, None
    day_start_idx = 0
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0
    consec_losses = 0
    contracts_log = []  # dynamic_sizing 진단용: 실제 체결된 계약수 이력

    def reentry_ok(direction, price):
        # (2026-08-04) 밴드 계수를 인자화. 기존 하드코딩은 pullback=0.5 / breakout=0.2였고,
        # 이 breakout 계수(0.2)가 좁아 '청산가에서 조금만 더 밀리면 돌파로 인정'됐다.
        # 2026-08-04 실매매 13:09 SHORT가 청산가 대비 6.75pt 아래(≈0.8*unit)에서 재진입해
        # -25.87pt를 냈다. 기본값은 기존과 같으므로 지정하지 않으면 동작이 바뀌지 않는다.
        exit_price = last_long_exit if direction == 1 else last_short_exit
        if exit_price <= 0:
            return True
        unit = prev_range * reentry_k
        if unit <= 0:
            unit = 0.5
        if direction == 1:
            lo, hi = exit_price - unit * reentry_pullback_mult, exit_price + unit * reentry_breakout_mult
        else:
            lo, hi = exit_price - unit * reentry_breakout_mult, exit_price + unit * reentry_pullback_mult
        return not (lo < price < hi)

    for i in range(n):
        day_key = day_keys[i]
        ts = dt_index[i]

        if day_key != cur_day:
            cur_day = day_key
            day_start_idx = i
            day_high, day_low = highs[i], lows[i]
            atr14 = atr_map.get(day_key, 2.0)
            prev_range = prev_range_map.get(day_key, 0.0)
            consec_losses = 0
            last_long_exit, last_short_exit = 0.0, 0.0
        else:
            day_high = max(day_high, highs[i])
            day_low = min(day_low, lows[i])

        hour, minute = ts.hour, ts.minute
        force_close = (hour == force_close_hour and
                        force_close_minute <= minute <= force_close_minute + force_close_window_min)
        if eod_close_unconditional:
            vol_force_close = (hour == 15 and 35 <= minute <= 45)
        else:
            session_range_threshold = max(session_range_mult * atr14, 15.0)
            vol_force_close = (hour == 15 and 35 <= minute <= 45) and (day_high - day_low > session_range_threshold)

        window_start = i - kf_window
        enough_data = i >= kf_window
        if enough_data:
            window_closes = closes[window_start:i]
            x, P = None, 1.0
            kf_path = np.empty(len(window_closes))
            for j, z in enumerate(window_closes):
                if x is None:
                    x = z
                else:
                    P = P + Q
                    K = P / (P + R)
                    x = x + K * (z - x)
                    P = (1 - K) * P
                kf_path[j] = x
            kf_price = kf_path[-1]
            errs = window_closes - kf_path
            std_slice = errs[-std_window:]
            if trim_std_outliers > 0 and len(std_slice) > trim_std_outliers:
                order = np.argsort(np.abs(std_slice))
                std_slice = std_slice[order[:-trim_std_outliers]]
            std_error = np.std(std_slice)
            if not np.isfinite(std_error) or std_error <= 0:
                std_error = 0.5
            band = std_error * mult
            target_long, target_short = kf_price + band, kf_price - band

            lb_start = max(0, i - 300)
            wb, wc = bucket60[lb_start:i], closes[lb_start:i]
            trend = "NEUTRAL"
            if len(wb) >= 5:
                rev_b, rev_c = wb[::-1], wc[::-1]
                uniq_b, first_idx = np.unique(rev_b, return_index=True)
                long_closes = rev_c[first_idx]
                # (2026-07-27 추가) trend_completed_bars_only=True면 아직 다 차지 않은 최신
                # 15분 버킷을 추세판정에서 제외한다. 기본 False는 실전/기존과 100% 동일한 동작
                # (미완성 버킷을 그대로 써서, 5분마다 비교 대상 마지막 값이 바뀌는 리페인팅 발생).
                if trend_completed_bars_only and len(uniq_b) >= 2:
                    bars_per_bucket = max(1, trend_bar_minutes // 5)
                    if np.count_nonzero(wb == uniq_b[-1]) < bars_per_bucket:
                        long_closes = long_closes[:-1]
                if len(long_closes) >= 5:
                    xl, Pl = None, 1.0
                    kf_long_path = np.empty(len(long_closes))
                    for j, zl in enumerate(long_closes):
                        if xl is None:
                            xl = zl
                        else:
                            Pl = Pl + trend_q
                            Kl = Pl / (Pl + trend_r)
                            xl = xl + Kl * (zl - xl)
                            Pl = (1 - Kl) * Pl
                        kf_long_path[j] = xl
                    slope = kf_long_path[-1] - kf_long_path[-2]
                    _thr = trend_slope_threshold
                    trend = "UP" if slope > _thr else ("DOWN" if slope < -_thr else "NEUTRAL")

        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]

        if pos != 0:
            exit_price, is_force = None, False
            dist = min(chandelier_mult * atr14, chandelier_hard_cap)
            if session_range_cap_mult is not None and (i - day_start_idx) >= session_range_cap_min_bars:
                session_range_so_far = day_high - day_low
                if session_range_so_far > 0:
                    dist = min(dist, session_range_cap_mult * session_range_so_far)

            if pos == 1:
                peak_price = max(peak_price, c_high)
                eff_dist, pl_floor = dist, None
                if profit_lock_enabled:
                    mfe = peak_price - entry_price
                    if profit_lock_be_move_trigger_pt is not None and mfe >= profit_lock_be_move_trigger_pt:
                        pl_floor = entry_price + profit_lock_be_stage_buffer_pt
                    elif hard_stop_enabled:
                        # (2026-08-04) 하드 초기손절 — 본전이동 도달 '이전' 구간 전용.
                        # era_order_manager.py의 _apply_profit_lock과 동일한 분기 구조.
                        _hs = hard_stop_pt if (hard_stop_pt and hard_stop_pt > 0) else hard_stop_se_mult * entry_std_error
                        if _hs and _hs > 0:
                            pl_floor = entry_price - _hs
                    if mfe >= profit_lock_trigger_pt:
                        eff_dist = min(eff_dist, profit_lock_mult * atr14)
                        pl_floor = entry_price + profit_lock_be_buffer_pt
                stop_price = peak_price - eff_dist
                if pl_floor is not None:
                    stop_price = max(stop_price, pl_floor)
                if force_close or vol_force_close:
                    exit_price = c_close
                    is_force = True
                elif c_low <= stop_price:
                    exit_price = stop_price
            else:
                peak_price = min(peak_price, c_low)
                eff_dist, pl_floor = dist, None
                if profit_lock_enabled:
                    mfe = entry_price - peak_price
                    if profit_lock_be_move_trigger_pt is not None and mfe >= profit_lock_be_move_trigger_pt:
                        pl_floor = entry_price - profit_lock_be_stage_buffer_pt
                    elif hard_stop_enabled:
                        _hs = hard_stop_pt if (hard_stop_pt and hard_stop_pt > 0) else hard_stop_se_mult * entry_std_error
                        if _hs and _hs > 0:
                            pl_floor = entry_price + _hs
                    if mfe >= profit_lock_trigger_pt:
                        eff_dist = min(eff_dist, profit_lock_mult * atr14)
                        pl_floor = entry_price - profit_lock_be_buffer_pt
                stop_price = peak_price + eff_dist
                if pl_floor is not None:
                    stop_price = min(stop_price, pl_floor)
                if force_close or vol_force_close:
                    exit_price = c_close
                    is_force = True
                elif c_high >= stop_price:
                    exit_price = stop_price

            if exit_price is not None:
                exit_slip = SLIP_EXIT_FORCE if is_force else SLIP_EXIT_NORMAL
                raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                commission_cost = entry_price * point_value * commission_rate * 2 * pos_contracts
                gain = (raw_pnl - exit_slip) * point_value * pos_contracts - commission_cost
                cap += gain
                equity.append(cap)
                equity_days.append(day_key)
                pnls.append(gain)
                wins += int(gain > 0)
                # 샹들리에는 별도 손절 플래그가 없으므로, era_order_manager.py와 동일하게
                # 실현손익 부호로 연속손실 카운터를 판단한다
                if gain < 0:
                    consec_losses += 1
                else:
                    consec_losses = 0
                if pos == 1:
                    last_long_exit = exit_price
                else:
                    last_short_exit = exit_price
                if return_trades:
                    trade_log.append({
                        'entry_time': entry_time, 'exit_time': dt_index[i],
                        'direction': 'LONG' if pos == 1 else 'SHORT',
                        'entry_price': entry_price, 'exit_price': exit_price,
                        'pnl_pt': raw_pnl - exit_slip, 'is_force': is_force,
                        'contracts': pos_contracts, 'gain_krw': gain,
                    })
                pos, entry_price, peak_price = 0, 0.0, 0.0
            continue

        if force_close or vol_force_close or (hour, minute) < (entry_start_hour, entry_start_minute) or i < kf_window:
            continue
        if entry_end_hour is not None and (hour, minute) >= (entry_end_hour, entry_end_minute):
            continue
        if consec_losses >= consecutive_loss_limit:
            continue
        if atr14 < atr_cutoff:
            continue
        if std_error < min_std_error_entry:
            continue

        if c_high >= target_long:
            if not disable_trend_filter and trend == "DOWN":
                continue
            elif regime_filter_enabled and trend != "UP":
                continue
            elif not enable_reentry_filter or reentry_ok(1, target_long):
                fill = target_long + SLIP_ENTRY
                pos, entry_price, peak_price = 1, fill, fill
                entry_std_error = std_error
                entry_time = dt_index[i]
                if dynamic_sizing:
                    m_per = _margin_per(fill)
                    pos_contracts = max(1, min(max_contracts, int((cap * margin_cap) / m_per))) if m_per > 0 else 1
                else:
                    pos_contracts = contracts
                contracts_log.append(pos_contracts)
        elif c_low <= target_short:
            if not disable_trend_filter and trend == "UP":
                continue
            elif regime_filter_enabled and trend != "DOWN":
                continue
            elif not enable_reentry_filter or reentry_ok(-1, target_short):
                fill = target_short - SLIP_ENTRY
                pos, entry_price, peak_price = -1, fill, fill
                entry_std_error = std_error
                entry_time = dt_index[i]
                if dynamic_sizing:
                    m_per = _margin_per(fill)
                    pos_contracts = max(1, min(max_contracts, int((cap * margin_cap) / m_per))) if m_per > 0 else 1
                else:
                    pos_contracts = contracts
                contracts_log.append(pos_contracts)

    total = len(pnls)
    if total == 0:
        return None
    equity_arr = np.array(equity)
    peaks = np.maximum.accumulate(equity_arr)
    drawdowns = (peaks - equity_arr) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0

    # dynamic_sizing=True면 거래마다 계약수가 달라지므로, 각 거래를 그 거래 당시 계약수로
    # 나눠 pt 환산해야 정확하다(고정 denom을 쓰면 뒤로 갈수록 계약수가 커져 왜곡됨)
    pt_equiv = [p / (point_value * c) for p, c in zip(pnls, contracts_log)]
    wins_pt = [v for v in pt_equiv if v > 0]
    losses_pt = [v for v in pt_equiv if v < 0]
    avg_win_pt = (sum(wins_pt) / len(wins_pt)) if wins_pt else 0.0
    avg_loss_pt = (sum(losses_pt) / len(losses_pt)) if losses_pt else 0.0
    loss_win_ratio = (abs(avg_loss_pt) / avg_win_pt) if avg_win_pt > 0 else None

    worst_loss_pt = min(losses_pt) if losses_pt else 0.0
    final_contracts = contracts_log[-1] if contracts_log else contracts
    avg_contracts = (sum(contracts_log) / len(contracts_log)) if contracts_log else contracts

    result = {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf,
              'contracts': contracts, 'avg_win_pt': avg_win_pt, 'avg_loss_pt': avg_loss_pt,
              'loss_win_ratio': loss_win_ratio, 'worst_loss_pt': worst_loss_pt,
              'final_capital': cap, 'final_contracts': final_contracts, 'avg_contracts': avg_contracts,
              'equity': equity[1:], 'equity_days': equity_days}
    if return_trades:
        result['trade_log'] = trade_log
    return result


def run_kalman_night_replica(df, Q=0.0001, R=0.5, mult=1.0, kf_sl_mult=5.0, atr_cutoff=0.5,
                              margin_cap=0.30, reentry_k=0.5, kf_window=40, std_window=20,
                              trend_q=0.001, trend_r=1.0, max_contracts=15, point_value=250_000,
                              enable_reentry_filter=True, slip_fee_pt=0.05, commission_rate=0.000065,
                              hard_cap=15.0,
                              slip_entry_pt=None, slip_exit_sl_pt=None,
                              slip_exit_normal_pt=None, slip_exit_force_pt=None):
    """
    era_order_manager.py의 야간선물 칼만 전략(_process_night_tick, 18:00 진입 -> 익일 04:45 청산)을
    run_kalman_live_replica()와 동일한 방식(5분봉 고가/저가 터치 근사)으로 재현한 백테스트.

    주간과 손절/익절/트레일링/추세필터/휩소방지 공식은 100% 동일(라이브에서도 같은 self.futures_kf_sl_mult,
    self.futures_atr_14, self.futures_best_k를 공유). 다른 점:
      - 세션 시간대: 18:00~익일 04:45 (자정을 가로지름 -> "야간 세션 키"를 따로 둬서 다룸)
      - 강제청산: 04:45~04:55만 있음 (주간의 15:35~15:45 변동폭 강제청산 같은 건 야간엔 없음)
      - ATR/전일Range 기준일: 야간 세션은 그날 저녁에 시작하므로, 그날 "당일"(주간 세션이 이미 끝난) 데이터까지
        포함해서 계산 (주간은 "전일까지"만 사용하는 것과 대비됨 — era_order_manager.py의 15분 주기 ATR
        재계산이 그날 18:00 이전에 그날 일봉을 반영하는 것과 동일하게 맞춤)
      - 직전 청산가/연속손실 카운터는 매 18:00 야간 세션 시작 시 리셋(주간은 매일 08:40에 리셋,
        era_order_manager.py _do_daily_reset 라인 1217~1242 참조) — 자정을 넘어가도 리셋되지 않음
    """
    n = len(df)
    if n < kf_window + 10:
        return None

    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    closes = df['close'].values.astype(float)
    day_keys = df['date_day'].values
    dt_index = df.index

    daily = df.groupby('date_day').agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last')).reset_index()
    daily['prev_close'] = daily['close'].shift(1)
    tr = np.maximum(daily['high'] - daily['low'],
                     np.maximum((daily['high'] - daily['prev_close']).abs(),
                                (daily['low'] - daily['prev_close']).abs())).fillna(daily['high'] - daily['low'])
    kf_atr_path = np.empty(len(tr))
    kf_atr, P_atr, Q_atr, R_atr = None, 1.0, 0.002, 0.2
    for j, tr_val in enumerate(tr.values):
        if kf_atr is None:
            kf_atr = tr_val
        else:
            P_atr = P_atr + Q_atr
            K_atr = P_atr / (P_atr + R_atr)
            kf_atr = kf_atr + K_atr * (tr_val - kf_atr)
            P_atr = (1 - K_atr) * P_atr
        kf_atr_path[j] = kf_atr
    daily['range'] = daily['high'] - daily['low']
    day_list = daily['date_day'].tolist()

    # 야간은 그날 저녁(18:00)에 시작 -> 그날 자체의 ATR/Range가 이미 확정되어 있으므로 인덱스를
    # -1 시프트하지 않고 당일(i) 값을 그대로 사용 (주간 atr_map/prev_range_map은 i-1을 씀)
    night_atr_map, night_prev_range_map = {}, {}
    for i, dkey in enumerate(day_list):
        v = kf_atr_path[i]
        night_atr_map[dkey] = float(v) if pd.notna(v) and v > 0 else 2.0
        night_prev_range_map[dkey] = float(daily['range'].iloc[i])

    # 직전 거래일 매핑 (자정을 넘긴 00:00~04:59 구간을 "전날 저녁에 시작한 야간 세션"으로 묶기 위함)
    prev_trading_day = {day_list[i]: (day_list[i - 1] if i > 0 else None) for i in range(len(day_list))}

    bucket60 = (df.index.astype('datetime64[ns]').astype(np.int64) // 10**9 // 3600)

    MARGIN_RATE, INIT_CAPITAL = 0.10, 50_000_000
    _any_new_slip = any(v is not None for v in (slip_entry_pt, slip_exit_sl_pt, slip_exit_normal_pt, slip_exit_force_pt))
    SLIP_ENTRY      = slip_entry_pt       if slip_entry_pt       is not None else (1.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_SL    = slip_exit_sl_pt     if slip_exit_sl_pt     is not None else (3.0 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_NORMAL= slip_exit_normal_pt if slip_exit_normal_pt is not None else (0.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_FORCE = slip_exit_force_pt  if slip_exit_force_pt  is not None else (2.0 if _any_new_slip else slip_fee_pt)

    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0

    first_price = closes[0]
    margin_per = first_price * point_value * MARGIN_RATE
    safe_budget = INIT_CAPITAL * margin_cap
    contracts = max(1, min(max_contracts, int(safe_budget // margin_per))) if margin_per > 0 else 1

    pos, entry_price, peak_price = 0, 0.0, 0.0
    cur_night_key = None
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    tp_long, tp_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0
    consec_losses = 0

    def reentry_ok(direction, price):
        exit_price = last_long_exit if direction == 1 else last_short_exit
        if exit_price <= 0:
            return True
        unit = prev_range * reentry_k
        if unit <= 0:
            unit = 0.5
        if direction == 1:
            lo, hi = exit_price - unit * 0.5, exit_price + unit * 0.2
        else:
            lo, hi = exit_price - unit * 0.2, exit_price + unit * 0.5
        return not (lo < price < hi)

    for i in range(n):
        ts = dt_index[i]
        hour, minute = ts.hour, ts.minute
        is_night_session = (hour >= 18) or (hour < 5)
        if not is_night_session:
            continue

        # 자정을 넘긴 새벽(0~4시대)은 "전날 저녁에 시작한 야간 세션"으로 귀속
        night_key = day_keys[i] if hour >= 18 else prev_trading_day.get(day_keys[i])
        if night_key is None:
            continue

        if night_key != cur_night_key:
            cur_night_key = night_key
            atr14 = night_atr_map.get(night_key, 2.0)
            prev_range = night_prev_range_map.get(night_key, 0.0)
            consec_losses = 0  # 18:00 야간 세션 시작 시 연속손실 카운터 리셋 (실전과 동일)
            last_long_exit, last_short_exit = 0.0, 0.0  # 18:00 시작 시 직전 청산가 리셋 (실전과 동일)

        force_close = (hour == 4 and 45 <= minute <= 55)

        # --- 칼만 타점/추세 재추정 (직전 i-1까지의 데이터로, 매 새 봉마다 갱신) ---
        if i >= kf_window:
            window_closes = closes[i - kf_window:i]
            x, P = None, 1.0
            kf_path = np.empty(kf_window)
            for j, z in enumerate(window_closes):
                if x is None:
                    x = z
                else:
                    P = P + Q
                    K = P / (P + R)
                    x = x + K * (z - x)
                    P = (1 - K) * P
                kf_path[j] = x
            kf_price = kf_path[-1]
            errs = window_closes - kf_path
            std_error = np.std(errs[-std_window:])
            if not np.isfinite(std_error) or std_error <= 0:
                std_error = 0.5
            band = std_error * mult
            target_long, target_short = kf_price + band, kf_price - band
            tp_long, tp_short = kf_price + 3.0 * std_error, kf_price - 3.0 * std_error

            lb_start = max(0, i - 300)
            wb, wc = bucket60[lb_start:i], closes[lb_start:i]
            trend = "NEUTRAL"
            if len(wb) >= 5:
                rev_b, rev_c = wb[::-1], wc[::-1]
                _, first_idx = np.unique(rev_b, return_index=True)
                long_closes = rev_c[first_idx]
                if len(long_closes) >= 5:
                    xl, Pl = None, 1.0
                    kf_long_path = np.empty(len(long_closes))
                    for j, zl in enumerate(long_closes):
                        if xl is None:
                            xl = zl
                        else:
                            Pl = Pl + trend_q
                            Kl = Pl / (Pl + trend_r)
                            xl = xl + Kl * (zl - xl)
                            Pl = (1 - Kl) * Pl
                        kf_long_path[j] = xl
                    slope = kf_long_path[-1] - kf_long_path[-2]
                    trend = "UP" if slope > 0.01 else ("DOWN" if slope < -0.01 else "NEUTRAL")

        c_high, c_low, c_close = highs[i], lows[i], closes[i]

        if pos != 0:
            sl_floor = max(0.5 * atr14, 2.0)
            sl_limit = max(min(kf_sl_mult * std_error, 1.2 * atr14), sl_floor)
            sl_limit = min(sl_limit, hard_cap)  # 절대적인 최대 손절폭 상한(Hard Cap)
            exit_price, is_sl, is_force = None, False, False

            if pos == 1:
                peak_price = max(peak_price, c_high)
                if force_close:
                    exit_price = c_close
                    is_force = True
                elif (c_low - entry_price) <= -sl_limit:
                    exit_price, is_sl = entry_price - sl_limit, True
                elif c_high >= tp_long:
                    exit_price = tp_long
                elif (peak_price - entry_price) >= 1.5 * std_error:
                    ts_price = peak_price - 0.5 * std_error
                    if c_low <= ts_price:
                        exit_price = ts_price
            else:
                peak_price = min(peak_price, c_low)
                if force_close:
                    exit_price = c_close
                    is_force = True
                elif (entry_price - c_high) <= -sl_limit:
                    exit_price, is_sl = entry_price + sl_limit, True
                elif c_low <= tp_short:
                    exit_price = tp_short
                elif (entry_price - peak_price) >= 1.5 * std_error:
                    ts_price = peak_price + 0.5 * std_error
                    if c_high >= ts_price:
                        exit_price = ts_price

            if exit_price is not None:
                exit_slip = SLIP_EXIT_SL if is_sl else (SLIP_EXIT_FORCE if is_force else SLIP_EXIT_NORMAL)
                raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                commission_cost = entry_price * point_value * commission_rate * 2 * contracts  # 왕복(편도 x2)
                gain = (raw_pnl - exit_slip) * point_value * contracts - commission_cost
                cap += gain
                equity.append(cap)
                pnls.append(gain)
                wins += int(gain > 0)
                if is_sl:
                    consec_losses += 1
                else:
                    consec_losses = 0
                if pos == 1:
                    last_long_exit = exit_price
                else:
                    last_short_exit = exit_price
                pos, entry_price, peak_price = 0, 0.0, 0.0
            continue

        # --- 신규 진입 (force_close 구간 제외, ATR 컷오프, 3연속손실 서킷브레이커, 추세역행/휩소 필터) ---
        if force_close or i < kf_window:
            continue
        if consec_losses >= 3:
            continue
        if atr14 < atr_cutoff:
            continue

        if c_high >= target_long:
            if trend == "DOWN":
                continue
            if not enable_reentry_filter or reentry_ok(1, target_long):
                pos, entry_price, peak_price = 1, target_long + SLIP_ENTRY, target_long + SLIP_ENTRY
        elif c_low <= target_short:
            if trend == "UP":
                continue
            if not enable_reentry_filter or reentry_ok(-1, target_short):
                pos, entry_price, peak_price = -1, target_short - SLIP_ENTRY, target_short - SLIP_ENTRY

    total = len(pnls)
    if total == 0:
        return None
    equity_arr = np.array(equity)
    peaks = np.maximum.accumulate(equity_arr)
    drawdowns = (peaks - equity_arr) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0

    return {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf, 'contracts': contracts}


def run_kalman_live_replica_oc(df, Q=0.0001, R=0.5, mult=1.0, kf_sl_mult=3.4, atr_cutoff=0.5,
                                margin_cap=0.30, reentry_k=0.5, kf_window=40, std_window=20,
                                trend_q=0.001, trend_r=1.0, max_contracts=15, point_value=250_000,
                                enable_reentry_filter=True, slip_fee_pt=0.05, commission_rate=0.000065,
                                hard_cap=15.0,
                                slip_entry_pt=None, slip_exit_sl_pt=None,
                                slip_exit_normal_pt=None, slip_exit_force_pt=None):
    """
    run_kalman_live_replica와 전략/리스크 규칙은 동일하지만, 5분봉의 고가·저가(틱 근사, "터치"로 간주)를
    쓰지 않고 각 5분봉의 "시가"와 "종가" 두 지점만 실제 틱처럼 순서대로 관찰한다고 가정합니다.
    (실시간 틱 피드 없이 5분에 한 번씩만 가격을 확인/판단하는 운용 방식의 보수적 근사)

    진입/손절/익절/트레일링 모두 시가를 먼저 확인하고, 그 다음 종가를 확인하는 2단계로만 판정합니다.
    봉 중간에 그 가격을 스쳐 지나간 경우(고가/저가만 닿고 시가·종가는 안 닿은 경우)는 체결로 보지 않습니다.
    """
    n = len(df)
    if n < kf_window + 10:
        return None

    opens = df['open'].values.astype(float)
    closes = df['close'].values.astype(float)
    day_keys = df['date_day'].values
    dt_index = df.index

    daily = df.groupby('date_day').agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last')).reset_index()
    daily['prev_close'] = daily['close'].shift(1)
    tr = np.maximum(daily['high'] - daily['low'],
                     np.maximum((daily['high'] - daily['prev_close']).abs(),
                                (daily['low'] - daily['prev_close']).abs())).fillna(daily['high'] - daily['low'])
    kf_atr_path = np.empty(len(tr))
    kf_atr, P_atr, Q_atr, R_atr = None, 1.0, 0.002, 0.2
    for j, tr_val in enumerate(tr.values):
        if kf_atr is None:
            kf_atr = tr_val
        else:
            P_atr = P_atr + Q_atr
            K_atr = P_atr / (P_atr + R_atr)
            kf_atr = kf_atr + K_atr * (tr_val - kf_atr)
            P_atr = (1 - K_atr) * P_atr
        kf_atr_path[j] = kf_atr
    daily['range'] = daily['high'] - daily['low']
    atr_map, prev_range_map = {}, {}
    day_list = daily['date_day'].tolist()
    for i, dkey in enumerate(day_list):
        if i == 0:
            atr_map[dkey] = 2.0
            prev_range_map[dkey] = 0.0
        else:
            v = kf_atr_path[i - 1]
            atr_map[dkey] = float(v) if pd.notna(v) and v > 0 else 2.0
            prev_range_map[dkey] = float(daily['range'].iloc[i - 1])

    bucket60 = (df.index.astype('datetime64[ns]').astype(np.int64) // 10**9 // 3600)

    MARGIN_RATE, INIT_CAPITAL = 0.10, 50_000_000
    _any_new_slip = any(v is not None for v in (slip_entry_pt, slip_exit_sl_pt, slip_exit_normal_pt, slip_exit_force_pt))
    SLIP_ENTRY      = slip_entry_pt       if slip_entry_pt       is not None else (1.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_SL    = slip_exit_sl_pt     if slip_exit_sl_pt     is not None else (3.0 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_NORMAL= slip_exit_normal_pt if slip_exit_normal_pt is not None else (0.5 if _any_new_slip else slip_fee_pt)
    SLIP_EXIT_FORCE = slip_exit_force_pt  if slip_exit_force_pt  is not None else (2.0 if _any_new_slip else slip_fee_pt)
    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0

    first_price = closes[0]
    margin_per = first_price * point_value * MARGIN_RATE
    safe_budget = INIT_CAPITAL * margin_cap
    contracts = max(1, min(max_contracts, int(safe_budget // margin_per))) if margin_per > 0 else 1

    pos, entry_price, peak_price = 0, 0.0, 0.0
    day_high, day_low, cur_day = -np.inf, np.inf, None
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    tp_long, tp_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0
    consec_losses = 0

    def reentry_ok(direction, price):
        exit_price = last_long_exit if direction == 1 else last_short_exit
        if exit_price <= 0:
            return True
        unit = prev_range * reentry_k
        if unit <= 0:
            unit = 0.5
        if direction == 1:
            lo, hi = exit_price - unit * 0.5, exit_price + unit * 0.2
        else:
            lo, hi = exit_price - unit * 0.2, exit_price + unit * 0.5
        return not (lo < price < hi)

    for i in range(n):
        day_key = day_keys[i]
        ts = dt_index[i]
        o_price, c_price = opens[i], closes[i]

        if day_key != cur_day:
            cur_day = day_key
            day_high, day_low = max(o_price, c_price), min(o_price, c_price)
            atr14 = atr_map.get(day_key, 2.0)
            prev_range = prev_range_map.get(day_key, 0.0)
            consec_losses = 0
            last_long_exit, last_short_exit = 0.0, 0.0
        else:
            day_high = max(day_high, o_price, c_price)
            day_low = min(day_low, o_price, c_price)

        hour, minute = ts.hour, ts.minute
        force_close = (hour == 8 and 45 <= minute <= 55)
        # 장마감 전 무조건 강제청산 (era_order_manager.py와 동일화, 2026-07-24 — 오버나잇 갭이
        # 손절선을 그냥 건너뛰는 사례 실측 확인돼 조건(변동폭>15pt) 없이 항상 청산하도록 변경)
        vol_force_close = (hour == 15 and 35 <= minute <= 45)

        if i >= kf_window:
            window_closes = closes[i - kf_window:i]
            x, P = None, 1.0
            kf_path = np.empty(kf_window)
            for j, z in enumerate(window_closes):
                if x is None:
                    x = z
                else:
                    P = P + Q
                    K = P / (P + R)
                    x = x + K * (z - x)
                    P = (1 - K) * P
                kf_path[j] = x
            kf_price = kf_path[-1]
            errs = window_closes - kf_path
            std_error = np.std(errs[-std_window:])
            if not np.isfinite(std_error) or std_error <= 0:
                std_error = 0.5
            band = std_error * mult
            target_long, target_short = kf_price + band, kf_price - band
            tp_long, tp_short = kf_price + 3.0 * std_error, kf_price - 3.0 * std_error

            lb_start = max(0, i - 300)
            wb, wc = bucket60[lb_start:i], closes[lb_start:i]
            trend = "NEUTRAL"
            if len(wb) >= 5:
                rev_b, rev_c = wb[::-1], wc[::-1]
                _, first_idx = np.unique(rev_b, return_index=True)
                long_closes = rev_c[first_idx]
                if len(long_closes) >= 5:
                    xl, Pl = None, 1.0
                    kf_long_path = np.empty(len(long_closes))
                    for j, zl in enumerate(long_closes):
                        if xl is None:
                            xl = zl
                        else:
                            Pl = Pl + trend_q
                            Kl = Pl / (Pl + trend_r)
                            xl = xl + Kl * (zl - xl)
                            Pl = (1 - Kl) * Pl
                        kf_long_path[j] = xl
                    slope = kf_long_path[-1] - kf_long_path[-2]
                    trend = "UP" if slope > 0.01 else ("DOWN" if slope < -0.01 else "NEUTRAL")

        # --- 봉 안에서 "시가 -> 종가" 두 지점만 순서대로 관찰 ---
        for price in (o_price, c_price):
            if pos != 0:
                sl_floor = max(0.5 * atr14, 2.0)
                sl_limit = max(min(kf_sl_mult * std_error, 1.2 * atr14), sl_floor)
                sl_limit = min(sl_limit, hard_cap)  # 절대적인 최대 손절폭 상한(Hard Cap)
                exit_price, is_sl, is_force = None, False, False
                if pos == 1:
                    peak_price = max(peak_price, price)
                    if force_close or vol_force_close:
                        exit_price = price
                        is_force = True
                    elif (price - entry_price) <= -sl_limit:
                        exit_price, is_sl = price, True
                    elif price >= tp_long:
                        exit_price = price
                    elif (peak_price - entry_price) >= 1.5 * std_error and price <= peak_price - 0.5 * std_error:
                        exit_price = price
                else:
                    peak_price = min(peak_price, price)
                    if force_close or vol_force_close:
                        exit_price = price
                        is_force = True
                    elif (entry_price - price) <= -sl_limit:
                        exit_price, is_sl = price, True
                    elif price <= tp_short:
                        exit_price = price
                    elif (entry_price - peak_price) >= 1.5 * std_error and price >= peak_price + 0.5 * std_error:
                        exit_price = price

                if exit_price is not None:
                    exit_slip = SLIP_EXIT_SL if is_sl else (SLIP_EXIT_FORCE if is_force else SLIP_EXIT_NORMAL)
                    raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                    commission_cost = entry_price * point_value * commission_rate * 2 * contracts  # 왕복(편도 x2)
                    gain = (raw_pnl - exit_slip) * point_value * contracts - commission_cost
                    cap += gain
                    equity.append(cap)
                    pnls.append(gain)
                    wins += int(gain > 0)
                    consec_losses = consec_losses + 1 if is_sl else 0
                    if pos == 1:
                        last_long_exit = exit_price
                    else:
                        last_short_exit = exit_price
                    pos, entry_price, peak_price = 0, 0.0, 0.0
                continue

            if force_close or vol_force_close or hour < 9 or i < kf_window or atr14 < atr_cutoff or consec_losses >= 3:
                continue
            if price >= target_long:
                if trend == "DOWN":
                    continue
                if not enable_reentry_filter or reentry_ok(1, price):
                    pos, entry_price, peak_price = 1, price, price
            elif price <= target_short:
                if trend == "UP":
                    continue
                if not enable_reentry_filter or reentry_ok(-1, price):
                    pos, entry_price, peak_price = -1, price, price

    total = len(pnls)
    if total == 0:
        return None
    equity_arr = np.array(equity)
    peaks = np.maximum.accumulate(equity_arr)
    drawdowns = (peaks - equity_arr) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    profit_pct = (cap - INIT_CAPITAL) / INIT_CAPITAL * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0

    return {'trades': total, 'win_rate': win_rate, 'profit_pct': profit_pct, 'mdd': max_mdd, 'pf': pf, 'contracts': contracts}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Kalman Filter Strategy Backtester")
    parser.add_argument('--code', type=str, default='10100000', help="Futures code to backtest (default: 10100000)")
    parser.add_argument('--q', type=float, default=0.0001, help="Kalman Filter process variance Q (default: 0.0001)")
    parser.add_argument('--r', type=float, default=0.5, help="Kalman Filter measurement variance R (default: 0.5)")
    parser.add_argument('--mult', type=float, default=1.0, help="Kalman Band Multiplier (default: 1.0)")
    parser.add_argument('--kf-sl-mult', type=float, default=5.0, help="하이브리드 동적 손절 배수 (era_order_manager.py / config.json 현재 기본값 5.0)")
    parser.add_argument('--atr-cutoff', type=float, default=0.5, help="ATR14 진입 차단 기준 (pt, 기본 0.5)")
    parser.add_argument('--margin-cap', type=float, default=0.30, help="가용예수금 대비 마진 캡 비율 (era_order_manager.py 기본값 0.30)")
    parser.add_argument('--reentry-k', type=float, default=0.5, help="재진입 휩소방지 구간폭 산정용 K값 (active_strategy.json의 best_k와 동일하게 넣으면 가장 정확)")
    parser.add_argument('--simple', action='store_true', help="단순화 버전(run_kalman_breakout_fair, 고정 5/10pt)으로 실행")
    parser.add_argument('--price-mode', choices=['tick', 'oc'], default='tick',
                         help="tick: 5분봉 고가/저가로 터치 판정(실시간 틱 근사, 기본값) | oc: 시가->종가 두 지점만 순차 관찰 (틱 데이터 없는 경우 근사)")
    parser.add_argument('--compare', action='store_true', help="Compare with production baseline")
    parser.add_argument('--disable-reentry-filter', action='store_true',
                         help="휩소 방지(재진입 차단) 필터를 비활성화하고 백테스트 (A/B 비교용)")
    parser.add_argument('--session', choices=['day', 'night'], default='day',
                         help="day: 주간선물(09:00~익일08:45) | night: 야간선물(18:00~익일04:45) (기본값: day)")
    parser.add_argument('--slip-fee-pt', type=float, default=0.05,
                         help="편도 슬리피지 가정치 (pt, 기본 0.05pt — 하위호환용. --realistic-slip 또는 개별 --slip-* 인자 사용 시 무시됨)")
    parser.add_argument('--commission-rate', type=float, default=0.000065,
                         help="편도 위탁수수료율 (계약가치 대비, 기본 0.000065=0.0065%% — bqa/cost_analysis.py 키움 실제 비용구조와 동일, 왕복은 x2 적용됨)")
    parser.add_argument('--no-commission', action='store_true', help="수수료를 0으로 두고 백테스트 (순수 슬리피지만 비교하고 싶을 때)")
    # 상황별 차등 슬리피지 인자 (2026-07-07 신규 도입)
    parser.add_argument('--realistic-slip', action='store_true',
                         help="상황별 차등 슬리피지 기본 프로필 활성화 (진입 1.5pt / SL청산 3.0pt / 익절 0.5pt / 강제청산 2.0pt). 개별 --slip-entry-pt 등으로 오버라이드 가능")
    parser.add_argument('--slip-entry-pt', type=float, default=None,
                         help="돌파 진입 시 편도 슬리피지 (pt, 기본 1.5pt). 시장가 추격 진입")
    parser.add_argument('--slip-exit-sl-pt', type=float, default=None,
                         help="손절(SL) 청산 시 편도 슬리피지 (pt, 기본 3.0pt). 급변동 중 시장가 손절")
    parser.add_argument('--slip-exit-normal-pt', type=float, default=None,
                         help="익절/트레일링 청산 시 편도 슬리피지 (pt, 기본 0.5pt). 유리한 방향 청산")
    parser.add_argument('--slip-exit-force-pt', type=float, default=None,
                         help="강제청산(시간/변동성) 시 편도 슬리피지 (pt, 기본 2.0pt). 불리한 타이밍 시장가 청산")

    args = parser.parse_args()
    enable_reentry_filter = not args.disable_reentry_filter
    commission_rate = 0.0 if args.no_commission else args.commission_rate

    # 상황별 슬리피지 인자 해석
    slip_kw = {}
    if args.realistic_slip or any(v is not None for v in (args.slip_entry_pt, args.slip_exit_sl_pt, args.slip_exit_normal_pt, args.slip_exit_force_pt)):
        slip_kw = {
            'slip_entry_pt':       args.slip_entry_pt       if args.slip_entry_pt       is not None else 1.5,
            'slip_exit_sl_pt':     args.slip_exit_sl_pt     if args.slip_exit_sl_pt     is not None else 3.0,
            'slip_exit_normal_pt': args.slip_exit_normal_pt if args.slip_exit_normal_pt is not None else 0.5,
            'slip_exit_force_pt':  args.slip_exit_force_pt  if args.slip_exit_force_pt  is not None else 2.0,
        }
        slip_label = f"차등(진입{slip_kw['slip_entry_pt']}/SL{slip_kw['slip_exit_sl_pt']}/익절{slip_kw['slip_exit_normal_pt']}/강제{slip_kw['slip_exit_force_pt']}pt)"
    else:
        slip_label = f"고정 {args.slip_fee_pt}pt"

    df = load_futures_data(args.code)
    if df.empty:
        sys.exit(1)

    print(f"[*] Loaded {len(df)} candles for code {args.code}")

    # 미니선물(코드 '105'로 시작)은 포인트당 50,000원, 그 외(KOSPI200 표준)는 250,000원
    point_value = 50_000 if str(args.code).startswith(('105', 'A05')) else 250_000

    if args.simple:
        print(f"[*] Running simplified Kalman backtest (Q={args.q}, R={args.r}, Mult={args.mult}, 고정 손절5pt/익절10pt)...")
        res_kalman = run_kalman_breakout_fair(df.copy(), Q=args.q, R=args.r, multiplier=args.mult)
    elif args.session == 'night':
        print(f"[*] Running LIVE-REPLICA Kalman backtest [야간선물, 고가/저가 틱 근사 모드] (era_order_manager.py _process_night_tick 동일 재현)...")
        print(f"    Q={args.q}, R={args.r}, Mult={args.mult}, kf_sl_mult={args.kf_sl_mult}, atr_cutoff={args.atr_cutoff}, margin_cap={args.margin_cap}, reentry_k={args.reentry_k}, point_value={point_value}, reentry_filter={'ON' if enable_reentry_filter else 'OFF'}, slip={slip_label}, commission_rate={commission_rate}")
        res_kalman = run_kalman_night_replica(df.copy(), Q=args.q, R=args.r, mult=args.mult,
                                               kf_sl_mult=args.kf_sl_mult, atr_cutoff=args.atr_cutoff,
                                               margin_cap=args.margin_cap, reentry_k=args.reentry_k,
                                               point_value=point_value, enable_reentry_filter=enable_reentry_filter,
                                               slip_fee_pt=args.slip_fee_pt, commission_rate=commission_rate,
                                               **slip_kw)
    elif args.price_mode == 'oc':
        print(f"[*] Running LIVE-REPLICA Kalman backtest [시가->종가 2지점 모드] ...")
        print(f"    Q={args.q}, R={args.r}, Mult={args.mult}, kf_sl_mult={args.kf_sl_mult}, atr_cutoff={args.atr_cutoff}, margin_cap={args.margin_cap}, reentry_k={args.reentry_k}, point_value={point_value}, reentry_filter={'ON' if enable_reentry_filter else 'OFF'}, slip={slip_label}, commission_rate={commission_rate}")
        res_kalman = run_kalman_live_replica_oc(df.copy(), Q=args.q, R=args.r, mult=args.mult,
                                                 kf_sl_mult=args.kf_sl_mult, atr_cutoff=args.atr_cutoff,
                                                 margin_cap=args.margin_cap, reentry_k=args.reentry_k,
                                                 point_value=point_value, enable_reentry_filter=enable_reentry_filter,
                                                 slip_fee_pt=args.slip_fee_pt, commission_rate=commission_rate,
                                                 **slip_kw)
    else:
        print(f"[*] Running LIVE-REPLICA Kalman backtest [고가/저가 틱 근사 모드] (era_order_manager.py 실전 로직 동일 재현)...")
        print(f"    Q={args.q}, R={args.r}, Mult={args.mult}, kf_sl_mult={args.kf_sl_mult}, atr_cutoff={args.atr_cutoff}, margin_cap={args.margin_cap}, reentry_k={args.reentry_k}, point_value={point_value}, reentry_filter={'ON' if enable_reentry_filter else 'OFF'}, slip={slip_label}, commission_rate={commission_rate}")
        res_kalman = run_kalman_live_replica(df.copy(), Q=args.q, R=args.r, mult=args.mult,
                                              kf_sl_mult=args.kf_sl_mult, atr_cutoff=args.atr_cutoff,
                                              margin_cap=args.margin_cap, reentry_k=args.reentry_k,
                                              point_value=point_value, enable_reentry_filter=enable_reentry_filter,
                                              slip_fee_pt=args.slip_fee_pt, commission_rate=commission_rate,
                                              **slip_kw)

    if res_kalman is None:
        print("[-] No trades executed for Kalman Filter Strategy.")
        sys.exit(0)

    print("\n" + "="*80)
    print(f"BACKTEST RESULTS FOR CODE {args.code} (session={args.session}, Q={args.q}, R={args.r}, Mult={args.mult}, 휩소필터={'ON' if enable_reentry_filter else 'OFF'}, slip={slip_label})")
    print("="*80)
    print(f"Kalman Filter Strategy:")
    print(f"   - Trades: {res_kalman['trades']} | Win Rate: {res_kalman['win_rate']:.2f}% | Profit Factor: {res_kalman['pf']:.2f}")
    print(f"   - Return: {res_kalman['profit_pct']:+.2f}% | MDD: {res_kalman['mdd']:.2f}%")
    if 'contracts' in res_kalman:
        print(f"   - Contracts per trade: {res_kalman['contracts']}")
    print("="*80)

    if args.compare:
        print("[*] Running production baseline strategy backtest...")
        res_prod = run_production_rolling_k(df.copy())
        if res_prod is not None:
            print("\n" + "="*80)
            print("COMPARISON WITH PRODUCTION BASELINE")
            print("="*80)
            print("1) Production Rolling K Strategy:")
            print(f"   - Trades: {res_prod['trades']} | Win Rate: {res_prod['win_rate']:.2f}% | PF: {res_prod['pf']:.2f}")
            print(f"   - Return: {res_prod['profit_pct']:+.2f}% | MDD: {res_prod['mdd']:.2f}%")
            print()
            print("2) Kalman Filter Strategy:")
            print(f"   - Trades: {res_kalman['trades']} | Win Rate: {res_kalman['win_rate']:.2f}% | PF: {res_kalman['pf']:.2f}")
            print(f"   - Return: {res_kalman['profit_pct']:+.2f}% | MDD: {res_kalman['mdd']:.2f}%")
            print("="*80)
