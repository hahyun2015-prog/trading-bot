# -*- coding: utf-8 -*-
"""개장범위 브레이크아웃(Opening Range Breakout, ORB) 검증.

오늘 테스트한 모든 기법(챈들리에/SAR/Donchian/UT Bot/SMC/Lorentzian 등)은 전부
'가격 추종형 트레일링' 아니면 '지표 크로스' 계열이라 서로 상관관계가 높다. ORB는
진입 신호 생성 자체가 완전히 다른 방식(장 시작 N분 레인지 이탈)이라 새로운 대안이
될 수 있는지 확인한다.

공정한 비교를 위해 청산 로직은 이미 검증된 SAR 트레일링 스탑을 그대로 재사용하고
(entry_end_hour, atr_cutoff=15.2도 동일 적용), 진입 신호 생성 부분만 Kalman 밴드
돌파 대신 개장범위 돌파로 교체했다. 비용구조(슬리피지/수수료), 강제청산 시각,
갭관통 방지(realistic_gap_fill과 동일 원리)도 기존 스크립트와 동일하게 유지한다.
"""
import sys
sys.path.insert(0, "c:\\Antigravity\\AI_T_Agent\\bqa")
import json
import numpy as np
import pandas as pd
from kalman_backtester import load_futures_data

INIT_CAPITAL = 50_000_000


def run_orb_replica(df, or_minutes=15, atr_cutoff=15.2,
                     point_value=50_000, reentry_k=0.25,
                     entry_start_hour=9, entry_start_minute=0,
                     entry_end_hour=15, entry_end_minute=0,
                     vol_force_close_hour=15, vol_force_close_min_start=35, vol_force_close_min_end=45,
                     commission_rate=0.000065,
                     slip_entry_pt=1.5, slip_exit_sl_pt=3.0, slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
                     reentry_pullback_mult=0.5, reentry_breakout_mult=0.2,
                     sar_af_init=0.02, sar_af_step=0.02, sar_af_max=0.20,
                     one_trade_per_direction_per_day=True,
                     return_trades=False):
    n = len(df)
    if n < 50:
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

    or_end_total_min = entry_start_hour * 60 + entry_start_minute + or_minutes

    SLIP_ENTRY, SLIP_EXIT_SL = slip_entry_pt, slip_exit_sl_pt
    SLIP_EXIT_NORMAL, SLIP_EXIT_FORCE = slip_exit_normal_pt, slip_exit_force_pt

    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0
    pos, entry_price, peak_price = 0, 0.0, 0.0
    entry_time = None
    trade_log = []
    cur_day = None
    or_high, or_low, or_done = -np.inf, np.inf, False
    last_long_exit, last_short_exit = 0.0, 0.0
    long_used_today, short_used_today = False, False
    atr14, prev_range = 2.0, 0.0
    sar_value, sar_ep, sar_af, sar_bull = 0.0, 0.0, sar_af_init, True

    def reentry_ok(direction, price):
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
            or_high, or_low, or_done = -np.inf, np.inf, False
            atr14 = atr_map.get(day_key, 2.0)
            prev_range = prev_range_map.get(day_key, 0.0)
            last_long_exit, last_short_exit = 0.0, 0.0
            long_used_today, short_used_today = False, False

        hour, minute = ts.hour, ts.minute
        total_min = hour * 60 + minute
        vol_force_close = (hour == vol_force_close_hour and vol_force_close_min_start <= minute <= vol_force_close_min_end)
        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]

        # 개장범위 형성 구간: 진입 없이 고가/저가만 누적
        in_or_window = (entry_start_hour * 60 + entry_start_minute) <= total_min < or_end_total_min
        if in_or_window:
            or_high = max(or_high, c_high)
            or_low = min(or_low, c_low)
            if pos == 0:
                continue
        elif not or_done:
            or_done = True

        if pos != 0:
            exit_price, is_force = None, False
            if pos == 1:
                peak_price = max(peak_price, c_high)
                pnl_pt = c_close - entry_price
                if sar_bull:
                    sar_value = min(sar_value + sar_af * (sar_ep - sar_value), peak_price)
                    if c_close > sar_ep:
                        sar_ep = c_close
                        sar_af = min(sar_af + sar_af_step, sar_af_max)
                    sl_limit = max(atr14 * 1.0, 2.0)
                    if vol_force_close:
                        exit_price, is_force = c_close, True
                    elif c_low <= sar_value or pnl_pt <= -sl_limit:
                        exit_price = min(c_close, max(sar_value, c_low))
                else:
                    sl_limit = max(atr14 * 1.0, 2.0)
                    if vol_force_close:
                        exit_price, is_force = c_close, True
                    elif pnl_pt <= -sl_limit:
                        exit_price = c_close
            else:
                peak_price = min(peak_price, c_low)
                pnl_pt = entry_price - c_close
                if not sar_bull:
                    sar_value = max(sar_value - sar_af * (sar_value - sar_ep), peak_price)
                    if c_close < sar_ep:
                        sar_ep = c_close
                        sar_af = min(sar_af + sar_af_step, sar_af_max)
                    sl_limit = max(atr14 * 1.0, 2.0)
                    if vol_force_close:
                        exit_price, is_force = c_close, True
                    elif c_high >= sar_value or pnl_pt <= -sl_limit:
                        exit_price = max(c_close, min(sar_value, c_high))
                else:
                    sl_limit = max(atr14 * 1.0, 2.0)
                    if vol_force_close:
                        exit_price, is_force = c_close, True
                    elif pnl_pt <= -sl_limit:
                        exit_price = c_close

            if exit_price is not None:
                exit_slip = SLIP_EXIT_FORCE if is_force else (SLIP_EXIT_SL if (exit_price < entry_price) == (pos == 1) else SLIP_EXIT_NORMAL)
                raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                commission_cost = entry_price * point_value * commission_rate * 2
                gain = (raw_pnl - exit_slip) * point_value - commission_cost
                cap += gain
                equity.append(cap)
                pnls.append(gain)
                wins += int(gain > 0)
                if pos == 1:
                    last_long_exit = exit_price
                else:
                    last_short_exit = exit_price
                if return_trades:
                    trade_log.append({'entry_time': entry_time, 'exit_time': dt_index[i],
                                       'direction': 'LONG' if pos == 1 else 'SHORT',
                                       'entry_price': entry_price, 'exit_price': exit_price,
                                       'pnl_pt': raw_pnl - exit_slip, 'is_force': is_force})
                pos, entry_price, peak_price = 0, 0.0, 0.0
            continue

        if vol_force_close or not or_done or total_min >= (entry_end_hour * 60 + entry_end_minute):
            continue
        if atr14 < atr_cutoff or not np.isfinite(or_high) or not np.isfinite(or_low) or or_high <= or_low:
            continue
        if one_trade_per_direction_per_day and long_used_today and short_used_today:
            continue

        if c_high >= or_high and not (one_trade_per_direction_per_day and long_used_today):
            if reentry_ok(1, or_high):
                fill = max(c_open, or_high) + SLIP_ENTRY
                pos, entry_price, peak_price = 1, fill, fill
                entry_time = dt_index[i]
                sar_value, sar_ep, sar_af, sar_bull = fill - atr14, fill, sar_af_init, True
                long_used_today = True
        elif c_low <= or_low and not (one_trade_per_direction_per_day and short_used_today):
            if reentry_ok(-1, or_low):
                fill = min(c_open, or_low) - SLIP_ENTRY
                pos, entry_price, peak_price = -1, fill, fill
                entry_time = dt_index[i]
                sar_value, sar_ep, sar_af, sar_bull = fill + atr14, fill, sar_af_init, False
                short_used_today = True

    total = len(pnls)
    if total == 0:
        return None
    equity_arr = np.array(equity)
    peaks = np.maximum.accumulate(equity_arr)
    drawdowns = (peaks - equity_arr) / peaks * 100
    max_mdd = drawdowns.max()
    win_rate = (wins / total) * 100
    gain_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = gain_sum / loss_sum if loss_sum > 0 else 999.0
    res = {'trades': total, 'win_rate': win_rate, 'pf': pf, 'mdd': max_mdd, 'final_capital': cap}
    if return_trades:
        res['trade_log'] = trade_log
    return res


if __name__ == "__main__":
    df_full = load_futures_data("10500000")
    max_day = pd.to_datetime(df_full["date_day"], format="%Y%m%d").max()
    periods = {
        "전체기간": df_full,
        "최근60일": df_full[pd.to_datetime(df_full["date_day"], format="%Y%m%d") >= (max_day - pd.Timedelta(days=60))],
        "최근30일": df_full[pd.to_datetime(df_full["date_day"], format="%Y%m%d") >= (max_day - pd.Timedelta(days=30))],
    }

    def fmt(res):
        if res is None:
            return "거래없음"
        return f"거래{res['trades']:>4d} 승률{res['win_rate']:6.2f}% PF{res['pf']:7.2f} MDD{res['mdd']:6.2f}%"

    for or_min in [5, 15, 30, 60]:
        print("=" * 100)
        print(f"개장범위 {or_min}분 (09:00~09:{or_min:02d}), 하루 방향당 1회 진입, atr_cutoff=15.2")
        print("=" * 100)
        for pname, pdf in periods.items():
            r = run_orb_replica(pdf.copy(), or_minutes=or_min, atr_cutoff=15.2)
            print(f"  {pname:6s}  {fmt(r)}")
        print()

    print("=" * 100)
    print("참고: atr_cutoff=0.5(사실상 무필터)로 개장범위 15분 재확인")
    print("=" * 100)
    for pname, pdf in periods.items():
        r = run_orb_replica(pdf.copy(), or_minutes=15, atr_cutoff=0.5)
        print(f"  {pname:6s}  {fmt(r)}")
