# -*- coding: utf-8 -*-
"""볼린저밴드 '평균회귀' 전략 재설계 백테스터 (2026-08-11).

기존 era_order_manager.py의 futures_strategy_type="bollinger_band"은 이름만 볼린저였다.
진입이 변동성 돌파(day_open ± prev_range*K)이고 BB는 익절 타깃으로만 쓰여, 추세추종
진입 + 역추세 청산이라는 모순된 구조였고 전 구간 PF 0.32~0.58로 탈락했다
(선물_전략전수분석_및_사이징검증_종합_20260811.md 4.4절).

이 파일은 파라미터 튜닝이 아니라 로직을 다시 만든 것이다:

  진입  : 직전봉이 밴드 '밖'에서 마감 -> 당봉이 밴드 '안'으로 복귀 마감 (되돌림 확인)
          하단 복귀 -> LONG, 상단 복귀 -> SHORT. 밴드는 전부 [i-1] 기준(미래참조 금지).
  청산  : (a) 중심선 회귀  또는  (b) 반대편 밴드  — 옵션으로 둘 다 측정
  손절  : 진입 시점 밴드 바깥으로 sl_atr_mult*ATR + 절대 캡
  국면  : ADX 상한 / MA 기울기 / 밴드폭 분위수 중 선택. 스퀴즈 극단은 진입 금지.
  보조  : RSI 또는 %B 임계 (옵션)
  시간  : 장 초반/마감 임박 제외 (옵션), 마지막 봉 강제청산은 항상 적용

미래참조 방지 규약: 진입/필터 판정에 쓰는 모든 지표는 [i-1] 이하만 참조한다.
당봉 종가 c[i]는 '되돌림 확인'에만 쓰고, 그 시점에 체결하므로 미래참조가 아니다
(라이브에서 5분봉 마감 틱을 보고 진입하는 것과 동일).
"""
import sys, os, sqlite3
import numpy as np
import pandas as pd

# [2026-08-12 지표 단일화] 자체 구현 지표를 indicators.py로 교체.
# 교체 대상은 '계산'뿐이며 전략 로직·비용 모델·진입 조건은 건드리지 않는다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicators as I

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "futures_data.db")
INIT_CAPITAL = 50_000_000


# ── 지표 (전부 순수 함수, 당봉 제외 규약은 호출부에서 [i-1]로 지킨다) ──────────
# [2026-08-12] wilder_adx / rsi 자체 구현을 indicators.py로 이관.
# 기존 함수명을 유지해 호출부를 바꾸지 않는다(동작 무변경 리팩터링).
def wilder_adx(high, low, close, window=14):
    return I.wilder_adx(high, low, close, window)


def rsi(close, window=14):
    return I.wilder_rsi(I.Window(np.asarray(close, dtype=float)), window)


def load(code="10500000", session_filter=True):
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        f"SELECT date,open,high,low,close,volume FROM futures_ohlcv WHERE code='{code}' ORDER BY date ASC", conn)
    conn.close()
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df = df.dropna(subset=['dt']).set_index('dt')
    df['date_day'] = df['date'].str[:8]
    if session_filter:
        # [2026-08-12] indicators.filter_session 위임 (FEED_B와 같은 세션 범위)
        df = I.filter_session(df, *I.FEED_B.session)
    return df


def run_bb_meanrev(df,
                   bb_window=20, bb_sigma=2.0,
                   exit_mode='mid',              # 'mid' | 'opposite'
                   sl_atr_mult=1.0, sl_hard_cap_pt=None,
                   regime='none',                # 'none'|'adx'|'ma_slope'|'bw_quantile'
                   adx_max=25.0, adx_window=14,
                   ma_period=200, ma_slope_bars=20, ma_slope_max_pct=0.05,
                   bw_hi_quantile=0.75, bw_window=100,
                   squeeze_block_quantile=0.10,  # 밴드폭 최하위 분위수 미만이면 진입 금지 (None=끔)
                   aux='none', rsi_window=14, rsi_long_max=35.0, rsi_short_min=65.0,
                   pctb_long_max=0.10, pctb_short_min=0.90,
                   entry_start=(9, 30), entry_end=(15, 0), use_time_filter=True,
                   point_value=50_000, commission_rate=0.000065, slip_pt=0.25,
                   max_hold_bars=None,
                   return_trades=False):
    n = len(df)
    if n < max(bb_window, bw_window, ma_period, 250) + 20:
        return None
    o = df['open'].values.astype(float); h = df['high'].values.astype(float)
    l = df['low'].values.astype(float);  c = df['close'].values.astype(float)
    dk = df['date_day'].values; ts = df.index

    # [2026-08-12] 볼린저·밴드폭·분위수·%B를 indicators.py로 위임.
    # 밴드폭 분모는 실제 가격 공간(Series space="actual") — back-adjust 미사용이므로 그대로.
    mid, up, lo = I.bollinger_series(c, bb_window, bb_sigma, ddof=1)
    bw = I.bandwidth_series(up, lo, I.Series(mid, "actual"))
    pctb = I.percent_b_series(c, up, lo)
    bw_hi = I.rolling_quantile(bw, bw_window, bw_hi_quantile)
    bw_sq = (I.rolling_quantile(bw, bw_window, squeeze_block_quantile)
             if squeeze_block_quantile is not None else None)
    adx = wilder_adx(h, l, c, adx_window) if regime == 'adx' else None
    ma  = I.moving_average_series(c, ma_period) if regime == 'ma_slope' else None
    rs  = rsi(c, rsi_window) if aux == 'rsi' else None

    # 일별 ATR (전일까지) — 손절폭용
    dd = df.groupby('date_day').agg(hh=('high','max'), ll=('low','min'), cc=('close','last')).reset_index()
    dd['pc'] = dd.cc.shift(1)
    # [2026-08-12] TR·칼만ATR을 indicators.py로 위임 (수식 동일)
    tr = pd.Series(I.true_range(dd.hh, dd.ll, dd.pc), index=dd.index)
    path = list(I.kalman_atr(tr.values))
    amap = {k: (float(path[i-1]) if i > 0 and path[i-1] > 0 else 2.0) for i, k in enumerate(dd.date_day)}

    cap = float(INIT_CAPITAL); equity = [cap]; pnls = []; wins = 0
    pos = 0; ep = 0.0; sl = 0.0; tgt = 0.0; eidx = 0
    trades = []
    warm = max(bb_window, bw_window, (ma_period if ma is not None else 0), (adx_window*3 if adx is not None else 0)) + 2

    for i in range(warm, n):
        newday = (dk[i] != dk[i-1])
        last_of_day = (i == n-1) or (dk[i+1] != dk[i])
        atr = amap.get(dk[i], 2.0)

        # ── 보유 중 ──
        if pos != 0:
            xp = None; reason = None
            if last_of_day:
                xp, reason = c[i], '강제청산'
            elif pos == 1:
                if l[i] <= sl:  xp, reason = min(c[i], max(sl, l[i])), '손절'
                elif h[i] >= tgt: xp, reason = min(c[i], max(tgt, l[i])), '목표도달'
            else:
                if h[i] >= sl:  xp, reason = max(c[i], min(sl, h[i])), '손절'
                elif l[i] <= tgt: xp, reason = max(c[i], min(tgt, h[i])), '목표도달'
            if xp is None and max_hold_bars and (i - eidx) >= max_hold_bars:
                xp, reason = c[i], '보유한도'
            if xp is not None:
                raw = (xp - ep) if pos == 1 else (ep - xp)
                gain = (raw - slip_pt) * point_value - ep*point_value*commission_rate*2
                cap += gain; equity.append(cap); pnls.append(gain); wins += int(gain > 0)
                if return_trades:
                    trades.append(dict(entry_time=ts[eidx], exit_time=ts[i], direction='LONG' if pos==1 else 'SHORT',
                                       entry_price=ep, exit_price=xp, pnl_pt=raw-slip_pt, reason=reason,
                                       day=dk[i], gain=gain))
                pos = 0
            continue

        # ── 신규 진입 판정 (모든 지표는 [i-1] 이하) ──
        if last_of_day or newday: continue
        if np.isnan(lo[i-1]) or np.isnan(lo[i-2]) or np.isnan(bw_hi[i-1]): continue
        if use_time_filter:
            hm = (ts[i].hour, ts[i].minute)
            if hm < entry_start or hm >= entry_end: continue
        # 스퀴즈 극단 회피
        if bw_sq is not None and not np.isnan(bw_sq[i-1]) and bw[i-1] < bw_sq[i-1]: continue
        # 국면 필터
        if regime == 'adx':
            if np.isnan(adx[i-1]) or adx[i-1] > adx_max: continue
        elif regime == 'ma_slope':
            if i-1-ma_slope_bars < 0 or np.isnan(ma[i-1]) or np.isnan(ma[i-1-ma_slope_bars]): continue
            if abs(ma[i-1]-ma[i-1-ma_slope_bars])/ma[i-1-ma_slope_bars]*100 > ma_slope_max_pct: continue
        elif regime == 'bw_quantile':
            if bw[i-1] > bw_hi[i-1]: continue

        long_sig  = (c[i-1] < lo[i-2]) and (c[i] > lo[i-1])
        short_sig = (c[i-1] > up[i-2]) and (c[i] < up[i-1])
        if not (long_sig or short_sig): continue
        if aux == 'rsi':
            if long_sig  and (np.isnan(rs[i-1]) or rs[i-1] > rsi_long_max):  continue
            if short_sig and (np.isnan(rs[i-1]) or rs[i-1] < rsi_short_min): continue
        elif aux == 'pctb':
            if long_sig  and (np.isnan(pctb[i-1]) or pctb[i-1] > pctb_long_max):  continue
            if short_sig and (np.isnan(pctb[i-1]) or pctb[i-1] < pctb_short_min): continue

        d = 1 if long_sig else -1
        ep = c[i] + slip_pt*d          # 진입 슬리피지는 불리한 방향
        eidx = i; pos = d
        band = lo[i-1] if d == 1 else up[i-1]
        raw_sl = sl_atr_mult * atr
        if sl_hard_cap_pt is not None: raw_sl = min(raw_sl, sl_hard_cap_pt)
        sl  = band - raw_sl if d == 1 else band + raw_sl
        tgt = (mid[i-1] if exit_mode == 'mid' else (up[i-1] if d == 1 else lo[i-1]))

    total = len(pnls)
    if total == 0: return None
    eq = np.array(equity); pk = np.maximum.accumulate(eq)
    cum = np.cumsum([t['pnl_pt'] for t in trades]) if return_trades else None
    res = dict(trades=total, win_rate=wins/total*100,
               pf=(sum(p for p in pnls if p>0)/abs(sum(p for p in pnls if p<0))) if any(p<0 for p in pnls) else 999.0,
               mdd=((pk-eq)/pk*100).max(), final_capital=cap,
               worst_loss_pt=min(pnls)/point_value,
               avg_win_pt=(np.mean([p for p in pnls if p>0])/point_value) if any(p>0 for p in pnls) else 0.0,
               avg_loss_pt=(np.mean([p for p in pnls if p<0])/point_value) if any(p<0 for p in pnls) else 0.0)
    if return_trades:
        res['trade_log'] = trades
        c2 = np.concatenate([[0.0], cum]); res['mdd_pt'] = float((np.maximum.accumulate(c2)-c2).max())
    return res
