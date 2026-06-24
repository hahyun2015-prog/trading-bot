# -*- coding: utf-8 -*-
"""
Parabolic SAR 장기 검증 백테스트
전체 DB 기간: 2025-01-10 ~ 2026-06-23 (약 11개월)
Kalman 하이브리드(현재 실전)와 비교
"""
import sqlite3, pandas as pd, numpy as np, os, sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = r"c:\Antigravity\AI_T_Agent\futures_data.db"
CODE    = '10500000'
PV      = 50_000
MARGIN_RATE = 0.10
MARGIN_CAP  = 0.30
SLIP_FEE    = 0.05
INIT_CAP    = 31_000_000
KF_Q, KF_R = 0.0001, 0.5
KF_SL_MULT  = 3.4
ATR_Q, ATR_R = 0.002, 0.2

def load_all():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        f"SELECT date, open, high, low, close FROM futures_ohlcv "
        f"WHERE code='{CODE}' ORDER BY date ASC", conn)
    conn.close()
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['dt'], inplace=True)
    df.set_index('dt', inplace=True)
    df['date_day'] = df['date'].str[:8]
    return df

def build_features(df):
    closes = df['close'].values
    kf_x, kf_P = closes[0], 1.0
    kf_vals = np.zeros(len(closes))
    for i, c in enumerate(closes):
        if i == 0: kf_vals[i] = kf_x; continue
        kf_P += KF_Q; K = kf_P / (kf_P + KF_R)
        kf_x += K * (c - kf_x); kf_P = (1-K) * kf_P; kf_vals[i] = kf_x
    df = df.copy()
    df['kf']       = kf_vals
    df['err']      = df['close'] - df['kf']
    df['std']      = df['err'].rolling(20).std().fillna(0.5)
    df['upper']    = df['kf'] + df['std']
    df['lower']    = df['kf'] - df['std']
    df['tp_long']  = df['kf'] + 3.0 * df['std']
    df['tp_short'] = df['kf'] - 3.0 * df['std']
    return df

def build_sar(df, af_init=0.02, af_step=0.02, af_max=0.20):
    h, l = df['high'].values, df['low'].values
    n = len(df)
    sar = np.zeros(n); ep = np.zeros(n); af = np.full(n, af_init); bull = np.ones(n, dtype=bool)
    sar[0] = l[0]; ep[0] = h[0]; bull[0] = True
    for i in range(1, n):
        pb, ps, pe, pa = bull[i-1], sar[i-1], ep[i-1], af[i-1]
        if pb:
            sar[i] = ps + pa * (pe - ps)
            sar[i] = min(sar[i], l[i-1], l[max(0,i-2)])
            if l[i] < sar[i]:
                bull[i]=False; sar[i]=pe; ep[i]=l[i]; af[i]=af_init
            else:
                bull[i]=True; ep[i]=max(pe,h[i]); af[i]=min(pa+af_step,af_max) if ep[i]>pe else pa
        else:
            sar[i] = ps - pa * (ps - pe)
            sar[i] = max(sar[i], h[i-1], h[max(0,i-2)])
            if h[i] > sar[i]:
                bull[i]=True; sar[i]=pe; ep[i]=h[i]; af[i]=af_init
            else:
                bull[i]=False; ep[i]=min(pe,l[i]); af[i]=min(pa+af_step,af_max) if ep[i]<pe else pa
    df = df.copy(); df['sar'] = sar; df['sar_bull'] = bull
    return df

def build_atr(df):
    daily = df.groupby('date_day').agg(high=('high','max'), low=('low','min'), close=('close','last'))
    daily['tr'] = daily['high'] - daily['low']
    kf_atr, P = daily['tr'].iloc[0], 1.0
    atr_map = {}
    for day, row in daily.iterrows():
        P += ATR_Q; K = P/(P+ATR_R)
        kf_atr += K*(row['tr']-kf_atr); P=(1-K)*P; atr_map[day] = kf_atr
    return atr_map

def run_sim(df, atr_map, mode='A', **params):
    kf_v=df['kf'].values; std_v=df['std'].values; up_v=df['upper'].values; lo_v=df['lower'].values
    tpl_v=df['tp_long'].values; tps_v=df['tp_short'].values
    hi_v=df['high'].values; lw_v=df['low'].values; cl_v=df['close'].values
    sar_v=df['sar'].values if 'sar' in df.columns else np.zeros(len(df))
    sar_b=df['sar_bull'].values if 'sar_bull' in df.columns else np.ones(len(df),dtype=bool)

    cap = float(INIT_CAP); equity=[cap]; pnls=[]; sl_c=tp_c=ts_c=flat_c=0
    fp=cl_v[0]; mp=fp*PV*MARGIN_RATE; budget=INIT_CAP*MARGIN_CAP
    CONTRACTS=max(1,int(budget//mp)) if mp>0 else 1
    grouped=df.groupby('date_day'); days=sorted(grouped.groups.keys())

    for day in days:
        day_idx=grouped.get_group(day).index
        candles=[(df.index.get_loc(i),i) for i in day_idx]
        if len(candles)<5: continue
        day_atr=atr_map.get(day,5.0); pos=0; entry=0.0; peak=0.0
        chan_high=0.0; trade_cnt=0

        for seq,(loc,idx) in enumerate(candles):
            is_last=(seq==len(candles)-1)
            c_hi=hi_v[loc]; c_lo=lw_v[loc]; c_cl=cl_v[loc]
            c_std=std_v[loc]; c_atr=day_atr

            if pos!=0:
                if is_last:
                    ep=c_cl-SLIP_FEE if pos==1 else c_cl+SLIP_FEE
                    gain=((ep-entry)*pos-SLIP_FEE*2)*PV*CONTRACTS
                    cap+=gain; pnls.append(gain); equity.append(cap); flat_c+=1; pos=0; break

                exit_price=None; exit_type=None

                if mode=='A':  # Kalman 하이브리드 (현재 실전)
                    sl_limit=max(min(KF_SL_MULT*c_std,1.2*c_atr),2.0)
                    tp_p=tpl_v[loc] if pos==1 else tps_v[loc]
                    if pos==1:
                        if c_lo<=entry-sl_limit: exit_price=entry-sl_limit; exit_type='SL'
                        elif c_hi>=tp_p: exit_price=tp_p; exit_type='TP'
                        else:
                            peak=max(peak,c_hi)
                            if (peak-entry)>=1.5*c_std and c_cl<=peak-0.5*c_std:
                                exit_price=c_cl; exit_type='TS'
                    else:
                        if c_hi>=entry+sl_limit: exit_price=entry+sl_limit; exit_type='SL'
                        elif c_lo<=tp_p: exit_price=tp_p; exit_type='TP'
                        else:
                            peak=min(peak,c_lo) if peak>0 else c_lo
                            if (entry-peak)>=1.5*c_std and c_cl>=peak+0.5*c_std:
                                exit_price=c_cl; exit_type='TS'

                elif mode=='SAR':  # Parabolic SAR
                    sl_init=max(c_atr*1.0,2.0)
                    tp_p=tpl_v[loc] if pos==1 else tps_v[loc]
                    if pos==1:
                        if c_lo<=entry-sl_init: exit_price=entry-sl_init; exit_type='SL'
                        elif not sar_b[loc] or c_lo<=sar_v[loc]:
                            exit_price=max(sar_v[loc], entry-sl_init); exit_type='TS'
                        elif c_hi>=tp_p: exit_price=tp_p; exit_type='TP'
                    else:
                        if c_hi>=entry+sl_init: exit_price=entry+sl_init; exit_type='SL'
                        elif sar_b[loc] or c_hi>=sar_v[loc]:
                            exit_price=min(sar_v[loc], entry+sl_init); exit_type='TS'
                        elif c_lo<=tp_p: exit_price=tp_p; exit_type='TP'

                elif mode=='SAR_PURE':  # SAR 단독 청산 (손절 없음)
                    tp_p=tpl_v[loc] if pos==1 else tps_v[loc]
                    if pos==1:
                        if not sar_b[loc] or c_lo<=sar_v[loc]:
                            exit_price=sar_v[loc]; exit_type='TS'
                        elif c_hi>=tp_p: exit_price=tp_p; exit_type='TP'
                    else:
                        if sar_b[loc] or c_hi>=sar_v[loc]:
                            exit_price=sar_v[loc]; exit_type='TS'
                        elif c_lo<=tp_p: exit_price=tp_p; exit_type='TP'

                if exit_price is not None:
                    if pos==1: ep=exit_price-SLIP_FEE; gain=((ep-entry)-SLIP_FEE*2)*PV*CONTRACTS
                    else: ep=exit_price+SLIP_FEE; gain=((entry-ep)-SLIP_FEE*2)*PV*CONTRACTS
                    cap+=gain; pnls.append(gain); equity.append(cap)
                    if exit_type=='SL': sl_c+=1
                    elif exit_type=='TP': tp_c+=1
                    elif exit_type=='TS': ts_c+=1
                    pos=0; peak=0.0; chan_high=0.0
            else:
                if not is_last and trade_cnt<3:
                    if c_hi>=up_v[loc]: pos=1; entry=up_v[loc]+SLIP_FEE; peak=entry; trade_cnt+=1
                    elif c_lo<=lo_v[loc]: pos=-1; entry=lo_v[loc]-SLIP_FEE; peak=entry; trade_cnt+=1

    total=len(pnls)
    if total==0: return None
    wins=sum(1 for p in pnls if p>0)
    gs=sum(p for p in pnls if p>0); ls=abs(sum(p for p in pnls if p<0))
    pf=gs/ls if ls>0 else 999.0
    eq=np.array(equity); pk=np.maximum.accumulate(eq)
    mdd=float(np.max((pk-eq)/pk*100))
    pnl_pt=sum(p/(PV*CONTRACTS) for p in pnls)
    # 월별 수익
    monthly = {}
    day_keys = days
    # 간단히 전체 수익만 계산
    return {
        'trades':total,'wins':wins,'win_rate':wins/total*100,
        'pf':pf,'pnl_pt':pnl_pt,'mdd':mdd,
        'sl':sl_c,'tp':tp_c,'ts':ts_c,'flat':flat_c,
        'final_cap':cap
    }

def main():
    print("="*70)
    print("  Parabolic SAR 장기 검증 백테스트 (전체 DB: 약 11개월)")
    print("="*70)
    print("[1/4] 데이터 로드...")
    df = load_all()
    print(f"     {len(df):,}개 캔들 | {df['date_day'].nunique()} 거래일")
    print(f"     기간: {df['date_day'].min()} ~ {df['date_day'].max()}")

    print("[2/4] Kalman 지표 계산...")
    df = build_features(df)

    print("[3/4] Parabolic SAR 계산...")
    df = build_sar(df)

    print("[4/4] 일봉 ATR 계산...")
    atr_map = build_atr(df)

    print("\n─── 시뮬레이션 실행 중 ───\n")
    strategies = [
        ('A',        '🔵 Kalman 하이브리드 (현재 실전)         '),
        ('SAR',      '🟡 Parabolic SAR + ATR 초기손절         '),
        ('SAR_PURE', '🟢 Parabolic SAR 순수 (손절 없음)        '),
    ]

    results = []
    for mode, label in strategies:
        r = run_sim(df.copy(), atr_map, mode=mode)
        if r: results.append((label, mode, r))
        print(f"     {label.strip()}: {'완료' if r else '결과없음'}")

    print("\n" + "="*85)
    print(f"  {'전략':45s}| {'거래':>5} | {'승률':>7} | {'PF':>6} | {'PnL(pt)':>10} | {'MDD':>6}")
    print("-"*85)
    for label, mode, r in results:
        print(f"  {label}| {r['trades']:>5} | {r['win_rate']:>6.1f}% | {r['pf']:>6.2f} | {r['pnl_pt']:>+9.0f}pt | {r['mdd']:>5.2f}%")
    print("="*85)

    # 연도별 분기 분석
    print("\n[분기별 성과 비교 - Kalman vs SAR]")
    quarters = [
        ('2025Q1', '20250101', '20250331'),
        ('2025Q2', '20250401', '20250630'),
        ('2025Q3', '20250701', '20250930'),
        ('2025Q4', '20251001', '20251231'),
        ('2026Q1', '20260101', '20260331'),
        ('2026Q2', '20260401', '20260624'),
    ]
    print(f"  {'분기':8} | {'Kalman PnL':>11} | {'SAR PnL':>10} | {'차이':>8}")
    print("  " + "-"*50)
    for qname, qs, qe in quarters:
        qdf = df[(df['date_day'] >= qs) & (df['date_day'] <= qe)]
        if len(qdf) < 100: continue
        qatr = {k:v for k,v in atr_map.items() if qs<=k<=qe}
        rA = run_sim(qdf.copy(), qatr, mode='A')
        rS = run_sim(qdf.copy(), qatr, mode='SAR')
        pA = rA['pnl_pt'] if rA else 0
        pS = rS['pnl_pt'] if rS else 0
        diff = pS - pA
        flag = '✅' if diff > 0 else '❌'
        print(f"  {qname:8} | {pA:>+10.0f}pt | {pS:>+9.0f}pt | {flag} {diff:>+6.0f}pt")

    print("\n[결론]")
    if len(results) >= 2:
        rA = next((r for _,m,r in results if m=='A'), None)
        rS = next((r for _,m,r in results if m=='SAR'), None)
        if rA and rS:
            diff = rS['pnl_pt'] - rA['pnl_pt']
            print(f"  Kalman PnL: {rA['pnl_pt']:+.0f}pt | 승률: {rA['win_rate']:.1f}% | MDD: {rA['mdd']:.2f}%")
            print(f"  SAR    PnL: {rS['pnl_pt']:+.0f}pt | 승률: {rS['win_rate']:.1f}% | MDD: {rS['mdd']:.2f}%")
            print(f"  → SAR이 Kalman 대비 {diff:+.0f}pt {'우위' if diff>0 else '열위'}")
            if rS['mdd'] < rA['mdd']:
                print(f"  → MDD {rA['mdd']:.2f}% → {rS['mdd']:.2f}% 개선 ({rA['mdd']-rS['mdd']:+.2f}%p)")

if __name__ == '__main__':
    main()
