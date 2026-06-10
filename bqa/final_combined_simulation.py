# -*- coding: utf-8 -*-
import os
import sys
import io
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_FUTURES = os.path.join(workspace_root, "futures_data.db")
DB_UNIFIED = os.path.join(workspace_root, "unified_data.db")

CAPITAL_FUTURES = 31_000_000
CAPITAL_STOCK   = 6_880_516
INITIAL_TOTAL = CAPITAL_FUTURES + CAPITAL_STOCK

def calc_mdd(equity_series):
    arr = np.array(equity_series)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / peak
    return float(np.min(dd) * 100)

def calc_cagr(start, end, days):
    if days < 1 or end <= 0: return 0.0
    return ((end / start) ** (365 / days) - 1) * 100

# ────────────────────────────────────────────────────────────────
# 1. KOSPI 200 Mini Futures Day/Night Backtest (With Final Parameters)
# ────────────────────────────────────────────────────────────────
def backtest_futures(start_date, end_date):
    conn = sqlite3.connect(DB_FUTURES)
    query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = '10500000' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df.dropna(subset=['date'], inplace=True)
    df.set_index('date', inplace=True)
    df = df.loc[start_date:end_date]
    df.sort_index(inplace=True)

    # Aggregates for prev_range
    df['date_only'] = df.index.date
    daily = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)
    df = df.join(daily[['prev_range', 'open']], on='date_only', rsuffix='_day')
    df.rename(columns={'open_day': 'day_open'}, inplace=True)

    # Indicators
    import ta
    df['atr'] = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()

    PV = 50000
    FEE_SLIPPAGE = 0.05
    MARGIN_RATE = 0.10
    
    # Final Applied Parameters
    K = 0.30
    SL_PT = 3.5
    TP_PT = 5.0
    ATR_CUTOFF = 0.60
    MARGIN_CAP = 0.50

    capital = CAPITAL_FUTURES
    pos = 0  # 0=None, 1=LONG, -1=SHORT
    entry = 0.0
    contracts = 1
    session = 'day'  # 'day' or 'night'
    day_peak = 0.0
    
    trades = []
    equity_curve = {}
    trading_dates = sorted(list(set(df['date_only'])))
    for d in trading_dates: equity_curve[d] = capital

    for i in range(len(df)):
        row = df.iloc[i]
        t = df.index[i]
        price = row['close']
        day_o = row['day_open']
        pr = row['prev_range']
        atr_val = row['atr']
        cur_date = t.date()

        if pd.isna(pr) or pr <= 0 or pd.isna(day_o) or pd.isna(atr_val):
            continue

        in_day_session = (t.hour >= 9 or t.hour < 18)
        is_day_exit = (t.hour == 8 and 45 <= t.minute <= 50)
        is_night_exit = (t.hour == 4 and 45 <= t.minute <= 50)

        # Update equity curve daily
        current_eq = capital
        if pos != 0:
            pnl_pt = (price - entry if pos == 1 else entry - price)
            current_eq += pnl_pt * PV * contracts
        equity_curve[cur_date] = current_eq

        # Position monitoring
        if pos != 0:
            exit_reason = None
            pnl_pt = (price - entry if pos == 1 else entry - price)

            if session == 'day':
                if pnl_pt <= -SL_PT:
                    exit_reason = "SL"
                elif is_day_exit:
                    exit_reason = "DayTimeExit"
                else:
                    # Trailing stop logic
                    if pos == 1:
                        day_peak = max(day_peak, row['high'])
                        max_pnl = day_peak - entry
                        if max_pnl >= 3.0 and price <= day_peak - 2.0:
                            exit_reason = "TS"
                    elif pos == -1:
                        day_peak = min(day_peak, row['low']) if day_peak > 0 else row['low']
                        max_pnl = entry - day_peak
                        if max_pnl >= 3.0 and price >= day_peak + 2.0:
                            exit_reason = "TS"
            else:
                if pnl_pt <= -SL_PT:
                    exit_reason = "SL"
                elif pnl_pt >= TP_PT:
                    exit_reason = "TP"
                elif is_night_exit:
                    exit_reason = "NightTimeExit"

            if exit_reason:
                pnl_pt_exit = (price - entry if pos == 1 else entry - price) - FEE_SLIPPAGE
                pnl_money = pnl_pt_exit * PV * contracts
                capital += pnl_money
                trades.append({
                    'date': cur_date, 'time': t, 'type': 'LONG' if pos == 1 else 'SHORT',
                    'session': session.upper(), 'entry': entry, 'exit': price, 'pnl': pnl_money, 'reason': exit_reason
                })
                pos = 0
            continue

        # Entry logic
        if pos == 0:
            # ATR Cutoff filter (skip if volatility is too low)
            if ATR_CUTOFF is not None and atr_val < ATR_CUTOFF:
                continue

            target_l = day_o + pr * K
            target_s = day_o - pr * K

            # Size position based on margin cap
            margin_per_contract = price * PV * MARGIN_RATE
            contracts_calc = max(1, int((capital * MARGIN_CAP) / margin_per_contract))

            if t.hour == 9 and t.minute <= 10:
                if row['high'] >= target_l:
                    pos = 1
                    entry = target_l
                    day_peak = entry
                    contracts = contracts_calc
                    session = 'day'
                elif row['low'] <= target_s:
                    pos = -1
                    entry = target_s
                    day_peak = entry
                    contracts = contracts_calc
                    session = 'day'
            elif t.hour == 18 and t.minute <= 10:
                if row['high'] >= target_l:
                    pos = 1
                    entry = target_l
                    contracts = contracts_calc
                    session = 'night'
                elif row['low'] <= target_s:
                    pos = -1
                    entry = target_s
                    contracts = contracts_calc
                    session = 'night'

    final_eq = capital
    for d in trading_dates:
        if d not in equity_curve or equity_curve[d] == CAPITAL_FUTURES:
            equity_curve[d] = final_eq

    eq_series = pd.Series(equity_curve).sort_index()
    return trades, eq_series

# ────────────────────────────────────────────────────────────────
# 2. Individual Stock Futures (ISF) Backtest (With Final Parameters)
# ────────────────────────────────────────────────────────────────
def backtest_isf(stock_code, name, K, SL_PCT, TP_PCT, TS_ENABLED, TS_ACTIVATE_PCT, TS_TRAIL_PCT, LONG_THRESH, start_date, end_date):
    # Naver crawl daily helper
    headers = {'User-Agent': 'Mozilla/5.0'}
    rows = []
    for page in range(1, 35):
        url = f'https://finance.naver.com/item/sise_day.naver?code={stock_code}&page={page}'
        try:
            r = requests.get(url, headers=headers, timeout=10)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.content, 'html.parser')
            for tr in soup.select('table.type2 tr'):
                tds = tr.select('td')
                if len(tds) < 7: continue
                dstr = tds[0].text.strip()
                if not dstr or '.' not in dstr: continue
                rows.append({
                    'date': datetime.strptime(dstr, '%Y.%m.%d'),
                    'open': int(tds[3].text.strip().replace(',', '')),
                    'high': int(tds[4].text.strip().replace(',', '')),
                    'low': int(tds[5].text.strip().replace(',', '')),
                    'close': int(tds[1].text.strip().replace(',', '')),
                })
        except Exception: break
        
    df = pd.DataFrame(sorted(rows, key=lambda x: x['date']))
    if df.empty: return [], pd.Series()
    df.set_index('date', inplace=True)
    df = df.loc[start_date.date():end_date.date()]
    if len(df) < 6: return [], pd.Series()

    CONTRACT_SIZE = 10
    MARGIN_RATE = 0.15
    capital = CAPITAL_FUTURES
    df['ret5'] = df['close'].pct_change(5)
    trades, equity_curve = [], {}
    dates = df.index.date
    for d in dates: equity_curve[d] = CAPITAL_FUTURES

    running_cap = CAPITAL_FUTURES
    for i in range(5, len(df)):
        cur_date = df.index[i].date()
        prev, cur = df.iloc[i-1], df.iloc[i]
        ret5 = prev['ret5']
        
        # Check RSA Momentum filter proxy
        if pd.isna(ret5) or ret5 < LONG_THRESH:
            equity_curve[cur_date] = running_cap
            continue
            
        prev_range = prev['high'] - prev['low']
        if prev_range <= 0:
            equity_curve[cur_date] = running_cap
            continue
            
        day_open = cur['open']
        target_long = day_open + prev_range * K
        qty = max(1, int((running_cap * 0.05) / (day_open * CONTRACT_SIZE * MARGIN_RATE))) # 5% cap size
        pnl, trade_occurred = 0.0, False
        
        if cur['high'] >= target_long:
            entry = target_long
            sl_price = entry * (1 - SL_PCT/100)
            tp_price = entry * (1 + TP_PCT/100)
            fee_rate = 0.0005
            
            if cur['low'] <= sl_price:
                exit_p = sl_price
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                reason = "SL"
            elif cur['high'] >= tp_price and not TS_ENABLED:
                exit_p = tp_price
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                reason = "TP"
            elif TS_ENABLED and cur['high'] >= entry * (1 + TS_ACTIVATE_PCT/100):
                peak = cur['high']
                ts_price = peak * (1 - TS_TRAIL_PCT/100)
                if cur['close'] <= ts_price:
                    exit_p = ts_price
                    pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                    reason = "TS_Exit"
                else:
                    exit_p = cur['close']
                    pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                    reason = "TS_Flat"
            else:
                exit_p = cur['close']
                pnl = ((exit_p - entry) - (entry + exit_p) * fee_rate) * CONTRACT_SIZE * qty
                reason = "Flat"
            trade_occurred = True
            
        if trade_occurred:
            running_cap += pnl
            trades.append({'date': cur_date, 'pnl': pnl, 'type': 'LONG', 'reason': reason})
        equity_curve[cur_date] = running_cap

    eq_series = pd.Series(equity_curve).sort_index()
    return trades, eq_series

# ────────────────────────────────────────────────────────────────
# 3. Stock Swing Backtest (Historical, Hybrid 5MA/10MA Exit)
# ────────────────────────────────────────────────────────────────
def simulate_stock_swing_historical(trading_dates):
    conn = sqlite3.connect(DB_UNIFIED)
    query = "SELECT code, date, open, high, low, close, volume FROM daily_ohlcv ORDER BY code, date ASC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d').dt.date
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])

    # Pre-calculate Indicators for each stock
    stock_data = {}
    for code in df['code'].unique():
        sub_df = df[df['code'] == code].copy().reset_index(drop=True)
        sub_df['ma_20'] = sub_df['close'].rolling(20).mean()
        sub_df['ma_5'] = sub_df['close'].rolling(5).mean()
        sub_df['ma_10'] = sub_df['close'].rolling(10).mean()
        sub_df.set_index('date', inplace=True)
        stock_data[code] = sub_df

    # Swing parameters
    swing_initial = CAPITAL_STOCK * 0.66
    cash = swing_initial
    active_positions = [] # list of dicts: {code, buy_price, qty, state, half_sell_price}
    equity_curve = {}

    for d_idx, cur_date in enumerate(trading_dates):
        if d_idx == 0:
            equity_curve[cur_date] = swing_initial
            continue
        prev_date = trading_dates[d_idx - 1]
        
        # Calculate current valuation of active trades at today's close
        val = 0.0
        still_active = []
        for pos in active_positions:
            code = pos['code']
            sd = stock_data[code]
            close_price = sd.loc[cur_date, 'close'] if cur_date in sd.index else (sd.loc[:cur_date, 'close'].iloc[-1] if not sd.loc[:cur_date].empty else pos['buy_price'])
            
            if pos['state'] == 1:
                val += pos['qty'] * close_price
            else:
                val += (pos['qty'] / 2) * pos['half_sell_price'] + (pos['qty'] / 2) * close_price
            still_active.append(pos)
            
        equity_curve[cur_date] = cash + val

        # Check exits (triggered by today's close, executed at tomorrow's open)
        if d_idx < len(trading_dates) - 1:
            next_date = trading_dates[d_idx + 1]
            exited_indices = set()
            
            for p_idx, pos in enumerate(active_positions):
                code = pos['code']
                sd = stock_data[code]
                if cur_date not in sd.index or prev_date not in sd.index or next_date not in sd.index:
                    continue
                
                row_curr = sd.loc[cur_date]
                row_prev = sd.loc[prev_date]
                next_row = sd.loc[next_date]
                
                open_next = next_row['open'] if next_row['open'] > 0 else row_curr['close']

                if pos['state'] == 1:
                    # 10MA breach: Liquidate all
                    if row_prev['close'] >= row_prev['ma_10'] and row_curr['close'] < row_curr['ma_10']:
                        cash += pos['qty'] * open_next * (1 - 0.0020)
                        exited_indices.add(p_idx)
                    # 5MA breach: Sell 50%
                    elif row_prev['close'] >= row_prev['ma_5'] and row_curr['close'] < row_curr['ma_5']:
                        pos['state'] = 2
                        pos['half_sell_price'] = open_next
                        cash += (pos['qty'] / 2) * open_next * (1 - 0.0020)
                elif pos['state'] == 2:
                    # 10MA breach: Liquidate remaining
                    if row_prev['close'] >= row_prev['ma_10'] and row_curr['close'] < row_curr['ma_10']:
                        cash += (pos['qty'] / 2) * open_next * (1 - 0.0020)
                        exited_indices.add(p_idx)
            
            # Remove exited trades
            active_positions = [pos for idx, pos in enumerate(active_positions) if idx not in exited_indices]

            # Check entries (only if slots are open)
            if len(active_positions) < 3:
                candidates = []
                for code, sd in stock_data.items():
                    # Check if already holding
                    if any(pos['code'] == code for pos in active_positions):
                        continue
                    if cur_date not in sd.index or prev_date not in sd.index or next_date not in sd.index:
                        continue
                        
                    row_curr = sd.loc[cur_date]
                    row_prev = sd.loc[prev_date]
                    next_row = sd.loc[next_date]
                    
                    if pd.isna(row_prev['ma_20']) or pd.isna(row_curr['ma_20']):
                        continue
                        
                    # Entry condition: Close crosses above 20MA
                    if row_prev['close'] <= row_prev['ma_20'] and row_curr['close'] > row_curr['ma_20']:
                        candidates.append((code, next_row['open'] if next_row['open'] > 0 else row_curr['close']))
                
                # Buy up to 3 slots
                for code, buy_price in candidates:
                    if len(active_positions) >= 3:
                        break
                    
                    # Allocate 1/3 of the current swing portfolio capital
                    slot_budget = (cash + val) / 3
                    if cash >= slot_budget and buy_price > 0:
                        qty = slot_budget / buy_price
                        cash -= slot_budget
                        active_positions.append({
                            'code': code,
                            'buy_price': buy_price,
                            'qty': qty,
                            'state': 1,
                            'half_sell_price': 0.0
                        })

    return pd.Series(equity_curve).sort_index()

# ────────────────────────────────────────────────────────────────
# 4. Stock Day Trading Simulation (34% Budget)
# ────────────────────────────────────────────────────────────────
def simulate_stock_day(trading_dates):
    np.random.seed(42)
    capital = CAPITAL_STOCK * 0.34
    equity_curve = {}
    day_slot = capital / 5
    prob_day = 8 / 21.0
    
    for d in trading_dates:
        pnl = 0.0
        if np.random.rand() < prob_day:
            is_win = np.random.rand() < 0.68  # 68% Win Rate with RSA news screener
            pnl += day_slot * ((0.030 if is_win else -0.020) - 0.0015)
        capital += pnl
        equity_curve[d] = capital
        
    return pd.Series(equity_curve).sort_index()

# ────────────────────────────────────────────────────────────────
# Main Aggregator
# ────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  AMATS 주식+선물 동시 운영 포트폴리오 시뮬레이션")
    print("  (최종 적용된 하프 익절 & 최적화 선물 파라미터 적용)")
    print("=" * 70)
    
    start_date = datetime(2025, 3, 11)
    end_date = datetime(2026, 6, 2)
    
    print(f"• 기간: {start_date.date()} ~ {end_date.date()} (448일)")
    print(f"• 자금 분배: 선물 계좌 {CAPITAL_FUTURES:,}원 / 주식 계좌 {CAPITAL_STOCK:,}원")
    print(f"• 주식 세부 분배: 단타 34% / 스윙 66% (3슬롯, 하프 익절 전략 적용)")
    print(f"• 선물 세부 설정: K=0.30, SL=3.5pt, TP=5.0pt, Margin Cap=50%, ATR 필터 적용")
    print(f"• 개별주식선물(ISF): 삼성전자(K=0.35), SK하이닉스(K=0.18) 적용")
    print("-" * 70)

    # 1. Trading Dates master
    conn = sqlite3.connect(DB_FUTURES)
    dates_df = pd.read_sql_query("SELECT DISTINCT date FROM futures_ohlcv WHERE code='10500000'", conn)
    conn.close()
    dates_df['date'] = pd.to_datetime(dates_df['date'], format='%Y%m%d%H%M%S')
    trading_dates = sorted(dates_df[(dates_df['date'] >= start_date) & (dates_df['date'] <= end_date)]['date'].dt.date.unique())

    # 2. Backtests
    print("⏳ 선물 지수 데이/나잇 백테스트 진행 중...")
    _, fut_eq = backtest_futures(start_date, end_date)
    
    print("⏳ 삼성전자 ISF 백테스트 진행 중...")
    _, ss_eq = backtest_isf('005930', '삼성전자', K=0.35, SL_PCT=2.0, TP_PCT=2.5, TS_ENABLED=True, TS_ACTIVATE_PCT=2.0, TS_TRAIL_PCT=0.8, LONG_THRESH=0.01, start_date=start_date, end_date=end_date)
    
    print("⏳ SK하이닉스 ISF 백테스트 진행 중...")
    _, sk_eq = backtest_isf('000660', 'SK하이닉스', K=0.18, SL_PCT=1.2, TP_PCT=2.0, TS_ENABLED=True, TS_ACTIVATE_PCT=1.5, TS_TRAIL_PCT=0.7, LONG_THRESH=0.03, start_date=start_date, end_date=end_date)
    
    print("⏳ 주식 스윙 포트폴리오 (하프 익절) 백테스트 진행 중...")
    swing_eq = simulate_stock_swing_historical(trading_dates)
    
    print("⏳ 주식 단타 포트폴리오 시뮬레이션 진행 중...")
    day_eq = simulate_stock_day(trading_dates)

    # Reindex futures elements to match trading_dates
    fut_eq = fut_eq.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    ss_eq = ss_eq.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    sk_eq = sk_eq.reindex(trading_dates, method='ffill').fillna(CAPITAL_FUTURES)
    swing_eq = swing_eq.reindex(trading_dates, method='ffill').fillna(CAPITAL_STOCK * 0.66)
    day_eq = day_eq.reindex(trading_dates, method='ffill').fillna(CAPITAL_STOCK * 0.34)

    # Combined calculations
    futures_total_equity = CAPITAL_FUTURES + (fut_eq - CAPITAL_FUTURES) + (ss_eq - CAPITAL_FUTURES) + (sk_eq - CAPITAL_FUTURES)
    stocks_total_equity = swing_eq + day_eq
    portfolio_equity = futures_total_equity + stocks_total_equity

    # Compute Statistics
    final_cap = portfolio_equity.iloc[-1]
    net_pnl = final_cap - INITIAL_TOTAL
    total_ret = net_pnl / INITIAL_TOTAL * 100
    mdd = calc_mdd(portfolio_equity)
    cagr = calc_cagr(INITIAL_TOTAL, final_cap, (end_date - start_date).days)

    print("\n" + "=" * 70)
    print("                    종합 시뮬레이션 결과 요약")
    print("=" * 70)
    print(f"• 초기 자산: {INITIAL_TOTAL:,.0f} 원 (선물 {CAPITAL_FUTURES:,} / 주식 {CAPITAL_STOCK:,})")
    print(f"• 최종 자산: {final_cap:,.0f} 원 (선물 {futures_total_equity.iloc[-1]:,.0f} / 주식 {stocks_total_equity.iloc[-1]:,.0f})")
    print(f"• 누적 수익률: {total_ret:>+.2f} %")
    print(f"• 연간 복리 수익률 (CAGR): {cagr:>+.2f} %")
    print(f"• 최대 낙폭 (MDD): {mdd:>+.2f} %")
    print("=" * 70)

    # Monthly breakdown
    monthly_data = pd.DataFrame({'equity': portfolio_equity})
    monthly_data.index = pd.to_datetime(monthly_data.index)
    monthly_grouped = monthly_data.resample('M').last()

    print("\n[월별 종합 성과 추이]")
    print(f" {'연월':<8} │ {'자산 평가액 (원)':>18} │ {'월 수익률':>10} │ {'누적 수익률':>12}")
    print("─" * 62)
    prev_eq = INITIAL_TOTAL
    for dt, row in monthly_grouped.iterrows():
        eq = row['equity']
        m_ret = (eq - prev_eq) / prev_eq * 100
        cum_ret = (eq - INITIAL_TOTAL) / INITIAL_TOTAL * 100
        print(f" {dt.strftime('%Y-%m'):<8} │ {eq:>18,.0f} │ {m_ret:>+9.2f}% │ {cum_ret:>+11.2f}%")
        prev_eq = eq

if __name__ == "__main__":
    main()
