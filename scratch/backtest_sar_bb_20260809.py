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
                           # [2026-08-11] 실측 반영. 종전 1.5/3.0/0.5/2.0은 근거 없는 가정이었고,
                           # ERA 로그의 주문가 대비 체결가 6,767건 실측은 평균 -0.004pt / 중앙값 0.000이었다
                           # (scratch/measure_real_slippage_20260811.py). 실측 0이 아니라 0.25를 쓰는 이유는
                           # ① 전 구간 모의투자라 하한값이고 ② 워크포워드 OOS가 정확히 0.25pt에서 손익분기여서
                           # 기본값이 "여기를 넘으면 손실" 기준선 역할을 하기 때문. 결론 전 0.5pt로 스트레스할 것.
                           # 커밋 ff74807이 bqa/kalman_backtester.py만 고치고 이 파일을 빠뜨렸다.
                           slip_entry_pt=0.25, slip_exit_sl_pt=0.25, slip_exit_normal_pt=0.25, slip_exit_force_pt=0.25,
                           realistic_gap_fill=True, gap_guard_mult=None,
                           reentry_pullback_mult=0.5, reentry_breakout_mult=0.2,
                           sar_af_init=0.02, sar_af_step=0.02, sar_af_max=0.20,
                           sar_sl_mult=1.0,
                           bb_window=20, bb_sigma=2.0, bb_trail_std_mult=1.5, bb_trail_exit_mult=0.5,
                           squeeze_window=100, squeeze_quantile=0.25, use_squeeze_filter=True,
                           entry_target_mode='kalman_band', breakout_k=0.2,
                           use_intraday_atr_for_sl=False, sl_hard_cap_pt=None,
                           ma_filter_period=None, allow_overnight=False,
                           daily_loss_limit_pt=None,
                           ma_slope_min=None, ma_slope_lookback=40,
                           reverse_entry=False, ma_filter_invert=False,
                           entry_anchor='open', entry_width_basis='prev_range',
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

    # [2026-08-11] 일중 ATR. 기존 atr14는 일봉 TR(=오버나잇 갭 포함) 기반인데, 이 전략은
    # 매일 15:35에 전량 청산해 갭을 감수하지 않으므로 손절폭 산출에 쓰기엔 horizon이 맞지
    # 않는다. 갭을 뺀 일중 레인지(H-L)에 같은 칼만 평활을 적용한 계열을 따로 만든다.
    # atr_cutoff 게이트는 계속 기존 atr14를 쓴다(용도가 다름 — 국면 필터).
    # 미래참조 방지: atr_map과 완전히 동일하게 '전일까지'(kf_rng_path[i-1])만 참조한다.
    kf_rng_path = np.empty(len(daily))
    kf_r_, P_r, Q_r, R_r = None, 1.0, 0.002, 0.2
    for j, rng_val in enumerate(daily['range'].values):
        if kf_r_ is None:
            kf_r_ = rng_val
        else:
            P_r = P_r + Q_r
            K_r = P_r / (P_r + R_r)
            kf_r_ = kf_r_ + K_r * (rng_val - kf_r_)
            P_r = (1 - K_r) * P_r
        kf_rng_path[j] = kf_r_

    atr_map, prev_range_map, intraday_atr_map = {}, {}, {}
    prev_close_map = {}
    day_list = daily['date_day'].tolist()
    for i, dkey in enumerate(day_list):
        if i == 0:
            atr_map[dkey] = 2.0
            intraday_atr_map[dkey] = 2.0
            prev_range_map[dkey] = 0.0
            prev_close_map[dkey] = 0.0
        else:
            v = kf_atr_path[i - 1]
            atr_map[dkey] = float(v) if pd.notna(v) and v > 0 else 2.0
            vr = kf_rng_path[i - 1]
            intraday_atr_map[dkey] = float(vr) if pd.notna(vr) and vr > 0 else 2.0
            prev_range_map[dkey] = float(daily['range'].iloc[i - 1])
            prev_close_map[dkey] = float(daily['close'].iloc[i - 1])

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

    # 이평선 기울기(pt/봉). 직전 봉까지만 쓰므로 인과적이다.
    # ma_slope_min은 "이평선이 최소 이만큼은 기울어 있어야 추세로 인정한다"는 문턱.
    ma_slope = None
    if ma_line is not None and ma_slope_min is not None:
        _m = pd.Series(ma_line)
        ma_slope = ((_m - _m.shift(ma_slope_lookback)) / float(ma_slope_lookback)).values

    SLIP_ENTRY, SLIP_EXIT_SL = slip_entry_pt, slip_exit_sl_pt
    SLIP_EXIT_NORMAL, SLIP_EXIT_FORCE = slip_exit_normal_pt, slip_exit_force_pt

    # [2026-08-11] 손절폭 산출 전용 헬퍼. use_intraday_atr_for_sl=False, sl_hard_cap_pt=None이면
    # 기존 식(max(atr14*mult, 2.0))과 100% 동일하다. 되돌리려면 이 함수와 두 호출부를 원복.
    def _sl_limit(mult_):
        base = intraday_atr if use_intraday_atr_for_sl else atr14
        v = max(base * mult_, 2.0)
        if sl_hard_cap_pt is not None:
            v = min(v, sl_hard_cap_pt)
        return v

    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0
    pos, entry_price, peak_price = 0, 0.0, 0.0
    entry_time = None
    trade_log = []
    day_high, day_low, cur_day = -np.inf, np.inf, None
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0
    prev_close_px = 0.0
    consec_losses = 0
    daily_loss_pt = 0.0
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
            intraday_atr = intraday_atr_map.get(day_key, 2.0)   # [2026-08-11] 손절폭 전용
            prev_range = prev_range_map.get(day_key, 0.0)
            prev_close_px = prev_close_map.get(day_key, 0.0)
            consec_losses = 0
            daily_loss_pt = 0.0          # 당일 누적 손실(pt, 양수=손실)
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
                # 앵커: 타점의 중심을 어디에 두는가.
                #   open       시초가 — 갭이 타점을 통째로 밀어 올린다(현행)
                #   prev_close 전일 종가 — 갭 자체가 돌파 판정에 포함된다
                #   mid        둘의 중간 — 갭의 절반만 반영
                if entry_anchor == 'prev_close':
                    _anchor = prev_close_px if prev_close_px > 0 else day_open
                elif entry_anchor == 'mid':
                    _anchor = (day_open + prev_close_px) / 2.0 if prev_close_px > 0 else day_open
                else:
                    _anchor = day_open
                # 폭의 기준: 전일 Range는 하루치 노이즈를 통째로 받는다.
                # 칼만 ATR/std_error는 평활돼 있어 더 안정적일 수 있다.
                if entry_width_basis == 'atr':
                    _w = atr14
                elif entry_width_basis == 'std_error':
                    _w = std_error
                else:
                    _w = prev_range
                # 실거래(era_order_manager.py:4065)가 SAR에 실제로 쓰던 타점.
                # 시초가에서 전일 Range의 K배만큼 떨어진 고정 가격이라, 칼만 밴드와 달리
                # 장중에 움직이지 않는다. std_error는 필터용으로만 계속 계산한다.
                target_long = _anchor + _w * breakout_k
                target_short = _anchor - _w * breakout_k
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
                        sl_limit = _sl_limit(sar_sl_mult)   # [2026-08-11] 일중ATR/절대캡 반영
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif c_low <= sar_value or pnl_pt <= -sl_limit:
                            exit_price = min(c_close, max(sar_value, c_low))
                        elif ts_fire:
                            exit_price = c_close
                    else:
                        sl_limit = _sl_limit(sar_sl_mult)   # [2026-08-11] 일중ATR/절대캡 반영
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif pnl_pt <= -sl_limit:
                            exit_price = c_close
                        elif ts_fire:
                            exit_price = c_close
                elif strategy == 'bb':
                    sl_limit = _sl_limit(1.2)   # [2026-08-11] 일중ATR/절대캡 반영
                    # [2026-08-11 미래참조 제거] bb_upper[i]는 당봉 종가까지 포함해 만든 밴드다.
                    # 그걸 당봉 고가(c_high)와 비교하면, 봉이 끝나야 알 수 있는 값으로 봉 도중의
                    # 체결을 판정하는 미래참조가 된다. 직전 완결봉 기준으로 옮긴다
                    # (ma_line[i-1]/closes[i-1]과 같은 관례). 되돌리려면 [i-1]을 [i]로.
                    bb_tp = bb_upper[i - 1]
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
                        sl_limit = _sl_limit(sar_sl_mult)   # [2026-08-11] 일중ATR/절대캡 반영
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif c_high >= sar_value or pnl_pt <= -sl_limit:
                            exit_price = max(c_close, min(sar_value, c_high))
                        elif ts_fire:
                            exit_price = c_close
                    else:
                        sl_limit = _sl_limit(sar_sl_mult)   # [2026-08-11] 일중ATR/절대캡 반영
                        if force_close or vol_force_close:
                            exit_price, is_force = c_close, True
                        elif pnl_pt <= -sl_limit:
                            exit_price = c_close
                        elif ts_fire:
                            exit_price = c_close
                elif strategy == 'bb':
                    sl_limit = _sl_limit(1.2)   # [2026-08-11] 일중ATR/절대캡 반영
                    # [2026-08-11 미래참조 제거] LONG 쪽과 동일한 이유(위 주석 참조).
                    bb_tp = bb_lower[i - 1]
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
                # [2026-08-11 추가] 청산 사유 분류. 손익 계산에는 일절 관여하지 않고
                # return_trades=True일 때 trade_log에만 기록한다(미래참조 수정 전후의
                # 청산 사유 분포를 대조하기 위해 필요). 위 분기에서 이미 확정된 지역변수만
                # 재사용하므로 전략 로직은 바뀌지 않는다. 되돌리려면 이 블록과
                # trade_log의 'reason' 항목을 삭제.
                if is_force:
                    exit_reason = '강제청산'
                elif strategy == 'sar':
                    if pos == 1 and sar_bull and c_low <= sar_value:
                        exit_reason = 'SAR역전'
                    elif pos == -1 and (not sar_bull) and c_high >= sar_value:
                        exit_reason = 'SAR역전'
                    elif pnl_pt <= -sl_limit:
                        exit_reason = '손절'
                    else:
                        exit_reason = '타임스톱'
                else:
                    if pnl_pt <= -sl_limit:
                        exit_reason = '손절'
                    elif (not np.isnan(bb_tp) and bb_tp > 0 and
                          ((pos == 1 and c_high >= bb_tp) or (pos == -1 and c_low <= bb_tp))):
                        exit_reason = 'BB익절'
                    else:
                        exit_reason = '트레일링'
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
                # 일일 손실 누적 — 라이브(_execute_futures_direct)와 같은 방식으로
                # 이익이 나면 차감한다. pt 단위이며 15계약 환산 한도와 비교한다.
                _net_pt = (raw_pnl - exit_slip)
                if _net_pt < 0:
                    daily_loss_pt += -_net_pt
                else:
                    daily_loss_pt = max(0.0, daily_loss_pt - _net_pt)
                if pos == 1:
                    last_long_exit = exit_price
                else:
                    last_short_exit = exit_price
                if return_trades:
                    trade_log.append({'entry_time': entry_time, 'exit_time': dt_index[i],
                                       'direction': 'LONG' if pos == 1 else 'SHORT',
                                       'entry_price': entry_price, 'exit_price': exit_price,
                                       'pnl_pt': raw_pnl - exit_slip, 'is_force': is_force,
                                       'reason': exit_reason})
                pos, entry_price, peak_price = 0, 0.0, 0.0
            continue

        if force_close or vol_force_close or (hour, minute) < (entry_start_hour, entry_start_minute) or i < kf_window:
            continue
        if entry_end_hour is not None and (hour, minute) >= (entry_end_hour, entry_end_minute):
            continue
        if consec_losses >= consecutive_loss_limit:
            continue
        if daily_loss_limit_pt is not None and daily_loss_pt >= daily_loss_limit_pt:
            continue          # 일일 손실 한도 — 신규 진입만 차단(청산은 위에서 이미 처리)
        if atr14 < atr_cutoff:
            continue
        if std_error < min_std_error_entry:
            continue

        if c_high >= target_long:
            # 직전 봉 종가와 직전 봉 이평선으로 판정한다. 당봉 종가를 쓰면 진입 시점에
            # 아직 모르는 값을 참조하는 셈이라 미래참조가 된다.
            if ma_line is not None and i >= 1 and not np.isnan(ma_line[i - 1]):
                _above = closes[i - 1] >= ma_line[i - 1]
                if (not _above) != bool(ma_filter_invert):
                    continue      # 이평선 아래에서는 LONG 금지 (invert면 위에서 금지)
            elif ma_line is not None:
                continue
            if ma_slope is not None and (i < 1 or np.isnan(ma_slope[i - 1]) or ma_slope[i - 1] < ma_slope_min):
                continue          # 이평선이 충분히 상승 중이 아니면 LONG 금지
            open_gap = max(c_open - target_long, 0.0)
            if gap_guard_mult is not None and open_gap > gap_guard_mult * std_error:
                pass
            elif trend == "DOWN":
                continue
            elif regime_filter_enabled and trend != "UP":
                continue
            # [2026-08-11 미래참조 제거] bandwidth/squeeze_limit/bb_mid는 당봉 종가까지 포함해
            # 만든 값이다. 진입은 당봉 고가가 target_long을 치는 순간 일어나므로, 그 시점엔
            # 아직 당봉 종가를 알 수 없다 → 직전 완결봉 기준으로 옮긴다. 중심선 방향 판정도
            # 당봉 종가(c_close) 대신 직전봉 종가(closes[i-1])와 비교해 이평선 필터
            # (ma_line[i-1]/closes[i-1])와 같은 관례로 통일한다. 되돌리려면 [i-1]을 [i]로,
            # closes[i-1]을 c_close로.
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(squeeze_limit[i - 1]) and bandwidth[i - 1] < squeeze_limit[i - 1]:
                continue
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(bb_mid[i - 1]) and closes[i - 1] <= bb_mid[i - 1]:
                continue
            elif reentry_ok(1, target_long):
                _lvl = max(c_open, target_long) if realistic_gap_fill else target_long
                _dir = -1 if reverse_entry else 1
                fill = _lvl + SLIP_ENTRY * _dir      # 사면 비싸게, 팔면 싸게
                pos, entry_price, peak_price = _dir, fill, fill
                entry_time = dt_index[i]
                if strategy == 'sar':
                    sar_value = fill - atr14 * sar_sl_mult * _dir
                    sar_ep, sar_af, sar_bull = fill, sar_af_init, _dir > 0
        elif c_low <= target_short:
            if ma_line is not None and i >= 1 and not np.isnan(ma_line[i - 1]):
                _below = closes[i - 1] <= ma_line[i - 1]
                if (not _below) != bool(ma_filter_invert):
                    continue      # 이평선 위에서는 SHORT 금지 (invert면 아래에서 금지)
            elif ma_line is not None:
                continue
            if ma_slope is not None and (i < 1 or np.isnan(ma_slope[i - 1]) or ma_slope[i - 1] > -ma_slope_min):
                continue          # 이평선이 충분히 하락 중이 아니면 SHORT 금지
            open_gap = max(target_short - c_open, 0.0)
            if gap_guard_mult is not None and open_gap > gap_guard_mult * std_error:
                pass
            elif trend == "UP":
                continue
            elif regime_filter_enabled and trend != "DOWN":
                continue
            # [2026-08-11 미래참조 제거] LONG 쪽과 동일한 이유(위 주석 참조).
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(squeeze_limit[i - 1]) and bandwidth[i - 1] < squeeze_limit[i - 1]:
                continue
            elif use_squeeze_filter and strategy == 'sar' and not np.isnan(bb_mid[i - 1]) and closes[i - 1] >= bb_mid[i - 1]:
                continue
            elif reentry_ok(-1, target_short):
                _lvl = min(c_open, target_short) if realistic_gap_fill else target_short
                _dir = 1 if reverse_entry else -1
                fill = _lvl + SLIP_ENTRY * _dir
                pos, entry_price, peak_price = _dir, fill, fill
                entry_time = dt_index[i]
                if strategy == 'sar':
                    sar_value = fill - atr14 * sar_sl_mult * _dir
                    sar_ep, sar_af, sar_bull = fill, sar_af_init, _dir > 0

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
