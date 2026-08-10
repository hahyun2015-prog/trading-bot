# -*- coding: utf-8 -*-
"""era_order_manager.py에 이미 구현돼 있지만 한 번도 백테스트된 적 없는 두 대안 청산
전략(Parabolic SAR, Bollinger Band 역추세)을 재현. 진입 로직은 run_chandelier_live_replica
(bqa/kalman_backtester.py:1099-1188)를 그대로 복사해 정확히 일치시키고, 포지션 보유 중
청산 로직만 SAR/BB로 교체했다(era_order_manager.py :4118-4460, :4600-4694 실측 이식).
realistic_gap_fill은 2026-08-08에 발견한 갭관통 유령체결 버그를 처음부터 방지하기 위해
기본 True로 둔다.
"""
import sys
sys.path.insert(0, "c:\\Antigravity\\AI_T_Agent\\bqa")
import json
import numpy as np
import pandas as pd
from kalman_backtester import load_futures_data

INIT_CAPITAL = 50_000_000


def run_sar_or_bb_replica(df, strategy, Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
                           point_value=50_000, reentry_k=0.25, kf_window=40, std_window=20,
                           trend_q=0.001, trend_r=1.0, trend_bar_minutes=15,
                           trend_slope_threshold=0.01, trim_std_outliers=0,
                           consecutive_loss_limit=5, min_std_error_entry=0.0,
                           entry_start_hour=9, entry_start_minute=0,
                           entry_end_hour=None, entry_end_minute=0,
                           force_close_hour=8, force_close_minute=45, force_close_window_min=10,
                           commission_rate=0.000065,
                           slip_entry_pt=1.5, slip_exit_sl_pt=3.0, slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
                           realistic_gap_fill=True, gap_guard_mult=None,
                           reentry_pullback_mult=0.5, reentry_breakout_mult=0.2,
                           sar_af_init=0.02, sar_af_step=0.02, sar_af_max=0.20,
                           sar_sl_mult=1.0,
                           bb_window=20, bb_sigma=2.0, bb_trail_std_mult=1.5, bb_trail_exit_mult=0.5,
                           squeeze_window=100, squeeze_quantile=0.25, use_squeeze_filter=True,
                           entry_target_mode='kalman_band', breakout_k=0.2,
                           ma_filter_period=None, allow_overnight=False,
                           regime_filter_enabled=False,
                           time_stop_enabled=False, time_stop_minutes=10.0, time_stop_mfe_pt=4.0,
                           return_trades=False):
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

    # Bollinger(20,2) 롤링 - BB 청산 타깃 + SAR 스퀴즈 필터 공용
    bb_mid = pd.Series(closes).rolling(bb_window).mean().values
    bb_std = pd.Series(closes).rolling(bb_window).std(ddof=1).values
    bb_upper = bb_mid + bb_sigma * bb_std
    bb_lower = bb_mid - bb_sigma * bb_std
    bandwidth = (bb_upper - bb_lower) / bb_mid
    squeeze_limit = pd.Series(bandwidth).rolling(squeeze_window).quantile(squeeze_quantile).values

    # [2026-08-11] 장기 이평선 방향 필터.
    # 종가가 이평선 위면 LONG만, 아래면 SHORT만 허용한다. 역방향 진입을 원천 차단해
    # 추세를 거스르는 거래를 없애려는 것. None이면 비활성(기존 동작과 100% 동일).
    ma_line = (pd.Series(closes).rolling(ma_filter_period).mean().values
               if ma_filter_period else None)

    SLIP_ENTRY, SLIP_EXIT_SL = slip_entry_pt, slip_exit_sl_pt
    SLIP_EXIT_NORMAL, SLIP_EXIT_FORCE = slip_exit_normal_pt, slip_exit_force_pt

    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0
    pos, entry_price, peak_price = 0, 0.0, 0.0
    entry_time = None
    trade_log = []
    day_high, day_low, cur_day = -np.inf, np.inf, None
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0
    consec_losses = 0
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
            day_start_idx = i
            day_high, day_low = highs[i], lows[i]
            day_open = opens[i]          # 돌파 타점(entry_target_mode='breakout')용
            atr14 = atr_map.get(day_key, 2.0)
            prev_range = prev_range_map.get(day_key, 0.0)
            consec_losses = 0
            last_long_exit, last_short_exit = 0.0, 0.0
        else:
            day_high = max(day_high, highs[i])
            day_low = min(day_low, lows[i])

        hour, minute = ts.hour, ts.minute
        # [2026-08-11] allow_overnight=True면 1일청산을 없앤다 — 장마감(15:35~15:45)
        # 무조건청산과 익일 08:45 안전청산 둘 다. 포지션이 여러 날에 걸쳐 유지되며
        # 청산은 오직 전략 자신의 손절·트레일링으로만 일어난다.
        # 오버나잇 갭 위험을 그대로 떠안는다는 뜻이다(2026-06-11 거래정지 갭 -117.98pt 사례).
        if allow_overnight:
            force_close = vol_force_close = False
        else:
            force_close = (hour == force_close_hour and force_close_minute <= minute <= force_close_minute + force_close_window_min)
            vol_force_close = (hour == 15 and 35 <= minute <= 45)

        enough_data = i >= kf_window
        if enough_data:
            window_closes = closes[i - kf_window:i]
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
            if entry_target_mode == 'breakout':
                # 실거래(era_order_manager.py:4065)가 SAR에 실제로 쓰던 타점.
                # 시초가에서 전일 Range의 K배만큼 떨어진 고정 가격이라, 칼만 밴드와 달리
                # 장중에 움직이지 않는다. std_error는 필터용으로만 계속 계산한다.
                target_long = day_open + prev_range * breakout_k
                target_short = day_open - prev_range * breakout_k
            else:
                target_long, target_short = kf_price + band, kf_price - band

            lb_start = max(0, i - 300)
            wb, wc = bucket60[lb_start:i], closes[lb_start:i]
            trend = "NEUTRAL"
            if len(wb) >= 5:
                rev_b, rev_c = wb[::-1], wc[::-1]
                uniq_b, first_idx = np.unique(rev_b, return_index=True)
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
                    trend = "UP" if slope > trend_slope_threshold else ("DOWN" if slope < -trend_slope_threshold else "NEUTRAL")

        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]

        # ── 포지션 보유 중: SAR / BB 청산 ──
        if pos != 0:
            exit_price, is_force = None, False
            if pos == 1:
                peak_price = max(peak_price, c_high)
                pnl_pt = c_close - entry_price
                ts_fire = (time_stop_enabled and entry_time is not None and
                           (ts - entry_time).total_seconds() >= time_stop_minutes * 60 and
                           (peak_price - entry_price) < time_stop_mfe_pt)
                if strategy == 'sar':
                    if sar_bull:
                        sar_value = min(sar_value + sar_af * (sar_ep - sar_value), peak_price)
                        if c_close > sar_ep:
                            sar_ep = c_close
                            sar_af = min(sar_af + sar_af_step, sar_af_max)
                        sl_limit = max(atr14 * sar_sl_mult, 2.0)   # [2026-08-11] 배수 파라미터화
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif c_low <= sar_value or pnl_pt <= -sl_limit:
                            exit_price = min(c_close, max(sar_value, c_low))
                        elif ts_fire:
                            exit_price = c_close
                    else:
                        sl_limit = max(atr14 * sar_sl_mult, 2.0)   # [2026-08-11] 배수 파라미터화
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif pnl_pt <= -sl_limit:
                            exit_price = c_close
                        elif ts_fire:
                            exit_price = c_close
                elif strategy == 'bb':
                    sl_limit = max(atr14 * 1.2, 2.0)
                    bb_tp = bb_upper[i]
                    max_pnl_pt = peak_price - entry_price
                    if force_close or vol_force_close:
                        exit_price, is_force = c_close, True
                    elif pnl_pt <= -sl_limit:
                        exit_price = c_close
                    elif not np.isnan(bb_tp) and bb_tp > 0 and c_high >= bb_tp:
                        exit_price = min(c_close, max(bb_tp, c_low)) if realistic_gap_fill else bb_tp
                    elif (max_pnl_pt >= bb_trail_std_mult * std_error) and (c_low <= peak_price - bb_trail_exit_mult * std_error):
                        exit_price = min(c_close, max(peak_price - bb_trail_exit_mult * std_error, c_low))
            else:
                peak_price = min(peak_price, c_low)
                pnl_pt = entry_price - c_close
                ts_fire = (time_stop_enabled and entry_time is not None and
                           (ts - entry_time).total_seconds() >= time_stop_minutes * 60 and
                           (entry_price - peak_price) < time_stop_mfe_pt)
                if strategy == 'sar':
                    if not sar_bull:
                        sar_value = max(sar_value - sar_af * (sar_value - sar_ep), peak_price)
                        if c_close < sar_ep:
                            sar_ep = c_close
                            sar_af = min(sar_af + sar_af_step, sar_af_max)
                        sl_limit = max(atr14 * sar_sl_mult, 2.0)   # [2026-08-11] 배수 파라미터화
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif c_high >= sar_value or pnl_pt <= -sl_limit:
                            exit_price = max(c_close, min(sar_value, c_high))
                        elif ts_fire:
                            exit_price = c_close
                    else:
                        sl_limit = max(atr14 * sar_sl_mult, 2.0)   # [2026-08-11] 배수 파라미터화
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif pnl_pt <= -sl_limit:
                            exit_price = c_close
                        elif ts_fire:
                            exit_price = c_close
                elif strategy == 'bb':
                    sl_limit = max(atr14 * 1.2, 2.0)
                    bb_tp = bb_lower[i]
                    max_pnl_pt = entry_price - peak_price
                    if force_close or vol_force_close:
                        exit_price, is_force = c_close, True
                    elif pnl_pt <= -sl_limit:
                        exit_price = c_close
                    elif not np.isnan(bb_tp) and bb_tp > 0 and c_low <= bb_tp:
                        exit_price = max(c_close, min(bb_tp, c_high)) if realistic_gap_fill else bb_tp
                    elif (max_pnl_pt >= bb_trail_std_mult * std_error) and (c_high >= peak_price + bb_trail_exit_mult * std_error):
                        exit_price = max(c_close, min(peak_price + bb_trail_exit_mult * std_error, c_high))

            if exit_price is not None:
                exit_slip = SLIP_EXIT_FORCE if is_force else (SLIP_EXIT_SL if (exit_price < entry_price) == (pos == 1) else SLIP_EXIT_NORMAL)
                raw_pnl = (exit_price - entry_price) if pos == 1 else (entry_price - exit_price)
                commission_cost = entry_price * point_value * commission_rate * 2
                gain = (raw_pnl - exit_slip) * point_value - commission_cost
                cap += gain
                equity.append(cap)
                pnls.append(gain)
                wins += int(gain > 0)
                if gain < 0:
                    consec_losses += 1
                else:
                    consec_losses = 0
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
            # 직전 봉 종가와 직전 봉 이평선으로 판정한다. 당봉 종가를 쓰면 진입 시점에
            # 아직 모르는 값을 참조하는 셈이라 미래참조가 된다.
            if ma_line is not None and (i < 1 or np.isnan(ma_line[i - 1]) or closes[i - 1] < ma_line[i - 1]):
                continue          # 이평선 아래에서는 LONG 금지
            open_gap = max(c_open - target_long, 0.0)
            if gap_guard_mult is not None and open_gap > gap_guard_mult * std_error:
                pass
            elif trend == "DOWN":
                continue
            elif regime_filter_enabled and trend != "UP":
                continue
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(squeeze_limit[i]) and bandwidth[i] < squeeze_limit[i]:
                continue
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(bb_mid[i]) and c_close <= bb_mid[i]:
                continue
            elif reentry_ok(1, target_long):
                fill = (max(c_open, target_long) + SLIP_ENTRY) if realistic_gap_fill else (target_long + SLIP_ENTRY)
                pos, entry_price, peak_price = 1, fill, fill
                entry_time = dt_index[i]
                if strategy == 'sar':
                    sar_value, sar_ep, sar_af, sar_bull = fill - atr14 * sar_sl_mult, fill, sar_af_init, True
        elif c_low <= target_short:
            if ma_line is not None and (i < 1 or np.isnan(ma_line[i - 1]) or closes[i - 1] > ma_line[i - 1]):
                continue          # 이평선 위에서는 SHORT 금지
            open_gap = max(target_short - c_open, 0.0)
            if gap_guard_mult is not None and open_gap > gap_guard_mult * std_error:
                pass
            elif trend == "UP":
                continue
            elif regime_filter_enabled and trend != "DOWN":
                continue
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(squeeze_limit[i]) and bandwidth[i] < squeeze_limit[i]:
                continue
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(bb_mid[i]) and c_close >= bb_mid[i]:
                continue
            elif reentry_ok(-1, target_short):
                fill = (min(c_open, target_short) - SLIP_ENTRY) if realistic_gap_fill else (target_short - SLIP_ENTRY)
                pos, entry_price, peak_price = -1, fill, fill
                entry_time = dt_index[i]
                if strategy == 'sar':
                    sar_value, sar_ep, sar_af, sar_bull = fill + atr14 * sar_sl_mult, fill, sar_af_init, False

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
    wins_list = [p for p in pnls if p > 0]
    losses_list = [p for p in pnls if p < 0]
    avg_win_pt = (sum(wins_list) / len(wins_list) / point_value) if wins_list else 0.0
    avg_loss_pt = (sum(losses_list) / len(losses_list) / point_value) if losses_list else 0.0
    worst_loss_pt = (min(pnls) / point_value) if pnls else 0.0
    res = {'trades': total, 'win_rate': win_rate, 'pf': pf, 'mdd': max_mdd,
           'worst_loss_pt': worst_loss_pt, 'avg_win_pt': avg_win_pt, 'avg_loss_pt': avg_loss_pt,
           'final_capital': cap}
    if return_trades:
        res['trade_log'] = trade_log
    return res


if __name__ == "__main__":
    with open("c:\\Antigravity\\AI_T_Agent\\config\\config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    with open("c:\\Antigravity\\AI_T_Agent\\config\\config_local.json", encoding="utf-8") as f:
        local = json.load(f)
    for key, val in local.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    fs = cfg["futures_settings"]

    df_full = load_futures_data("10500000")
    max_day = pd.to_datetime(df_full["date_day"], format="%Y%m%d").max()
    periods = {
        "전체기간": df_full,
        "최근60일": df_full[pd.to_datetime(df_full["date_day"], format="%Y%m%d") >= (max_day - pd.Timedelta(days=60))],
        "최근30일": df_full[pd.to_datetime(df_full["date_day"], format="%Y%m%d") >= (max_day - pd.Timedelta(days=30))],
    }

    COMMON = dict(Q=fs["kf_q"], R=fs["kf_r"], mult=fs["kf_mult"], reentry_k=fs["reentry_k"],
                  point_value=50_000, realistic_gap_fill=True)

    def fmt(res):
        if res is None:
            return "거래없음"
        return (f"거래{res['trades']:>4d} 승률{res['win_rate']:6.2f}% PF{res['pf']:7.2f} "
                f"최악{res['worst_loss_pt']:+7.2f}pt 평균승{res['avg_win_pt']:+.2f} 평균패{res['avg_loss_pt']:+.2f}")

    for pname, pdf in periods.items():
        print(f"--- {pname} ---")
        r_sar = run_sar_or_bb_replica(pdf.copy(), strategy='sar', **COMMON)
        print(f"  Parabolic SAR   {fmt(r_sar)}")
        r_bb = run_sar_or_bb_replica(pdf.copy(), strategy='bb', **COMMON)
        print(f"  Bollinger Band  {fmt(r_bb)}")
        print()
