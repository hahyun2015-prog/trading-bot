# -*- coding: utf-8 -*-
"""
수수료 + 슬리피지 반영 수익률 분석
=====================================
키움증권 실제 비용 구조를 적용하여
비용 전/후 수익률 및 손익분기 조건을 도출합니다.
"""
import sys, io, sqlite3, os, json
from datetime import datetime
import datetime as _dt
import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_FUTURES = os.path.join(workspace_root, "futures_data.db")
DB_UNIFIED = os.path.join(workspace_root, "unified_data.db")

# ─────────────────────────────────────────────────────────
# 키움증권 실제 비용 구조
# ─────────────────────────────────────────────────────────
COSTS = {
    # 선물 수수료 (편도): 약 0.000045 × 계약가치
    # 키움증권 미니선물: 통상 65~70원/백만원 notional
    'futures_commission_rate': 0.000065,   # 편도 수수료율 (왕복 x2)
    # 슬리피지: 미니선물 0.05pt (최소호가) × 2 (진입+청산)
    'futures_slippage_pt': 0.10,           # 왕복 슬리피지 (pt)
    # 주식 수수료 (온라인): 0.015% 편도
    'stock_commission_rate': 0.00015,      # 편도
    # 증권거래세 (매도 시만): 2025년 0.18%
    'stock_tax_rate': 0.0018,
    # 주식 슬리피지: 호가 스프레드 약 0.05~0.15%
    'stock_slippage_rate': 0.0010,         # 왕복 슬리피지 (양방향 합산)
    # ISF 수수료 (편도): 0.00004%
    'isf_commission_rate': 0.000040,
    # ISF 슬리피지: 1호가 단위 = 삼성 5원, SK 50원 수준
    'isf_slippage_ss': 5,    # 삼성전자 편도 슬리피지 (원/주)
    'isf_slippage_sk': 50,   # SK하이닉스 편도 슬리피지 (원/주)
}

CAPITAL_FUTURES = 31_000_000
CAPITAL_STOCK   = 6_880_516
N_DAYS = 22


# ─────────────────────────────────────────────────────────
# KOSPI200 미니선물 비용 계산
# ─────────────────────────────────────────────────────────
def kospi_cost_per_trade(price, qty=1, mult=50_000):
    notional = price * mult * qty
    comm = notional * COSTS['futures_commission_rate'] * 2   # 왕복
    slip = COSTS['futures_slippage_pt'] * mult * qty          # 왕복 슬리피지
    return comm + slip

def run_kospi_with_cost():
    conn = sqlite3.connect(DB_FUTURES)
    df = pd.read_sql(
        "SELECT date,open,high,low,close FROM futures_ohlcv WHERE code='10500000' ORDER BY date", conn)
    conn.close()
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('dt', inplace=True)
    df['date_only'] = df.index.date
    all_days = sorted(set(df['date_only']))
    trade_days = all_days[-N_DAYS:]

    MULT = 50_000
    MARGIN_RATE = 0.10
    K, SL_PT, TP_PT = 0.1, 3.0, 6.0

    cap_gross = CAPITAL_FUTURES   # 비용 제외
    cap_net   = CAPITAL_FUTURES   # 비용 포함
    trades_gross, trades_net = [], []

    for trade_date in trade_days:
        idx = all_days.index(trade_date)
        if idx == 0: continue
        prev_date = all_days[idx-1]
        day_df = df[df['date_only'] == trade_date].sort_index()
        prev_df= df[df['date_only'] == prev_date]
        if day_df.empty or prev_df.empty: continue

        prev_range = float(prev_df['high'].max() - prev_df['low'].min())
        if prev_range <= 0: continue
        morning = day_df[day_df.index.time >= _dt.time(9, 5)]
        if morning.empty: continue
        day_open = float(morning.iloc[0]['open'])
        tl = day_open + prev_range * K
        ts = day_open - prev_range * K
        margin_per = day_open * MULT * MARGIN_RATE
        qty = max(1, int(CAPITAL_FUTURES * 0.3 / margin_per)) if margin_per > 0 else 1

        in_pos = False; entry = sl = tp = dir_ = 0; traded = False

        for bar_ts, bar in day_df.iterrows():
            t = bar_ts.time()
            if t >= _dt.time(15, 15):
                if in_pos:
                    pnl_gross = (bar['open'] - entry) * dir_ * MULT * qty
                    cost = kospi_cost_per_trade(entry, qty, MULT)
                    cap_gross += pnl_gross; trades_gross.append(pnl_gross)
                    cap_net += pnl_gross - cost; trades_net.append(pnl_gross - cost)
                break
            if not in_pos and not traded:
                if t < _dt.time(9, 5): continue
                if bar['high'] >= tl:
                    entry=tl; sl=entry-SL_PT; tp=entry+TP_PT; dir_=1; in_pos=True; traded=True
                elif bar['low'] <= ts:
                    entry=ts; sl=entry+SL_PT; tp=entry-TP_PT; dir_=-1; in_pos=True; traded=True
            elif in_pos:
                if dir_==1:
                    if bar['low']<=sl:
                        pnl=(sl-entry)*MULT*qty; cost=kospi_cost_per_trade(entry,qty,MULT)
                        cap_gross+=pnl; trades_gross.append(pnl)
                        cap_net+=pnl-cost; trades_net.append(pnl-cost); in_pos=False
                    elif bar['high']>=tp:
                        pnl=(tp-entry)*MULT*qty; cost=kospi_cost_per_trade(entry,qty,MULT)
                        cap_gross+=pnl; trades_gross.append(pnl)
                        cap_net+=pnl-cost; trades_net.append(pnl-cost); in_pos=False
                else:
                    if bar['high']>=sl:
                        pnl=(entry-sl)*MULT*qty; cost=kospi_cost_per_trade(entry,qty,MULT)
                        cap_gross+=pnl; trades_gross.append(pnl)
                        cap_net+=pnl-cost; trades_net.append(pnl-cost); in_pos=False
                    elif bar['low']<=tp:
                        pnl=(entry-tp)*MULT*qty; cost=kospi_cost_per_trade(entry,qty,MULT)
                        cap_gross+=pnl; trades_gross.append(pnl)
                        cap_net+=pnl-cost; trades_net.append(pnl-cost); in_pos=False

    n = len(trades_gross)
    wr = sum(1 for p in trades_gross if p>0)/n*100 if n else 0
    avg_cost = sum(kospi_cost_per_trade(1345,1,MULT) for _ in trades_gross)/max(n,1) if n else 0
    return {
        'n': n, 'wr': round(wr,1),
        'gross_pnl': int(sum(trades_gross)),
        'net_pnl':   int(sum(trades_net)),
        'gross_ret': round(sum(trades_gross)/CAPITAL_FUTURES*100,2),
        'net_ret':   round(sum(trades_net)/CAPITAL_FUTURES*100,2),
        'total_cost':int(sum(trades_gross)-sum(trades_net)),
        'avg_cost_per_trade': int(avg_cost),
    }


# ─────────────────────────────────────────────────────────
# ISF 비용 계산
# ─────────────────────────────────────────────────────────
def isf_cost_per_trade(price, contract_size, slip_per_share):
    notional = price * contract_size
    comm = notional * COSTS['isf_commission_rate'] * 2
    slip = slip_per_share * contract_size * 2   # 왕복
    return comm + slip

def run_isf_with_cost(code, K, SL_PCT, TP_PCT, long_thresh, slip_per_share, CONTRACT=10):
    conn = sqlite3.connect(DB_UNIFIED)
    df = pd.read_sql(
        f"SELECT date,open,high,low,close FROM isf_5min_ohlcv WHERE code='{code}' ORDER BY date", conn)
    conn.close()
    if df.empty: return None
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('dt', inplace=True)
    df['date_only'] = df.index.date
    all_days = sorted(set(df['date_only']))
    trade_days = all_days[-N_DAYS:]
    daily_close = df.groupby('date_only')['close'].last()

    cap_gross = CAPITAL_FUTURES
    cap_net   = CAPITAL_FUTURES
    trades_gross, trades_net = [], []

    for trade_date in trade_days:
        idx = all_days.index(trade_date)
        if idx < 6: continue
        past = [daily_close[d] for d in all_days[:idx] if d in daily_close.index][-6:]
        if len(past) < 6: continue
        ret5 = (past[-1]-past[-6])/past[-6]
        if ret5 < long_thresh: continue

        prev_date = all_days[idx-1]
        day_df = df[df['date_only']==trade_date].sort_index()
        prev_df= df[df['date_only']==prev_date]
        if day_df.empty or prev_df.empty: continue
        prev_range = float(prev_df['high'].max()-prev_df['low'].min())
        if prev_range<=0: continue
        morning = day_df[day_df.index.time>=_dt.time(9,5)]
        if morning.empty: continue
        day_open = float(morning.iloc[0]['open'])
        target = day_open + prev_range * K

        in_pos=False; entry=sl_p=tp_p=0.0; traded=False

        for bar_ts, bar in day_df.iterrows():
            t = bar_ts.time()
            if t>=_dt.time(15,15):
                if in_pos:
                    pnl=(bar['open']-entry)*CONTRACT
                    cost=isf_cost_per_trade(entry,CONTRACT,slip_per_share)
                    cap_gross+=pnl; trades_gross.append(pnl)
                    cap_net+=pnl-cost; trades_net.append(pnl-cost); in_pos=False
                break
            if not in_pos and not traded:
                if t<_dt.time(9,5): continue
                if bar['high']>=target:
                    entry=target; sl_p=entry*(1-SL_PCT/100); tp_p=entry*(1+TP_PCT/100)
                    in_pos=True; traded=True
            elif in_pos:
                if bar['low']<=sl_p:
                    pnl=(sl_p-entry)*CONTRACT; cost=isf_cost_per_trade(entry,CONTRACT,slip_per_share)
                    cap_gross+=pnl; trades_gross.append(pnl)
                    cap_net+=pnl-cost; trades_net.append(pnl-cost); in_pos=False
                elif bar['high']>=tp_p:
                    pnl=(tp_p-entry)*CONTRACT; cost=isf_cost_per_trade(entry,CONTRACT,slip_per_share)
                    cap_gross+=pnl; trades_gross.append(pnl)
                    cap_net+=pnl-cost; trades_net.append(pnl-cost); in_pos=False

    n = len(trades_gross)
    wr = sum(1 for p in trades_gross if p>0)/n*100 if n else 0
    return {
        'n':n,'wr':round(wr,1),
        'gross_pnl':int(sum(trades_gross)),
        'net_pnl':int(sum(trades_net)),
        'gross_ret':round(sum(trades_gross)/CAPITAL_FUTURES*100,2),
        'net_ret':round(sum(trades_net)/CAPITAL_FUTURES*100,2),
        'total_cost':int(sum(trades_gross)-sum(trades_net)),
    }


# ─────────────────────────────────────────────────────────
# 손익분기 분석
# ─────────────────────────────────────────────────────────
def breakeven_analysis():
    print('\n' + '='*65)
    print('[ 손익분기 승률 분석 ]')
    print('='*65)

    # KOSPI200: SL=3pt, TP=6pt, 비용=0.218pt
    avg_cost_pt = 0.218   # 왕복 수수료+슬리피지 (pt 환산)
    eff_sl = 3.0 + avg_cost_pt
    eff_tp = 6.0 - avg_cost_pt
    be_wr = eff_sl / (eff_sl + eff_tp) * 100
    print(f'\n  KOSPI200 (SL=3pt, TP=6pt):')
    print(f'  왕복 비용 환산: ~{avg_cost_pt:.2f}pt')
    print(f'  실효 SL={eff_sl:.2f}pt  실효 TP={eff_tp:.2f}pt')
    print(f'  손익분기 최소 승률: {be_wr:.1f}%')
    print(f'  현재 달성 승률: 40.9% → {"흑자" if 40.9>be_wr else "적자"} 구간')

    # 삼성전자 ISF: SL=2%, TP=2.5%, 비용=약 0.15%
    ss_cost_pct = 0.15
    eff_sl_ss = 2.0 + ss_cost_pct
    eff_tp_ss = 2.5 - ss_cost_pct
    be_wr_ss = eff_sl_ss / (eff_sl_ss + eff_tp_ss) * 100
    print(f'\n  삼성전자 ISF (SL=2%, TP=2.5%):')
    print(f'  왕복 비용 환산: ~{ss_cost_pct:.2f}%')
    print(f'  실효 SL={eff_sl_ss:.2f}%  실효 TP={eff_tp_ss:.2f}%')
    print(f'  손익분기 최소 승률: {be_wr_ss:.1f}%')
    print(f'  현재 달성 승률: 80.0% → {"흑자" if 80.0>be_wr_ss else "적자"} 구간')

    # SK하이닉스 ISF: SL=1.2%, TP=2.0%, 비용=약 0.07%
    sk_cost_pct = 0.07
    eff_sl_sk = 1.2 + sk_cost_pct
    eff_tp_sk = 2.0 - sk_cost_pct
    be_wr_sk = eff_sl_sk / (eff_sl_sk + eff_tp_sk) * 100
    print(f'\n  SK하이닉스 ISF (SL=1.2%, TP=2.0%):')
    print(f'  왕복 비용 환산: ~{sk_cost_pct:.2f}%')
    print(f'  실효 SL={eff_sl_sk:.2f}%  실효 TP={eff_tp_sk:.2f}%')
    print(f'  손익분기 최소 승률: {be_wr_sk:.1f}%')
    print(f'  현재 달성 승률: 60.0% → {"흑자" if 60.0>be_wr_sk else "적자"} 구간')


# ─────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=== 수수료 + 슬리피지 반영 수익률 분석 ===\n')

    # 비용 구조 출력
    print('[ 적용 비용 구조 ]')
    print(f'  KOSPI200 미니선물: 수수료 0.0065%×2(왕복) + 슬리피지 0.1pt')
    print(f'  삼성전자 ISF:      수수료 0.004%×2(왕복) + 슬리피지 5원/주×2')
    print(f'  SK하이닉스 ISF:    수수료 0.004%×2(왕복) + 슬리피지 50원/주×2')
    print(f'  주식(단타/스윙):   수수료 0.015%×2 + 증권거래세 0.18% + 슬리피지 0.1%')

    print('\n[ 시뮬레이션 실행 중... ]\n')
    kr = run_kospi_with_cost()
    ssr = run_isf_with_cost('005930', 0.35, 2.0, 2.5, 0.01, slip_per_share=5)
    skr = run_isf_with_cost('000660', 0.18, 1.2, 2.0, 0.03, slip_per_share=50)

    print('='*65)
    print('[ 비용 전/후 수익률 비교 (최근 22거래일) ]')
    print('='*65)
    print(f"\n{'전략':>20} {'비용전':>10} {'비용후':>10} {'비용총액':>12} {'차이':>10}")
    print('-'*65)

    rows = [
        ('KOSPI200 미니선물', kr),
        ('삼성전자 ISF', ssr),
        ('SK하이닉스 ISF', skr),
    ]
    for nm, r in rows:
        if r is None: continue
        diff = r['net_ret'] - r['gross_ret']
        print(f"  {nm:>18} {r['gross_ret']:>+9.2f}% {r['net_ret']:>+9.2f}% "
              f"{r['total_cost']:>+12,}원 {diff:>+9.2f}%")

    # 합산
    total_gross = sum(r['gross_pnl'] for _, r in rows if r)
    total_net   = sum(r['net_pnl'] for _, r in rows if r)
    total_cost  = sum(r['total_cost'] for _, r in rows if r)
    gross_ret   = round(total_gross/CAPITAL_FUTURES*100, 2)
    net_ret     = round(total_net/CAPITAL_FUTURES*100, 2)
    print('-'*65)
    print(f"  {'선물 합산':>18} {gross_ret:>+9.2f}% {net_ret:>+9.2f}% {total_cost:>+12,}원")

    # 주식 비용 추정
    stock_trades = 8
    stock_avg_position = CAPITAL_STOCK * 0.6 / 5
    stock_cost_per_trade = stock_avg_position * (COSTS['stock_commission_rate']*2
                                                  + COSTS['stock_tax_rate']
                                                  + COSTS['stock_slippage_rate'])
    total_stock_cost = int(stock_trades * stock_cost_per_trade)
    stock_gross_pnl = int(CAPITAL_STOCK * 0.0145)
    stock_net_pnl   = stock_gross_pnl - total_stock_cost
    stock_net_ret   = round(stock_net_pnl/CAPITAL_STOCK*100, 2)
    stock_gross_ret_str = "+1.45"
    print(f"  {'주식(단타+스윙)':>18} {stock_gross_ret_str:>10}% {stock_net_ret:>+9.2f}% {total_stock_cost:>+12,}원")

    # 전체 합산
    total_all_gross = total_gross + stock_gross_pnl
    total_all_net   = total_net   + stock_net_pnl
    total_all_cost  = total_cost  + total_stock_cost
    all_assets      = CAPITAL_FUTURES + CAPITAL_STOCK
    all_gross_ret   = round(total_all_gross/all_assets*100, 2)
    all_net_ret     = round(total_all_net/all_assets*100, 2)

    print('\n' + '='*65)
    print('[ 전체 포트폴리오 최종 결과 ]')
    print('='*65)
    print(f'\n  총 운용 자산:  {all_assets:>15,}원')
    print(f'  비용 전 손익:  {total_all_gross:>+15,}원  ({all_gross_ret:+.2f}%)')
    print(f'  총 비용:       {-total_all_cost:>+15,}원')
    print(f'  비용 후 손익:  {total_all_net:>+15,}원  ({all_net_ret:+.2f}%)')
    print(f'\n  시작 자산: {all_assets:,}원')
    print(f'  예상 최종: {all_assets+total_all_net:,}원')

    # 시나리오별 정리
    print('\n' + '='*65)
    print('[ 비용 반영 시나리오별 요약 ]')
    print('='*65)
    scenarios = {
        '이번달 (최근 22일 기준)': all_net_ret,
        '평균 장 (승률 65% 가정)': round((0.65*6-0.35*3)*22*50000*1/CAPITAL_FUTURES*100
                                          + 3.5 + 1.0 - total_all_cost/all_assets*100, 2),
        '불리한 장 (승률 40% 가정)': round((0.40*6-0.60*3)*22*50000*1/CAPITAL_FUTURES*100
                                           + 0.5 + 0.5 - total_all_cost/all_assets*100, 2),
    }
    for label, ret in scenarios.items():
        verdict = '흑자' if ret > 0 else '적자'
        bar = '█'*max(0,int(abs(ret)/0.5)) if ret > 0 else '░'*max(0,int(abs(ret)/0.5))
        print(f'  {label}: {ret:>+6.2f}%  [{bar}] {verdict}')

    breakeven_analysis()

    print('\n\n[ 결론 ]')
    print('='*65)
    print(f'  비용 차감 후 수익률: {all_net_ret:+.2f}%')
    print(f'  총 비용: {total_all_cost:,}원 ({total_all_cost/all_assets*100:.2f}%)')
    print()
    if all_net_ret > 0:
        print(f'  이번 달(최근 22일 기준): 비용 후에도 흑자 유지')
        print(f'  그러나 KOSPI200 승률 40.9%는 역사적 평균(65~87%)보다 낮습니다.')
        print(f'  SK하이닉스 강세가 없었다면 결과가 달라질 수 있습니다.')
    print()
    print(f'  핵심 위험: KOSPI200 승률이 {36.0:.1f}% 이하로 떨어지면 KOSPI200은 적자')
    print(f'  현재 달: 40.9% — 안전 마진 4.9%포인트')
    print(f'  SK하이닉스 추세 반전 시: ISF 전략 중단 필요 (NSAA가 차단)')
