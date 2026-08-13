# -*- coding: utf-8 -*-
"""era_order_manager.py에 이미 구현돼 있지만 한 번도 백테스트된 적 없는 두 대안 청산
전략(Parabolic SAR, Bollinger Band 역추세)을 재현. 진입 로직은 run_chandelier_live_replica
(bqa/kalman_backtester.py:1099-1188)를 그대로 복사해 정확히 일치시키고, 포지션 보유 중
청산 로직만 SAR/BB로 교체했다(era_order_manager.py :4118-4460, :4600-4694 실측 이식).
realistic_gap_fill은 2026-08-08에 발견한 갭관통 유령체결 버그를 처음부터 방지하기 위해
기본 True로 둔다.
"""
import sys, os
sys.path.insert(0, "c:\\Antigravity\\AI_T_Agent\\bqa")
import json
import numpy as np
import pandas as pd
from kalman_backtester import load_futures_data

# [2026-08-12 지표 단일화] 지표 '계산'만 indicators.py 단일 소스로 교체한다.
# 동작 무변경 리팩터링 — 전략 로직·비용 모델·진입 조건·세션 필터는 손대지 않았다.
# 이 백테스터는 back-adjust를 쓰지 않으므로 모든 시계열이 실제 가격 공간(actual)이다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicators as I

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
                           # [2026-08-14] SAR 트레일링 '시작 거리' 배수를 손절폭 배수와 분리한다.
                           # 종전에는 sar_sl_mult 하나가 두 가지를 동시에 바꿨다 —
                           #   ① 손절폭      : _sl_limit(sar_sl_mult) = max(atr14*mult, 2.0)  [캡 적용됨]
                           #   ② SAR 시작거리: sar.flip(fill, atr14*sar_sl_mult, ...)          [캡 미적용]
                           # 그래서 손절폭이 물리적으로 발동 불가능한 배수(예 100)에서도 결과가 바뀌었다.
                           # 라이브는 ②가 'ATR14 × 1.0' 하드코딩이었다. None이면 종전대로
                           # sar_sl_mult를 따라가므로 기존 동작과 100% 동일하다.
                           # [2026-08-14 갱신] 라이브에도 배수가 생겼다 —
                           # era_order_manager.py 1459행 config 키 sar_init_mult(기본 1.0),
                           # 적용 지점 1777/5108/5178행. 현재 라이브 설정은 2.0이므로
                           # 라이브와 맞추려면 여기도 2.0을 넘겨야 한다.
                           # 근거: 선물_SAR_트레일링폭_최적화_20260814.md
                           sar_init_mult=None,
                           # [2026-08-12] 라이브 정합용 틱 단위 SAR.
                           #   False(기본) = 기존 봉 단위(on_bar) — 동작 100% 동일, 골든 대조로 검증됨.
                           #   True        = era_order_manager.py의 _sar_tick(on_tick)과 같은 cadence.
                           # 라이브는 틱마다 SAR을 전진시키고 AF를 올린다. 5분봉 1회 갱신인 이 백테스터와
                           # 회전율이 16~18배 어긋나 있어(백테 1.4건/일 vs 라이브 22건/일) 백테스트로
                           # 라이브를 예측할 수 없었다. 그 격차를 측정하기 위한 옵션이다.
                           sar_tick_mode=False, sar_ticks_per_bar=24,
                           bb_window=20, bb_sigma=2.0, bb_trail_std_mult=1.5, bb_trail_exit_mult=0.5,
                           squeeze_window=100, squeeze_quantile=0.25, use_squeeze_filter=True,
                           entry_target_mode='kalman_band', breakout_k=0.2,
                           use_intraday_atr_for_sl=False, sl_hard_cap_pt=None,
                           # [2026-08-14] 변동성 비례(동적) 손절 캡. 라이브 kalman 경로가 이미 쓰는
                           # _effective_sl_hard_cap() = clamp(mult*std_error, min, max) 와 동일한 형태.
                           # dyn_cap_mult=None(기본)이면 이 블록은 통째로 비활성이고 기존 동작과 100% 동일.
                           # std_error는 '진입 시점 스냅샷'을 쓴다 — 라이브가 보유 중 재추정으로 손절선이
                           # 흔들리는 것을 막으려고 진입 스냅샷을 쓰는 것과 같은 이유(무한루프 버그 수정 이력).
                           dyn_cap_mult=None, dyn_cap_min=None, dyn_cap_max=None,
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
    # [2026-08-12] 일봉 TR(오버나잇 갭 포함) + 칼만 평활 → indicators로 이관.
    # I.true_range의 NaN 채움(첫 행 prev_close 없음 → H-L)은 종전 .fillna(H-L)와 같다.
    tr = I.true_range(daily['high'], daily['low'], daily['prev_close'])
    kf_atr_path = I.kalman_atr(tr, q=0.002, r=0.2)
    daily['range'] = I.intraday_range(daily['high'], daily['low'])

    # [2026-08-11] 일중 ATR. 기존 atr14는 일봉 TR(=오버나잇 갭 포함) 기반인데, 이 전략은
    # 매일 15:35에 전량 청산해 갭을 감수하지 않으므로 손절폭 산출에 쓰기엔 horizon이 맞지
    # 않는다. 갭을 뺀 일중 레인지(H-L)에 같은 칼만 평활을 적용한 계열을 따로 만든다.
    # atr_cutoff 게이트는 계속 기존 atr14를 쓴다(용도가 다름 — 국면 필터).
    # 미래참조 방지: atr_map과 완전히 동일하게 '전일까지'(kf_rng_path[i-1])만 참조한다.
    # [2026-08-12] 갭을 뺀 일중 레인지에 같은 칼만 평활(q=0.002, r=0.2) — indicators로 이관.
    kf_rng_path = I.kalman_atr(daily['range'].values, q=0.002, r=0.2)

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
    # [2026-08-12] 볼린저/밴드폭/스퀴즈 분위수 → indicators로 이관(ddof=1 그대로).
    # 밴드폭 분모는 실제 가격 공간이어야 한다. 이 파일은 back-adjust를 쓰지 않으므로
    # closes가 곧 실제 가격이고, 중심선에 "actual" 태그를 붙여 넘기면 된다.
    bb_mid, bb_upper, bb_lower = I.bollinger_series(closes, bb_window, bb_sigma, ddof=1)
    bandwidth = I.bandwidth_series(bb_upper, bb_lower, I.Series(bb_mid, "actual"))
    squeeze_limit = I.rolling_quantile(bandwidth, squeeze_window, squeeze_quantile)

    # [2026-08-11] 장기 이평선 방향 필터.
    # 종가가 이평선 위면 LONG만, 아래면 SHORT만 허용한다. 역방향 진입을 원천 차단해
    # 추세를 거스르는 거래를 없애려는 것. None이면 비활성(기존 동작과 100% 동일).
    ma_line = (I.moving_average_series(closes, ma_filter_period)      # [2026-08-12] indicators 이관
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
        # [2026-08-14] 동적 캡. 고정 캡과 동시 지정 시 둘 다 적용(더 좁은 쪽이 이김).
        if dyn_cap_mult is not None:
            _c = dyn_cap_mult * entry_std_error
            if dyn_cap_min is not None:
                _c = max(_c, dyn_cap_min)
            if dyn_cap_max is not None:
                _c = min(_c, dyn_cap_max)
            v = min(v, _c)
        return v

    # [2026-08-12] 봉 하나를 O→H→L→C 경로의 합성 틱으로 편다. sar_tick_mode 전용.
    # 리플레이 하네스(scratch/replay/replay_harness.py --tick-order OHLC --ticks-per-bar)와
    # 같은 규약이다. 시가 자신은 제외하고 각 구간 끝점(H·L·C)이 정확히 포함된다.
    def _tick_path(o_, h_, l_, c_):
        per = max(1, int(sar_ticks_per_bar) // 3)
        out = []
        for a_, b_ in ((o_, h_), (h_, l_), (l_, c_)):
            step_ = (b_ - a_) / per
            for k_ in range(1, per + 1):
                out.append(a_ + step_ * k_)
        return out

    cap = float(INIT_CAPITAL)
    equity, pnls, wins = [cap], [], 0
    pos, entry_price, peak_price = 0, 0.0, 0.0
    entry_time = None
    entry_std_error = 0.5      # [2026-08-14] 진입 시점 std_error 스냅샷 (동적 캡 전용)
    entry_atr14 = 2.0          # [2026-08-14] 진입 시점 atr14 스냅샷 (진단 전용)
    trade_log = []
    day_high, day_low, cur_day = -np.inf, np.inf, None
    last_long_exit, last_short_exit = 0.0, 0.0
    target_long, target_short = np.inf, -np.inf
    std_error, trend, atr14, prev_range = 0.5, "NEUTRAL", 2.0, 0.0
    prev_close_px = 0.0
    consec_losses = 0
    daily_loss_pt = 0.0
    # [2026-08-12] 인라인 SAR 4개 변수 → indicators.SarState 한 개로 교체.
    # 갱신 순서(SAR 전진 → peak_price 클램프 → EP/AF 갱신)는 종전과 완전히 같다.
    # 주의: 이 백테스터는 EP 갱신 기준으로 고가/저가가 아니라 '종가'를 쓴다(비표준).
    # 그 동작을 그대로 보존하려고 on_bar에 high=low=c_close를 넘긴다. 봉마다 1회 호출.
    sar = I.SarState(sar=0.0, ep=0.0, af=sar_af_init, bull=True,
                     af_init=sar_af_init, af_step=sar_af_step, af_max=sar_af_max)

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
            # [2026-08-12] 칼만 평활 + 잔차 표준편차 → indicators로 이관.
            # 창을 먼저 잘라서 넘기므로(closed_upto는 끝점 배타적) 당봉 종가 closes[i]가
            # 구조적으로 창에 들어갈 수 없다. 잘리는 구간은 종전 closes[i-kf_window:i]와 동일.
            _win = I.Window.closed_upto(closes, i, kf_window)
            std_error, kf_price = I.kalman_residual_std(
                _win, Q, R, std_window=std_window, trim=trim_std_outliers, fallback=0.5)
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
                target_long, target_short = I.breakout_targets(_anchor, _w, breakout_k)
            else:
                target_long, target_short = I.kalman_band_targets(kf_price, std_error, mult)

            lb_start = max(0, i - 300)
            wb, wc = bucket60[lb_start:i], closes[lb_start:i]
            trend = "NEUTRAL"
            if len(wb) >= 5:
                rev_b, rev_c = wb[::-1], wc[::-1]
                uniq_b, first_idx = np.unique(rev_b, return_index=True)
                long_closes = rev_c[first_idx]
                if len(long_closes) >= 5:
                    # [2026-08-12] 장기추세 칼만 → indicators로 이관(경로 전체가 필요해 kalman_path).
                    kf_long_path = I.kalman_path(I.Window(long_closes), trend_q, trend_r)
                    slope = kf_long_path[-1] - kf_long_path[-2]
                    trend = "UP" if slope > trend_slope_threshold else ("DOWN" if slope < -trend_slope_threshold else "NEUTRAL")

        c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]

        # ── 포지션 보유 중: SAR / BB 청산 ──
        if pos != 0:
            exit_price, is_force = None, False
            if pos == 1:
                _prev_peak = peak_price   # [2026-08-12] sar_tick_mode 전용: 이 봉을 반영하기 '전'의 피크
                peak_price = max(peak_price, c_high)
                pnl_pt = c_close - entry_price
                ts_fire = (time_stop_enabled and entry_time is not None and
                           (ts - entry_time).total_seconds() >= time_stop_minutes * 60 and
                           (peak_price - entry_price) < time_stop_mfe_pt)
                if strategy == 'sar':
                    if sar.bull:
                        sl_limit = _sl_limit(sar_sl_mult)   # [2026-08-11] 일중ATR/절대캡 반영
                        if sar_tick_mode:
                            # [2026-08-12] 라이브(_sar_tick) cadence — 봉 안을 틱으로 걸으며
                            # 매 틱 SAR 전진·AF 상승·청산 판정. 청산가는 그 틱 가격(라이브 동일).
                            if force_close or vol_force_close:
                                exit_price, is_force = c_close, True
                            else:
                                _pk = _prev_peak
                                for _p in _tick_path(c_open, c_high, c_low, c_close):
                                    _pk = max(_pk, _p)
                                    sar.on_tick(_p, clamp=_pk)
                                    if _p <= sar.sar or (_p - entry_price) <= -sl_limit:
                                        exit_price = _p
                                        break
                                if exit_price is None and ts_fire:
                                    exit_price = c_close
                        else:
                            sar.on_bar(c_close, c_close, clamp=peak_price)   # [2026-08-12] SarState
                            if force_close or vol_force_close:
                                exit_price, is_force = c_close, True
                            elif c_low <= sar.sar or pnl_pt <= -sl_limit:
                                exit_price = min(c_close, max(sar.sar, c_low))
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
                _prev_peak = peak_price   # [2026-08-12] sar_tick_mode 전용: 이 봉을 반영하기 '전'의 피크
                peak_price = min(peak_price, c_low)
                pnl_pt = entry_price - c_close
                ts_fire = (time_stop_enabled and entry_time is not None and
                           (ts - entry_time).total_seconds() >= time_stop_minutes * 60 and
                           (entry_price - peak_price) < time_stop_mfe_pt)
                if strategy == 'sar':
                    if not sar.bull:
                        sl_limit = _sl_limit(sar_sl_mult)   # [2026-08-11] 일중ATR/절대캡 반영
                        if sar_tick_mode:
                            # [2026-08-12] LONG 쪽과 동일 — 라이브(_sar_tick) cadence.
                            if force_close or vol_force_close:
                                exit_price, is_force = c_close, True
                            else:
                                _pk = _prev_peak
                                for _p in _tick_path(c_open, c_high, c_low, c_close):
                                    _pk = min(_pk, _p)
                                    sar.on_tick(_p, clamp=_pk)
                                    if _p >= sar.sar or (entry_price - _p) <= -sl_limit:
                                        exit_price = _p
                                        break
                                if exit_price is None and ts_fire:
                                    exit_price = c_close
                        else:
                            sar.on_bar(c_close, c_close, clamp=peak_price)   # [2026-08-12] SarState
                            if force_close or vol_force_close:
                                exit_price, is_force = c_close, True
                            elif c_high >= sar.sar or pnl_pt <= -sl_limit:
                                exit_price = max(c_close, min(sar.sar, c_high))
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
                    if pos == 1 and sar.bull and c_low <= sar.sar:
                        exit_reason = 'SAR역전'
                    elif pos == -1 and (not sar.bull) and c_high >= sar.sar:
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
                                       'reason': exit_reason,
                                       # [2026-08-14] 진단용 — 캡이 무엇에 걸리는지 사후 분석하려면
                                       # 진입 시점의 두 변동성 지표가 필요하다. 손익 계산엔 안 쓰인다.
                                       'entry_std_error': float(entry_std_error),
                                       'entry_atr14': float(entry_atr14)})
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
                entry_std_error = std_error   # [2026-08-14] 동적 캡용 진입 스냅샷
                entry_atr14 = atr14           # [2026-08-14] 진단용 진입 스냅샷
                entry_time = dt_index[i]
                if strategy == 'sar':
                    # [2026-08-12] flip: sar = fill ∓ atr, ep = fill, af = af_init, bull = _dir>0.
                    # 종전 'fill - atr14*sar_sl_mult*_dir'과 부호까지 동일하다.
                    sar.flip(fill, atr14 * (sar_sl_mult if sar_init_mult is None else sar_init_mult),
                             _dir > 0)
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
                entry_std_error = std_error   # [2026-08-14] 동적 캡용 진입 스냅샷
                entry_atr14 = atr14           # [2026-08-14] 진단용 진입 스냅샷
                entry_time = dt_index[i]
                if strategy == 'sar':
                    sar.flip(fill, atr14 * (sar_sl_mult if sar_init_mult is None else sar_init_mult),
                             _dir > 0)   # [2026-08-12] LONG쪽과 동일

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
