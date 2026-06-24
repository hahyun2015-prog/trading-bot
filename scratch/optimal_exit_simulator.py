# -*- coding: utf-8 -*-
"""
최적 손익절/청산 방법 연구 시뮬레이터
=========================================
현재 Kalman Static ATR (베이스라인) 대비 6가지 청산 전략 비교
기간: 2026-02-27 ~ 2026-06-24 (72 거래일)
진입: Kalman 밴드 돌파 (실전 엔진과 동일)
"""

import sqlite3
import pandas as pd
import numpy as np
import os
import sys
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────
DB_PATH      = r"c:\Antigravity\AI_T_Agent\futures_data.db"
CODE         = '10500000'
START_DATE   = '20260227'
END_DATE     = '20260624'

PV           = 50_000       # 미니선물 포인트당 가치 (50,000원)
MARGIN_RATE  = 0.10
MARGIN_CAP   = 0.30
SLIP_FEE     = 0.05         # 슬리피지 + 수수료 (pt)
INIT_CAP     = 31_000_000   # 실전 초기자본과 동일

# Kalman 파라미터 (실전 엔진 동일)
KF_Q, KF_R  = 0.0001, 0.5
KF_STD_WIN  = 20            # std_error 롤링 윈도우
KF_SL_MULT  = 3.4           # 실전 손절 배수 (kf_sl_mult)
ATR_Q       = 0.002         # 일봉 Kalman ATR Q
ATR_R       = 0.2           # 일봉 Kalman ATR R

# ─────────────────────────────────────────
# 1. 데이터 로드
# ─────────────────────────────────────────
def load_data():
    if not os.path.exists(DB_PATH):
        print(f"[-] DB 없음: {DB_PATH}"); return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        f"SELECT date, open, high, low, close FROM futures_ohlcv "
        f"WHERE code='{CODE}' AND date >= '{START_DATE}000000' AND date <= '{END_DATE}235959' "
        f"ORDER BY date ASC", conn)
    conn.close()
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['dt'], inplace=True)
    df.set_index('dt', inplace=True)
    df['date_day'] = df['date'].str[:8]
    return df

# ─────────────────────────────────────────
# 2. Kalman 필터 & 지표 계산
# ─────────────────────────────────────────
def build_kalman_features(df):
    """5분봉에 Kalman 필터 적용하여 진입 밴드, std, TP 계산"""
    closes = df['close'].values
    kf_x, kf_P = closes[0], 1.0
    kf_vals = np.zeros(len(closes))
    for i, c in enumerate(closes):
        if i == 0:
            kf_vals[i] = kf_x; continue
        kf_P += KF_Q
        K = kf_P / (kf_P + KF_R)
        kf_x = kf_x + K * (c - kf_x)
        kf_P = (1 - K) * kf_P
        kf_vals[i] = kf_x
    df = df.copy()
    df['kf']      = kf_vals
    df['err']     = df['close'] - df['kf']
    df['std']     = df['err'].rolling(KF_STD_WIN).std().fillna(0.5)
    df['upper']   = df['kf'] + df['std']      # 진입 밴드
    df['lower']   = df['kf'] - df['std']
    df['tp_long'] = df['kf'] + 3.0 * df['std']   # 3-Sigma 익절 목표
    df['tp_short']= df['kf'] - 3.0 * df['std']
    return df

def build_kalman_atr(df):
    """일봉 Kalman ATR 계산 → 동적 SL 상한선용"""
    daily = df.groupby('date_day').agg(high=('high','max'), low=('low','min'), close=('close','last'))
    daily['tr'] = (daily['high'] - daily['low'])
    kf_atr, P = daily['tr'].iloc[0], 1.0
    atr_map = {}
    for day, row in daily.iterrows():
        P += ATR_Q
        K = P / (P + ATR_R)
        kf_atr = kf_atr + K * (row['tr'] - kf_atr)
        P = (1 - K) * P
        atr_map[day] = kf_atr
    return atr_map   # {date_day_str: atr_value}

def build_parabolic_sar(df, af_init=0.02, af_step=0.02, af_max=0.20):
    """Parabolic SAR 계산"""
    high = df['high'].values
    low  = df['low'].values
    n    = len(df)
    sar  = np.zeros(n)
    ep   = np.zeros(n)   # Extreme Point
    af   = np.full(n, af_init)
    bull = np.ones(n, dtype=bool)  # True=상승장

    sar[0]  = low[0]
    ep[0]   = high[0]
    bull[0] = True

    for i in range(1, n):
        prev_bull = bull[i-1]
        prev_sar  = sar[i-1]
        prev_ep   = ep[i-1]
        prev_af   = af[i-1]

        if prev_bull:
            sar[i] = prev_sar + prev_af * (prev_ep - prev_sar)
            sar[i] = min(sar[i], low[i-1], low[max(0,i-2)])
            if low[i] < sar[i]:
                bull[i] = False
                sar[i]  = prev_ep
                ep[i]   = low[i]
                af[i]   = af_init
            else:
                bull[i] = True
                ep[i]   = max(prev_ep, high[i])
                af[i]   = min(prev_af + af_step, af_max) if ep[i] > prev_ep else prev_af
        else:
            sar[i] = prev_sar - prev_af * (prev_sar - prev_ep)
            sar[i] = max(sar[i], high[i-1], high[max(0,i-2)])
            if high[i] > sar[i]:
                bull[i] = True
                sar[i]  = prev_ep
                ep[i]   = high[i]
                af[i]   = af_init
            else:
                bull[i] = False
                ep[i]   = min(prev_ep, low[i])
                af[i]   = min(prev_af + af_step, af_max) if ep[i] < prev_ep else prev_af

    df = df.copy()
    df['sar']      = sar
    df['sar_bull'] = bull
    return df

# ─────────────────────────────────────────
# 3. 핵심 시뮬레이션 엔진
# ─────────────────────────────────────────
def simulate(df, atr_map, exit_mode='A', **params):
    """
    exit_mode:
      A = Kalman 하이브리드 (현재 실전, 베이스라인)
      B = Pure ATR 비례 동적 SL/TP
      C = Chandelier Exit (트레일링)
      D = Parabolic SAR 청산
      E = 볼린저 밴드 역추세 익절
      F = 복합 앙상블 (Chandelier TS + Kalman 3Sig TP + ATR SL)
    """
    arr       = df.to_dict('index')
    idx_list  = list(df.index)
    n         = len(idx_list)

    # 지표 배열
    kf_vals   = df['kf'].values
    std_vals  = df['std'].values
    upper_v   = df['upper'].values
    lower_v   = df['lower'].values
    tp_long_v = df['tp_long'].values
    tp_shrt_v = df['tp_short'].values
    high_v    = df['high'].values
    low_v     = df['low'].values
    close_v   = df['close'].values
    day_v     = df['date_day'].values

    # 볼린저 밴드 (E, F용)
    bb_win    = params.get('bb_win', 20)
    bb_sig    = params.get('bb_sig', 2.0)
    bb_upper  = df['close'].rolling(bb_win).mean() + bb_sig * df['close'].rolling(bb_win).std()
    bb_lower  = df['close'].rolling(bb_win).mean() - bb_sig * df['close'].rolling(bb_win).std()
    bbu_v     = bb_upper.values
    bbl_v     = bb_lower.values

    # Parabolic SAR (D용)
    sar_v     = df['sar'].values if 'sar' in df.columns else np.zeros(n)
    sar_bull  = df['sar_bull'].values if 'sar_bull' in df.columns else np.ones(n, dtype=bool)

    # Chandelier 파라미터
    chan_n    = params.get('chan_n', 22)   # lookback 봉수
    chan_k    = params.get('chan_k', 2.5)  # ATR 배수

    cap       = float(INIT_CAP)
    equity    = [cap]
    pnls      = []
    sl_cnt    = tp_cnt = ts_cnt = flat_cnt = 0

    first_p   = close_v[0]
    margin_p  = first_p * PV * MARGIN_RATE
    budget    = INIT_CAP * MARGIN_CAP
    CONTRACTS = max(1, int(budget // margin_p)) if margin_p > 0 else 1

    grouped   = df.groupby('date_day')
    days      = sorted(grouped.groups.keys())

    for day in days:
        day_idx = grouped.get_group(day).index
        candles = [(df.index.get_loc(i), i) for i in day_idx]
        if len(candles) < 5:
            continue

        day_atr  = atr_map.get(day, 5.0)
        pos      = 0          # 1=LONG, -1=SHORT, 0=없음
        entry    = 0.0
        peak     = 0.0        # LONG 최고가, SHORT 최저가 (트레일링용)
        chan_high = 0.0        # Chandelier 최고가 추적
        chan_low  = float('inf')  # Chandelier 최저가 추적
        trade_cnt = 0

        for seq, (loc, idx) in enumerate(candles):
            is_last = (seq == len(candles) - 1)
            c_hi  = high_v[loc];  c_lo = low_v[loc];  c_cl = close_v[loc]
            c_std = std_vals[loc]; c_kf = kf_vals[loc]
            c_atr = day_atr

            # ── 포지션 보유 중 ──
            if pos != 0:
                if is_last:
                    # 당일 마감 강제 청산
                    exit_p = c_cl - SLIP_FEE if pos == 1 else c_cl + SLIP_FEE
                    gain   = ((exit_p - entry) * pos - SLIP_FEE * 2) * PV * CONTRACTS
                    cap   += gain; pnls.append(gain); equity.append(cap)
                    flat_cnt += 1
                    pos = 0; break

                # ─── 전략별 청산 로직 ───
                exit_price = None
                exit_type  = None

                # ── A: Kalman 하이브리드 (실전 공식과 동일) ──
                if exit_mode == 'A':
                    sl_limit = max(min(KF_SL_MULT * c_std, 1.2 * c_atr), 2.0)
                    tp_price = tp_long_v[loc] if pos == 1 else tp_shrt_v[loc]

                    if pos == 1:
                        if c_lo <= entry - sl_limit:
                            exit_price = entry - sl_limit; exit_type = 'SL'
                        elif c_hi >= tp_price:
                            exit_price = tp_price; exit_type = 'TP'
                        else:
                            peak = max(peak, c_hi)
                            if (peak - entry) >= 1.5 * c_std and c_cl <= peak - 0.5 * c_std:
                                exit_price = c_cl; exit_type = 'TS'
                    else:
                        if c_hi >= entry + sl_limit:
                            exit_price = entry + sl_limit; exit_type = 'SL'
                        elif c_lo <= tp_price:
                            exit_price = tp_price; exit_type = 'TP'
                        else:
                            peak = min(peak, c_lo) if peak > 0 else c_lo
                            if (entry - peak) >= 1.5 * c_std and c_cl >= peak + 0.5 * c_std:
                                exit_price = c_cl; exit_type = 'TS'

                # ── B: Pure ATR 비례 동적 SL/TP ──
                elif exit_mode == 'B':
                    sl_mult = params.get('sl_mult', 1.0)
                    tp_mult = params.get('tp_mult', 2.0)
                    sl_pt   = max(c_atr * sl_mult, 2.0)
                    tp_pt   = c_atr * tp_mult
                    if pos == 1:
                        if c_lo <= entry - sl_pt:
                            exit_price = entry - sl_pt; exit_type = 'SL'
                        elif c_hi >= entry + tp_pt:
                            exit_price = entry + tp_pt; exit_type = 'TP'
                    else:
                        if c_hi >= entry + sl_pt:
                            exit_price = entry + sl_pt; exit_type = 'SL'
                        elif c_lo <= entry - tp_pt:
                            exit_price = entry - tp_pt; exit_type = 'TP'

                # ── C: Chandelier Exit (트레일링 전용) ──
                elif exit_mode == 'C':
                    sl_init = max(c_atr * 1.2, 2.0)
                    if pos == 1:
                        chan_high = max(chan_high, c_hi)
                        chan_sl   = chan_high - chan_k * c_atr
                        chan_sl   = max(chan_sl, entry - sl_init)
                        if c_lo <= chan_sl:
                            exit_price = chan_sl; exit_type = 'TS'
                        elif c_hi >= tp_long_v[loc]:
                            exit_price = tp_long_v[loc]; exit_type = 'TP'
                    else:
                        chan_low = min(chan_low, c_lo)
                        chan_sl  = chan_low + chan_k * c_atr
                        chan_sl  = min(chan_sl, entry + sl_init)
                        if c_hi >= chan_sl:
                            exit_price = chan_sl; exit_type = 'TS'
                        elif c_lo <= tp_shrt_v[loc]:
                            exit_price = tp_shrt_v[loc]; exit_type = 'TP'

                # ── D: Parabolic SAR ──
                elif exit_mode == 'D':
                    sl_init = max(c_atr * 1.0, 2.0)
                    if pos == 1:
                        sar_val  = sar_v[loc]
                        sar_bull_now = sar_bull[loc]
                        if c_lo <= entry - sl_init:
                            exit_price = entry - sl_init; exit_type = 'SL'
                        elif not sar_bull_now or c_lo <= sar_val:
                            exit_price = sar_val; exit_type = 'TS'
                        elif c_hi >= tp_long_v[loc]:
                            exit_price = tp_long_v[loc]; exit_type = 'TP'
                    else:
                        sar_val  = sar_v[loc]
                        sar_bull_now = sar_bull[loc]
                        if c_hi >= entry + sl_init:
                            exit_price = entry + sl_init; exit_type = 'SL'
                        elif sar_bull_now or c_hi >= sar_val:
                            exit_price = sar_val; exit_type = 'TS'
                        elif c_lo <= tp_shrt_v[loc]:
                            exit_price = tp_shrt_v[loc]; exit_type = 'TP'

                # ── E: 볼린저 밴드 역추세 익절 ──
                elif exit_mode == 'E':
                    sl_pt = max(c_atr * 1.2, 2.0)
                    bbu   = bbu_v[loc]; bbl = bbl_v[loc]
                    if pos == 1:
                        if c_lo <= entry - sl_pt:
                            exit_price = entry - sl_pt; exit_type = 'SL'
                        elif np.isfinite(bbu) and c_hi >= bbu:
                            exit_price = bbu; exit_type = 'TP'
                        else:
                            peak = max(peak, c_hi)
                            if (peak - entry) >= 1.5 * c_std and c_cl <= peak - 0.5 * c_std:
                                exit_price = c_cl; exit_type = 'TS'
                    else:
                        if c_hi >= entry + sl_pt:
                            exit_price = entry + sl_pt; exit_type = 'SL'
                        elif np.isfinite(bbl) and c_lo <= bbl:
                            exit_price = bbl; exit_type = 'TP'
                        else:
                            peak = min(peak, c_lo) if peak > 0 else c_lo
                            if (entry - peak) >= 1.5 * c_std and c_cl >= peak + 0.5 * c_std:
                                exit_price = c_cl; exit_type = 'TS'

                # ── F: 복합 앙상블 (Chandelier TS + Kalman 3Sig TP + ATR SL) ──
                elif exit_mode == 'F':
                    sl_pt = max(min(KF_SL_MULT * c_std, 1.5 * c_atr), 2.0)  # A보다 넓은 cap
                    tp_price = tp_long_v[loc] if pos == 1 else tp_shrt_v[loc]
                    if pos == 1:
                        chan_high = max(chan_high, c_hi)
                        chan_sl   = chan_high - chan_k * c_atr
                        chan_sl   = max(chan_sl, entry - sl_pt)
                        if c_lo <= entry - sl_pt:
                            exit_price = entry - sl_pt; exit_type = 'SL'
                        elif c_hi >= tp_price:
                            exit_price = tp_price; exit_type = 'TP'
                        elif c_lo <= chan_sl and (chan_high - entry) >= 1.0 * c_std:
                            exit_price = chan_sl; exit_type = 'TS'
                    else:
                        chan_low = min(chan_low, c_lo)
                        chan_sl  = chan_low + chan_k * c_atr
                        chan_sl  = min(chan_sl, entry + sl_pt)
                        if c_hi >= entry + sl_pt:
                            exit_price = entry + sl_pt; exit_type = 'SL'
                        elif c_lo <= tp_price:
                            exit_price = tp_price; exit_type = 'TP'
                        elif c_hi >= chan_sl and (entry - chan_low) >= 1.0 * c_std:
                            exit_price = chan_sl; exit_type = 'TS'

                # ── 청산 실행 ──
                if exit_price is not None:
                    if pos == 1:
                        exit_p = exit_price - SLIP_FEE
                        gain = ((exit_p - entry) - SLIP_FEE * 2) * PV * CONTRACTS
                    else:
                        exit_p = exit_price + SLIP_FEE
                        gain = ((entry - exit_p) - SLIP_FEE * 2) * PV * CONTRACTS
                    cap += gain; pnls.append(gain); equity.append(cap)
                    if exit_type == 'SL': sl_cnt += 1
                    elif exit_type == 'TP': tp_cnt += 1
                    elif exit_type == 'TS': ts_cnt += 1
                    pos = 0; peak = 0.0; chan_high = 0.0; chan_low = float('inf')

            # ── 신규 진입 ──
            else:
                if not is_last and trade_cnt < 3:
                    c_up = upper_v[loc]; c_lo_band = lower_v[loc]
                    if c_hi >= c_up:
                        pos = 1; entry = c_up + SLIP_FEE
                        peak = entry; chan_high = entry; chan_low = float('inf')
                        trade_cnt += 1
                    elif c_lo <= c_lo_band:
                        pos = -1; entry = c_lo_band - SLIP_FEE
                        peak = entry; chan_low = entry; chan_high = 0.0
                        trade_cnt += 1

    total = len(pnls)
    if total == 0:
        return None
    wins      = sum(1 for p in pnls if p > 0)
    gain_sum  = sum(p for p in pnls if p > 0)
    loss_sum  = abs(sum(p for p in pnls if p < 0))
    pf        = gain_sum / loss_sum if loss_sum > 0 else 999.0
    eq_arr    = np.array(equity)
    peaks_arr = np.maximum.accumulate(eq_arr)
    mdd       = float(np.max((peaks_arr - eq_arr) / peaks_arr * 100))
    pnl_pt    = sum(p / (PV * CONTRACTS) for p in pnls)

    return {
        'trades': total,
        'wins':   wins,
        'win_rate': wins / total * 100,
        'pf':     pf,
        'pnl_pt': pnl_pt,
        'mdd':    mdd,
        'sl':     sl_cnt,
        'tp':     tp_cnt,
        'ts':     ts_cnt,
        'flat':   flat_cnt,
        'final_cap': cap,
    }

# ─────────────────────────────────────────
# 4. 메인 실행
# ─────────────────────────────────────────
def main():
    print("=" * 75)
    print("  최적 손익절/청산 방법 연구 시뮬레이터")
    print(f"  기간: {START_DATE} ~ {END_DATE}  |  종목: KOSPI200 미니 ({CODE})")
    print("=" * 75)

    print("[1/4] 5분봉 데이터 로드 중...")
    df = load_data()
    if df.empty:
        print("[-] 데이터 없음."); return

    print(f"     로드 완료: {len(df):,}개 캔들")

    print("[2/4] Kalman 지표 계산 중...")
    df = build_kalman_features(df)

    print("[3/4] Parabolic SAR 계산 중...")
    df = build_parabolic_sar(df, af_init=0.02, af_step=0.02, af_max=0.20)

    print("[4/4] 일봉 Kalman ATR 계산 중...")
    atr_map = build_kalman_atr(df)

    print("\n─── 6가지 청산 전략 시뮬레이션 실행 ───\n")

    strategies = [
        ('A', '🏆 Kalman 하이브리드 (현재 실전)',      {},                              '현재 실전 엔진과 완전 동일'),
        ('B', '  Pure ATR 비례 (SL×1.0 / TP×2.0)',    {'sl_mult':1.0, 'tp_mult':2.0}, 'ATR×1.0 손절 / ATR×2.0 익절'),
        ('B', '  Pure ATR 비례 (SL×0.8 / TP×1.6)',    {'sl_mult':0.8, 'tp_mult':1.6}, 'ATR×0.8 손절 / ATR×1.6 익절'),
        ('C', '  Chandelier Exit (N=22, K=2.5)',       {'chan_n':22, 'chan_k':2.5},     '22봉 최고가 - 2.5×ATR 트레일링'),
        ('C', '  Chandelier Exit (N=14, K=2.0)',       {'chan_n':14, 'chan_k':2.0},     '14봉 최고가 - 2.0×ATR 트레일링'),
        ('D', '  Parabolic SAR (AF=0.02, Max=0.20)',  {},                              'SAR 역전 시 청산'),
        ('E', '  볼린저 밴드 역추세 (20봉, 2.0σ)',     {'bb_win':20, 'bb_sig':2.0},    '반대 밴드 터치 시 익절'),
        ('E', '  볼린저 밴드 역추세 (20봉, 1.5σ)',     {'bb_win':20, 'bb_sig':1.5},    '타이트 밴드 터치 시 익절'),
        ('F', '  복합 앙상블 (Chandelier+3Sig+ATR)',  {'chan_k':2.5},                  'Chandelier TS + Kalman 3Sig TP + ATR SL'),
    ]

    results = []
    for mode, label, params, desc in strategies:
        res = simulate(df.copy(), atr_map, exit_mode=mode, **params)
        if res:
            results.append((label, res, desc))

    # ─── 결과 출력 ───
    print("\n" + "=" * 100)
    print(f"  {'전략':42s} | {'거래':>5} | {'승률':>7} | {'PF':>5} | {'누적PnL(pt)':>12} | {'MDD':>6} | SL/TP/TS")
    print("-" * 100)
    baseline_pnl = None
    for label, r, desc in results:
        flag = ''
        if baseline_pnl is None:
            baseline_pnl = r['pnl_pt']
            flag = ' ← 베이스라인'
        else:
            diff = r['pnl_pt'] - baseline_pnl
            flag = f" ({diff:+.1f}pt)" if diff != 0 else ''
        print(
            f"  {label:42s} | {r['trades']:>5} | {r['win_rate']:>6.1f}% | {r['pf']:>5.2f}"
            f" | {r['pnl_pt']:>+11.1f}pt{flag}"
            f" | {r['mdd']:>5.2f}%"
            f" | {r['sl']}/{r['tp']}/{r['ts']}"
        )
    print("=" * 100)

    # ─── 상세 분석 ───
    print("\n[상세 분석 - 각 전략 설명]")
    for label, r, desc in results:
        print(f"  {label.strip():40s} → {desc}")

    # ─── 종합 순위 ───
    print("\n[종합 평가 점수 (PnL 40% + 승률 20% + PF 20% + MDD 20%)]")
    scores = []
    pnl_vals = [r['pnl_pt'] for _, r, _ in results]
    wr_vals  = [r['win_rate'] for _, r, _ in results]
    pf_vals  = [r['pf'] for _, r, _ in results]
    mdd_vals = [r['mdd'] for _, r, _ in results]

    pnl_max, pnl_min = max(pnl_vals), min(pnl_vals)
    wr_max, wr_min   = max(wr_vals), min(wr_vals)
    pf_max, pf_min   = max(pf_vals), min(pf_vals)
    mdd_max, mdd_min = max(mdd_vals), min(mdd_vals)

    def norm(v, mn, mx): return (v - mn) / (mx - mn) if mx > mn else 0.5

    for label, r, desc in results:
        s_pnl = norm(r['pnl_pt'], pnl_min, pnl_max) * 40
        s_wr  = norm(r['win_rate'], wr_min, wr_max) * 20
        s_pf  = norm(r['pf'], pf_min, pf_max) * 20
        s_mdd = norm(mdd_max - r['mdd'], 0, mdd_max - mdd_min) * 20  # MDD 낮을수록 좋음
        total_score = s_pnl + s_wr + s_pf + s_mdd
        scores.append((label.strip(), total_score, r))

    scores.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  {'순위':>4} | {'전략':42s} | {'점수':>6} | {'PnL(pt)':>12} | {'승률':>7} | {'PF':>5} | {'MDD':>6}")
    print("  " + "-" * 90)
    for rank, (label, score, r) in enumerate(scores, 1):
        medal = ['🥇','🥈','🥉','   ','   ','   ','   ','   ','   '][rank-1]
        print(f"  {medal} {rank:>2} | {label:42s} | {score:>6.1f} | {r['pnl_pt']:>+11.1f}pt | {r['win_rate']:>6.1f}% | {r['pf']:>5.2f} | {r['mdd']:>5.2f}%")

    print("\n[결론]")
    best_label, best_score, best_r = scores[0]
    print(f"  ✅ 최우수 전략: {best_label}")
    print(f"     누적 PnL: {best_r['pnl_pt']:+.1f}pt | 승률: {best_r['win_rate']:.1f}% | PF: {best_r['pf']:.2f} | MDD: {best_r['mdd']:.2f}%")

    if scores[0][0] != scores[0][0] or '실전' in scores[0][0]:
        print("  → 현재 실전 전략이 최적입니다.")
    else:
        print(f"  → 현재 실전 전략 대비 PnL {best_r['pnl_pt'] - results[0][1]['pnl_pt']:+.1f}pt 개선 가능합니다.")

if __name__ == '__main__':
    main()
