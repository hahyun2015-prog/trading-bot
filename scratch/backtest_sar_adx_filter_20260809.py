# -*- coding: utf-8 -*-
"""검증된 SAR+entry_cutoff+min_std_error_entry+atr_cutoff(15.2pt) 조합에
ADX/DMI 추세강도 진입필터를 추가했을 때 효과가 있는지 검증.

가설: 현재 atr_cutoff는 '절대 변동성'만 거르는데, 전체기간·최근구간 성과 격차의
근본 원인은 저변동성 자체가 아니라 방향성 없는 횡보(추세 부재)였다(새매매기법_탐색_20260809.md
9절). ADX는 변동성 크기와 무관하게 '그 움직임이 추세인지 노이즈인지'를 구분하므로
atr_cutoff와 상호보완적인 필터가 될 수 있다.

ADX는 시스템의 기존 추세필터(trend_bar_minutes=15분 버킷)와 동일한 15분봉 위에서
계산해 일관성을 맞추고, lookahead 방지를 위해 '직전에 완결된 15분봉' 기준 ADX만
사용한다(현재 형성 중인 봉은 사용 안 함).

backtest_sar_bb_20260809.py의 run_sar_or_bb_replica를 그대로 복사해
ADX 필터 부분만 추가했다(그 외 로직 100% 동일).
"""
import sys
sys.path.insert(0, "c:\\Antigravity\\AI_T_Agent\\bqa")
import json
import numpy as np
import pandas as pd
from kalman_backtester import load_futures_data

INIT_CAPITAL = 50_000_000


def _wilder_smooth(x, period):
    result = np.zeros(len(x))
    result[0] = x[0]
    for i in range(1, len(x)):
        result[i] = result[i - 1] - (result[i - 1] / period) + x[i]
    return result


def compute_adx(highs, lows, closes, period=14):
    n = len(highs)
    up_move = np.zeros(n)
    down_move = np.zeros(n)
    tr = np.zeros(n)
    up_move[1:] = highs[1:] - highs[:-1]
    down_move[1:] = lows[:-1] - lows[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr[1:] = np.maximum(highs[1:] - lows[1:],
                         np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
    tr[0] = highs[0] - lows[0]

    tr_s = _wilder_smooth(tr, period)
    plus_dm_s = _wilder_smooth(plus_dm, period)
    minus_dm_s = _wilder_smooth(minus_dm, period)

    with np.errstate(divide='ignore', invalid='ignore'):
        plus_di = 100 * plus_dm_s / tr_s
        minus_di = 100 * minus_dm_s / tr_s
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    plus_di = np.nan_to_num(plus_di, nan=0.0, posinf=0.0, neginf=0.0)
    minus_di = np.nan_to_num(minus_di, nan=0.0, posinf=0.0, neginf=0.0)
    dx = np.nan_to_num(dx, nan=0.0, posinf=0.0, neginf=0.0)

    adx = np.zeros(n)
    if n > period:
        adx[period] = np.mean(dx[1:period + 1])
        for i in range(period + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return plus_di, minus_di, adx


def run_sar_adx_replica(df, Q=0.00005, R=1.0, mult=0.6, atr_cutoff=0.5,
                         point_value=50_000, reentry_k=0.25, kf_window=40, std_window=20,
                         trend_q=0.001, trend_r=1.0, trend_bar_minutes=15,
                         trend_slope_threshold=0.01, trim_std_outliers=0,
                         consecutive_loss_limit=5, min_std_error_entry=0.0,
                         entry_start_hour=9, entry_start_minute=0,
                         entry_end_hour=None, entry_end_minute=0,
                         force_close_hour=8, force_close_minute=45, force_close_window_min=10,
                         commission_rate=0.000065,
                         slip_entry_pt=1.5, slip_exit_sl_pt=3.0, slip_exit_normal_pt=0.5, slip_exit_force_pt=2.0,
                         reentry_pullback_mult=0.5, reentry_breakout_mult=0.2,
                         sar_af_init=0.02, sar_af_step=0.02, sar_af_max=0.20,
                         squeeze_window=100, squeeze_quantile=0.25, use_squeeze_filter=True,
                         bb_window=20, bb_sigma=2.0,
                         adx_filter_enabled=False, adx_period=14, adx_threshold=20.0,
                         require_di_direction=True,
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

    bb_mid = pd.Series(closes).rolling(bb_window).mean().values
    bb_std = pd.Series(closes).rolling(bb_window).std(ddof=1).values
    bb_upper = bb_mid + bb_sigma * bb_std
    bb_lower = bb_mid - bb_sigma * bb_std
    bandwidth = (bb_upper - bb_lower) / bb_mid
    squeeze_limit = pd.Series(bandwidth).rolling(squeeze_window).quantile(squeeze_quantile).values

    # ── ADX/DMI: 시스템의 기존 추세필터와 동일한 15분봉 위에서 계산, lookahead 방지를
    # 위해 '직전에 완결된 15분봉'의 ADX만 각 5분봉에 매핑한다.
    adx_arr = np.full(n, np.nan)
    plus_di_arr = np.full(n, np.nan)
    minus_di_arr = np.full(n, np.nan)
    if adx_filter_enabled:
        tmp = pd.DataFrame({'bucket': bucket60, 'high': highs, 'low': lows, 'close': closes})
        agg = tmp.groupby('bucket').agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last')).reset_index()
        pdi_b, mdi_b, adx_b = compute_adx(agg['high'].values, agg['low'].values, agg['close'].values, period=adx_period)
        agg['adx_prev'] = pd.Series(adx_b).shift(1).values
        agg['pdi_prev'] = pd.Series(pdi_b).shift(1).values
        agg['mdi_prev'] = pd.Series(mdi_b).shift(1).values
        b2adx = dict(zip(agg['bucket'], agg['adx_prev']))
        b2pdi = dict(zip(agg['bucket'], agg['pdi_prev']))
        b2mdi = dict(zip(agg['bucket'], agg['mdi_prev']))
        adx_arr = np.array([b2adx.get(b, np.nan) for b in bucket60])
        plus_di_arr = np.array([b2pdi.get(b, np.nan) for b in bucket60])
        minus_di_arr = np.array([b2mdi.get(b, np.nan) for b in bucket60])

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
            day_high, day_low = highs[i], lows[i]
            atr14 = atr_map.get(day_key, 2.0)
            prev_range = prev_range_map.get(day_key, 0.0)
            consec_losses = 0
            last_long_exit, last_short_exit = 0.0, 0.0
        else:
            day_high = max(day_high, highs[i])
            day_low = min(day_low, lows[i])

        hour, minute = ts.hour, ts.minute
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
                    if force_close or vol_force_close:
                        exit_price, is_force = c_close, True
                    elif c_low <= sar_value or pnl_pt <= -sl_limit:
                        exit_price = min(c_close, max(sar_value, c_low))
                else:
                    sl_limit = max(atr14 * 1.0, 2.0)
                    if force_close or vol_force_close:
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
                    if force_close or vol_force_close:
                        exit_price, is_force = c_close, True
                    elif c_high >= sar_value or pnl_pt <= -sl_limit:
                        exit_price = max(c_close, min(sar_value, c_high))
                else:
                    sl_limit = max(atr14 * 1.0, 2.0)
                    if force_close or vol_force_close:
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
            if trend == "DOWN":
                continue
            elif use_squeeze_filter and not np.isnan(squeeze_limit[i]) and bandwidth[i] < squeeze_limit[i]:
                continue
            elif use_squeeze_filter and not np.isnan(bb_mid[i]) and c_close <= bb_mid[i]:
                continue
            elif adx_filter_enabled and (np.isnan(adx_arr[i]) or adx_arr[i] < adx_threshold or
                                          (require_di_direction and plus_di_arr[i] <= minus_di_arr[i])):
                continue
            elif reentry_ok(1, target_long):
                fill = max(c_open, target_long) + SLIP_ENTRY
                pos, entry_price, peak_price = 1, fill, fill
                entry_time = dt_index[i]
                sar_value, sar_ep, sar_af, sar_bull = fill - atr14, fill, sar_af_init, True
        elif c_low <= target_short:
            if trend == "UP":
                continue
            elif use_squeeze_filter and not np.isnan(squeeze_limit[i]) and bandwidth[i] < squeeze_limit[i]:
                continue
            elif use_squeeze_filter and not np.isnan(bb_mid[i]) and c_close >= bb_mid[i]:
                continue
            elif adx_filter_enabled and (np.isnan(adx_arr[i]) or adx_arr[i] < adx_threshold or
                                          (require_di_direction and minus_di_arr[i] <= plus_di_arr[i])):
                continue
            elif reentry_ok(-1, target_short):
                fill = min(c_open, target_short) - SLIP_ENTRY
                pos, entry_price, peak_price = -1, fill, fill
                entry_time = dt_index[i]
                sar_value, sar_ep, sar_af, sar_bull = fill + atr14, fill, sar_af_init, False

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
    res = {'trades': total, 'win_rate': win_rate, 'pf': pf, 'mdd': max_mdd,
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

    BASE = dict(Q=fs["kf_q"], R=fs["kf_r"], mult=fs["kf_mult"], reentry_k=fs["reentry_k"], point_value=50_000,
                entry_end_hour=fs.get("entry_end_hour"), entry_end_minute=fs.get("entry_end_minute", 0),
                min_std_error_entry=fs.get("min_std_error_entry", 1.5), atr_cutoff=15.2)

    def fmt(res):
        if res is None:
            return "거래없음"
        return f"거래{res['trades']:>4d} 승률{res['win_rate']:6.2f}% PF{res['pf']:7.2f} MDD{res['mdd']:5.2f}%"

    print("=" * 100)
    print("기준선(ADX 필터 없음, 현재 라이브 조합)")
    print("=" * 100)
    for pname, pdf in periods.items():
        r = run_sar_adx_replica(pdf.copy(), adx_filter_enabled=False, **BASE)
        print(f"  {pname:6s}  {fmt(r)}")
    print()

    for thr in [15, 20, 25, 30]:
        print("=" * 100)
        print(f"ADX 필터 ON, threshold={thr}, DI방향 일치 요구")
        print("=" * 100)
        for pname, pdf in periods.items():
            r = run_sar_adx_replica(pdf.copy(), adx_filter_enabled=True, adx_threshold=thr,
                                     require_di_direction=True, **BASE)
            print(f"  {pname:6s}  {fmt(r)}")
        print()

    print("=" * 100)
    print("ADX 필터 ON, threshold=20, DI방향 요구 없음(추세강도만)")
    print("=" * 100)
    for pname, pdf in periods.items():
        r = run_sar_adx_replica(pdf.copy(), adx_filter_enabled=True, adx_threshold=20,
                                 require_di_direction=False, **BASE)
        print(f"  {pname:6s}  {fmt(r)}")
