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


def run_kalman_live_replica(df, Q=0.0001, R=0.5, mult=1.0, kf_sl_mult=3.4, atr_cutoff=0.5,
                             margin_cap=0.20, reentry_k=0.5, kf_window=40, std_window=20,
                             trend_q=0.001, trend_r=1.0, max_contracts=15):
    """
    era_order_manager.py의 실전 주간선물 칼만 전략(update_kalman_targets / _process_day_tick)을
    최대한 동일하게 재현한 백테스트.

    진입/청산 판정은 5분봉의 고가·저가로 "그 봉 안에서 가격이 닿았는지"를 체크합니다
    (실시간 틱 감시의 근사치이며, 종가만으로 판정하지 않습니다).

    실전과 동일하게 반영한 요소:
      - 매 5분봉마다 직전 kf_window(40)개 봉으로 칼만필터를 새로 추정(재시드) -> KF가, 최근 std_window(20)봉 오차표준편차
      - 60분봉 리샘플 장기 칼만(q=trend_q, r=trend_r)으로 추세(UP/DOWN/NEUTRAL) 필터 -> 역추세 진입 차단
      - 손절 = max(min(kf_sl_mult * std_error, 1.2 * ATR14), 2.0)pt (하이브리드 동적, ATR14는 전일까지 일별 14일 롤링)
      - 익절 = KF가 ± 3*std_error (3-Sigma), 도달 전 1.5*std_error 수익 시 트레일링(0.5*std_error 되돌림) 가동
      - ATR14 < atr_cutoff(기본 0.5pt)면 신규 진입 차단, 진입은 09:00 이후만 허용
      - 강제청산: 08:45~08:55(익일 장전), 15:35~15:45 사이 당일 변동폭(고가-저가) > 15pt
      - 재진입 휩소 방지: 직전 청산가 기준 전일Range*reentry_k 단위 폭의 구간에서는 재진입 차단
        (실전은 이 구간폭 계산에 strategy_type과 무관하게 self.futures_best_k를 그대로 사용합니다.
         reentry_k에 현재 active_strategy.json의 best_k 값을 넣어 호출하면 가장 정확합니다.)
    """
    n = len(df)
    if n < kf_window + 10:
        return None

    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    closes = df['close'].values.astype(float)
    day_keys = df['date_day'].values
    dt_index = df.index

    # --- 일별 ATR(14) 사전 계산: 전일까지의 데이터만 사용 (update_futures_dynamic_sl_tp와 동일 로직) ---
    daily = df.groupby('date_day').agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last')).reset_index()
    daily['prev_close'] = daily['close'].shift(1)
    tr = np.maximum(daily['high'] - daily['low'],
                     np.maximum((daily['high'] - daily['prev_close']).abs(),
                                (daily['low'] - daily['prev_close']).abs()))
    atr14_series = tr.rolling(14).mean()
    daily['range'] = daily['high'] - daily['low']
    atr_map, prev_range_map = {}, {}
    day_list = daily['date_day'].tolist()
    for i, dkey in enumerate(day_list):
        if i == 0:
            atr_map[dkey] = 2.0
            prev_range_map[dkey] = 0.0
        else:
            v = atr14_series.iloc[i - 1]
            atr_map[dkey] = float(v) if pd.notna(v) and v > 0 else 2.0
            prev_range_map[dkey] = float(daily['range'].iloc[i - 1])

    # --- 60분 버킷 ID (장기 추세필터용, epoch 시간 기준 정수 버킷) ---
    bucket60 = (df.index.values.astype('int64') // 10**9 // 3600).astype(np.int64)

    POINT_VALUE, MARGIN_RATE, SLIP_FEE_PT, INIT_CAPITAL = 250_000, 0.10, 0.05, 50_000_000

    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0

    first_price = closes[0]
    margin_per = first_price * POINT_VALUE * MARGIN_RATE
    safe_budget = INIT_CAPITAL * margin_cap
    contracts = max(1, min(max_contracts, int(safe_budget // margin_per))) if margin_per > 0 else 1

    pos, entry_price, peak_price = 0, 0.0, 0.0
    day_high, day_low, cur_day = -np.inf, np.inf, None
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    tp_long, tp_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0

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
            day_high, day_low = highs[i], lows[i]
            atr14 = atr_map.get(day_key, 2.0)
            prev_range = prev_range_map.get(day_key, 0.0)
        else:
            day_high = max(day_high, highs[i])
            day_low = min(day_low, lows[i])

        hour, minute = ts.hour, ts.minute
        force_close = (hour == 8 and 45 <= minute <= 55)
        vol_force_close = (hour == 15 and 35 <= minute <= 45) and (day_high - day_low > 15.0)

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
            sl_limit = max(min(kf_sl_mult * std_error, 1.2 * atr14), 2.0)
            exit_price = None

            if pos == 1:
                peak_price = max(peak_price, c_high)
                if force_close or vol_force_close:
                    exit_price = c_close
                elif (c_low - entry_price) <= -sl_limit:
                    exit_price = entry_price - sl_limit
                elif c_high >= tp_long:
                    exit_price = tp_long
                elif (peak_price - entry_price) >= 1.5 * std_error:
                    ts_price = peak_price - 0.5 * std_error
                    if c_low <= ts_price:
                        exit_price = ts_price
            else:
                peak_price = min(peak_price, c_low)
                if force_close or vol_force_close:
                    exit_price = c_close
                elif (entry_price - c_high) <= -sl_limit:
                    exit_price = entry_price + sl_limit
                elif c_low <= tp_short:
                    exit_price = tp_short
                elif (entry_price - peak_price) >= 1.5 * std_error:
                    ts_price = peak_price + 0.5 * std_error
                    if c_high >= ts_price:
                        exit_price = ts_price

            if exit_price is not None:
                raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                gain = (raw_pnl - SLIP_FEE_PT) * POINT_VALUE * contracts
                cap += gain
                equity.append(cap)
                pnls.append(gain)
                wins += int(gain > 0)
                if pos == 1:
                    last_long_exit = exit_price
                else:
                    last_short_exit = exit_price
                pos, entry_price, peak_price = 0, 0.0, 0.0
            continue

        # --- 신규 진입 (09:00 이후, ATR 컷오프, 추세역행/휩소 필터) ---
        if force_close or vol_force_close or hour < 9 or i < kf_window:
            continue
        if atr14 < atr_cutoff:
            continue

        if c_high >= target_long:
            if trend == "DOWN":
                continue
            if reentry_ok(1, target_long):
                pos, entry_price, peak_price = 1, target_long + SLIP_FEE_PT, target_long + SLIP_FEE_PT
        elif c_low <= target_short:
            if trend == "UP":
                continue
            if reentry_ok(-1, target_short):
                pos, entry_price, peak_price = -1, target_short - SLIP_FEE_PT, target_short - SLIP_FEE_PT

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
                                margin_cap=0.20, reentry_k=0.5, kf_window=40, std_window=20,
                                trend_q=0.001, trend_r=1.0, max_contracts=15):
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
                                (daily['low'] - daily['prev_close']).abs()))
    atr14_series = tr.rolling(14).mean()
    daily['range'] = daily['high'] - daily['low']
    atr_map, prev_range_map = {}, {}
    day_list = daily['date_day'].tolist()
    for i, dkey in enumerate(day_list):
        if i == 0:
            atr_map[dkey] = 2.0
            prev_range_map[dkey] = 0.0
        else:
            v = atr14_series.iloc[i - 1]
            atr_map[dkey] = float(v) if pd.notna(v) and v > 0 else 2.0
            prev_range_map[dkey] = float(daily['range'].iloc[i - 1])

    bucket60 = (df.index.values.astype('int64') // 10**9 // 3600).astype(np.int64)

    POINT_VALUE, MARGIN_RATE, SLIP_FEE_PT, INIT_CAPITAL = 250_000, 0.10, 0.05, 50_000_000
    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0

    first_price = closes[0]
    margin_per = first_price * POINT_VALUE * MARGIN_RATE
    safe_budget = INIT_CAPITAL * margin_cap
    contracts = max(1, min(max_contracts, int(safe_budget // margin_per))) if margin_per > 0 else 1

    pos, entry_price, peak_price = 0, 0.0, 0.0
    day_high, day_low, cur_day = -np.inf, np.inf, None
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    tp_long, tp_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0

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
        else:
            day_high = max(day_high, o_price, c_price)
            day_low = min(day_low, o_price, c_price)

        hour, minute = ts.hour, ts.minute
        force_close = (hour == 8 and 45 <= minute <= 55)
        vol_force_close = (hour == 15 and 35 <= minute <= 45) and (day_high - day_low > 15.0)

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
                sl_limit = max(min(kf_sl_mult * std_error, 1.2 * atr14), 2.0)
                exit_price = None
                if pos == 1:
                    peak_price = max(peak_price, price)
                    if force_close or vol_force_close:
                        exit_price = price
                    elif (price - entry_price) <= -sl_limit:
                        exit_price = price
                    elif price >= tp_long:
                        exit_price = price
                    elif (peak_price - entry_price) >= 1.5 * std_error and price <= peak_price - 0.5 * std_error:
                        exit_price = price
                else:
                    peak_price = min(peak_price, price)
                    if force_close or vol_force_close:
                        exit_price = price
                    elif (entry_price - price) <= -sl_limit:
                        exit_price = price
                    elif price <= tp_short:
                        exit_price = price
                    elif (entry_price - peak_price) >= 1.5 * std_error and price >= peak_price + 0.5 * std_error:
                        exit_price = price

                if exit_price is not None:
                    raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                    gain = (raw_pnl - SLIP_FEE_PT) * POINT_VALUE * contracts
                    cap += gain
                    equity.append(cap)
                    pnls.append(gain)
                    wins += int(gain > 0)
                    if pos == 1:
                        last_long_exit = exit_price
                    else:
                        last_short_exit = exit_price
                    pos, entry_price, peak_price = 0, 0.0, 0.0
                continue

            if force_close or vol_force_close or hour < 9 or i < kf_window or atr14 < atr_cutoff:
                continue
            if price >= target_long:
                if trend == "DOWN":
                    continue
                if reentry_ok(1, price):
                    pos, entry_price, peak_price = 1, price, price
            elif price <= target_short:
                if trend == "UP":
                    continue
                if reentry_ok(-1, price):
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
    parser.add_argument('--kf-sl-mult', type=float, default=3.4, help="하이브리드 동적 손절 배수 (era_order_manager.py 기본값 3.4)")
    parser.add_argument('--atr-cutoff', type=float, default=0.5, help="ATR14 진입 차단 기준 (pt, 기본 0.5)")
    parser.add_argument('--margin-cap', type=float, default=0.20, help="가용예수금 대비 마진 캡 비율 (기본 0.20, 실전 기본값)")
    parser.add_argument('--reentry-k', type=float, default=0.5, help="재진입 휩소방지 구간폭 산정용 K값 (active_strategy.json의 best_k와 동일하게 넣으면 가장 정확)")
    parser.add_argument('--simple', action='store_true', help="단순화 버전(run_kalman_breakout_fair, 고정 5/10pt)으로 실행")
    parser.add_argument('--price-mode', choices=['tick', 'oc'], default='tick',
                         help="tick: 5분봉 고가/저가로 터치 판정(실시간 틱 근사, 기본값) | oc: 시가->종가 두 지점만 순차 관찰 (틱 데이터 없는 경우 근사)")
    parser.add_argument('--compare', action='store_true', help="Compare with production baseline")

    args = parser.parse_args()

    df = load_futures_data(args.code)
    if df.empty:
        sys.exit(1)

    print(f"[*] Loaded {len(df)} candles for code {args.code}")

    if args.simple:
        print(f"[*] Running simplified Kalman backtest (Q={args.q}, R={args.r}, Mult={args.mult}, 고정 손절5pt/익절10pt)...")
        res_kalman = run_kalman_breakout_fair(df.copy(), Q=args.q, R=args.r, multiplier=args.mult)
    elif args.price_mode == 'oc':
        print(f"[*] Running LIVE-REPLICA Kalman backtest [시가->종가 2지점 모드] ...")
        print(f"    Q={args.q}, R={args.r}, Mult={args.mult}, kf_sl_mult={args.kf_sl_mult}, atr_cutoff={args.atr_cutoff}, margin_cap={args.margin_cap}, reentry_k={args.reentry_k}")
        res_kalman = run_kalman_live_replica_oc(df.copy(), Q=args.q, R=args.r, mult=args.mult,
                                                 kf_sl_mult=args.kf_sl_mult, atr_cutoff=args.atr_cutoff,
                                                 margin_cap=args.margin_cap, reentry_k=args.reentry_k)
    else:
        print(f"[*] Running LIVE-REPLICA Kalman backtest [고가/저가 틱 근사 모드] (era_order_manager.py 실전 로직 동일 재현)...")
        print(f"    Q={args.q}, R={args.r}, Mult={args.mult}, kf_sl_mult={args.kf_sl_mult}, atr_cutoff={args.atr_cutoff}, margin_cap={args.margin_cap}, reentry_k={args.reentry_k}")
        res_kalman = run_kalman_live_replica(df.copy(), Q=args.q, R=args.r, mult=args.mult,
                                              kf_sl_mult=args.kf_sl_mult, atr_cutoff=args.atr_cutoff,
                                              margin_cap=args.margin_cap, reentry_k=args.reentry_k)

    if res_kalman is None:
        print("[-] No trades executed for Kalman Filter Strategy.")
        sys.exit(0)

    print("\n" + "="*80)
    print(f"BACKTEST RESULTS FOR CODE {args.code} (Q={args.q}, R={args.r}, Mult={args.mult})")
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
