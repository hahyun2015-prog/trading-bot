import sqlite3
import pandas as pd
import numpy as np
import sys
import os

# 종목 매핑
STOCK_NAMES = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "012450": "한화에어로스페이스",
    "274090": "AP위성",
    "027360": "에이텍",
    "036930": "주성엔지니어링"
}

def load_all_intraday_data(conn):
    query = "SELECT DISTINCT code FROM backtest_ohlcv"
    cursor = conn.cursor()
    cursor.execute(query)
    codes = [row[0] for row in cursor.fetchall()]
    return codes

def load_intraday_data(code, conn):
    query = f"SELECT date, open, high, low, close, volume FROM backtest_ohlcv WHERE code = '{code}' ORDER BY date ASC"
    df = pd.read_sql_query(query, conn)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
    return df

def calculate_vwap(df):
    df['date_only'] = df.index.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3
    def _group_vwap(x):
        cum_vol = x['volume'].cumsum()
        cum_vol = cum_vol.where(cum_vol > 0, 1)
        return (x['typ_price'] * x['volume']).cumsum() / cum_vol
    df['vwap'] = df.groupby('date_only').apply(_group_vwap).reset_index(level=0, drop=True)
    return df

def apply_rsi(df, window=14):
    # TA 라이브러리 없이 단순 RSI 계산으로 속도 및 무의존성 확보
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss.replace(0, 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    return df

def run_standard_backtest_vectorized(conn, codes):
    FEE_AND_TAX = 0.0025
    results = {}
    
    total_trades = 0
    winning_trades = 0
    total_profit_pct = 0.0
    
    for code in codes:
        df = load_intraday_data(code, conn)
        if len(df) < 50:
            continue
            
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        
        # 벡터 지표 미리 산출
        df['vol_mean_5'] = df['volume'].rolling(window=5).mean()
        df['vol_mean_5_shift_5'] = df['vol_mean_5'].shift(5)
        df['low_min_5'] = df['low'].rolling(window=5).min()
        df['low_min_5_shift_5'] = df['low_min_5'].shift(5)
        df['rsi_min_5'] = df['rsi'].rolling(window=5).min()
        df['rsi_min_5_shift_5'] = df['rsi_min_5'].shift(5)
        
        # 신호 조건 벡터화 판별
        df['is_pullback'] = (abs(df['close'] - df['vwap']) / df['vwap'] <= 0.015) & (df['vol_mean_5'] < df['vol_mean_5_shift_5'])
        df['is_divergence'] = (df['low_min_5'] >= df['low_min_5_shift_5']) & (df['rsi_min_5'] < df['rsi_min_5_shift_5'])
        df['is_uptrend'] = df['close'] >= (df['ma_10'] * 0.99)
        df['entry_signal'] = df['is_pullback'] & df['is_divergence'] & df['is_uptrend']
        
        in_position = False
        super_trend_mode = False
        buy_price = 0
        
        stock_trades = 0
        stock_wins = 0
        stock_profit = 0.0
        equity_curve = [1.0]
        
        for i in range(30, len(df)):
            current_row = df.iloc[i]
            
            if not in_position:
                if current_row['entry_signal']:
                    in_position = True
                    super_trend_mode = False
                    buy_price = current_row['close']
                    
            else:
                profit_ratio = (current_row['close'] - buy_price) / buy_price
                sell_reason = None
                
                if profit_ratio <= -0.02:
                    sell_reason = "손절 (-2%)"
                else:
                    if super_trend_mode:
                        if current_row['close'] < current_row['ma_10']:
                            sell_reason = "10MA 하향 돌파 익절"
                        elif profit_ratio <= 0.015:
                            sell_reason = "최소수익보장(+1.5%) 익절"
                    else:
                        if profit_ratio >= 0.03:
                            ma_10_is_up = current_row['ma_10'] > df.iloc[i-1]['ma_10']
                            if current_row['ma_10'] > 0 and ma_10_is_up and current_row['close'] >= current_row['ma_10']:
                                super_trend_mode = True
                                continue
                            else:
                                sell_reason = "목표가(+3%) 도달 (추세꺾임 익절)"
                                
                if sell_reason:
                    net_profit_ratio = profit_ratio - FEE_AND_TAX
                    profit_pct = net_profit_ratio * 100
                    
                    stock_trades += 1
                    stock_profit += profit_pct
                    equity_curve.append(equity_curve[-1] * (1 + net_profit_ratio))
                    
                    if net_profit_ratio > 0:
                        stock_wins += 1
                        
                    in_position = False
                    super_trend_mode = False
                    
        # MDD 계산
        mdd = 0.0
        if len(equity_curve) > 1:
            eq_series = pd.Series(equity_curve)
            cum_max = eq_series.cummax()
            dd = (eq_series - cum_max) / cum_max
            mdd = dd.min() * 100
            
        results[code] = {
            "name": STOCK_NAMES.get(code, code),
            "trades": stock_trades,
            "win_rate": (stock_wins / stock_trades * 100) if stock_trades > 0 else 0.0,
            "profit": stock_profit,
            "mdd": mdd
        }
        
        total_trades += stock_trades
        winning_trades += stock_wins
        total_profit_pct += stock_profit
        
    return results, {
        "trades": total_trades,
        "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0.0,
        "profit": total_profit_pct
    }

def run_optimized_backtest_vectorized(conn, codes):
    FEE_AND_TAX = 0.0025
    results = {}
    
    total_trades = 0
    winning_trades = 0
    total_profit_pct = 0.0
    
    for code in codes:
        df = load_intraday_data(code, conn)
        if len(df) < 120:
            continue
            
        df = calculate_vwap(df)
        df = apply_rsi(df)
        df['ma_10'] = df['close'].rolling(window=10).mean()
        df['ma_120'] = df['close'].rolling(window=120).mean()
        
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift())
        df['tr2'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        df['atr_14'] = df['tr'].rolling(window=14).mean()
        
        # 벡터 지표 미리 산출
        df['vol_mean_5'] = df['volume'].rolling(window=5).mean()
        df['vol_mean_5_shift_5'] = df['vol_mean_5'].shift(5)
        df['low_min_5'] = df['low'].rolling(window=5).min()
        df['low_min_5_shift_5'] = df['low_min_5'].shift(5)
        df['rsi_min_5'] = df['rsi'].rolling(window=5).min()
        df['rsi_min_5_shift_5'] = df['rsi_min_5'].shift(5)
        
        # 신호 조건 벡터화 판별
        df['is_pullback'] = (abs(df['close'] - df['vwap']) / df['vwap'] <= 0.015) & (df['vol_mean_5'] < df['vol_mean_5_shift_5'])
        df['is_divergence'] = (df['low_min_5'] >= df['low_min_5_shift_5']) & (df['rsi_min_5'] < df['rsi_min_5_shift_5'])
        df['is_uptrend'] = df['close'] >= (df['ma_10'] * 0.99)
        df['is_bull_regime'] = df['close'] >= df['ma_120']
        
        df['entry_signal'] = df['is_pullback'] & df['is_divergence'] & df['is_uptrend'] & df['is_bull_regime']
        
        in_pos = False
        buy_price = 0
        super_trend_mode = False
        half_sold = False
        peak_price = 0
        position_size = 1.0
        
        stock_trades = 0
        stock_wins = 0
        stock_profit = 0.0
        equity_curve = [1.0]
        
        for i in range(120, len(df)):
            current_row = df.iloc[i]
            
            hour = current_row.name.hour
            minute = current_row.name.minute
            time_val = hour * 100 + minute
            is_valid_time = (900 <= time_val <= 1100) or (1430 <= time_val <= 1520)
            
            if not in_pos:
                if current_row['entry_signal'] and is_valid_time:
                    in_pos = True
                    buy_price = current_row['close']
                    peak_price = buy_price
                    super_trend_mode = False
                    half_sold = False
                    
                    volatility_pct = current_row['atr_14'] / current_row['close']
                    if volatility_pct > 0.02:
                        position_size = 0.5
                    else:
                        position_size = 1.0
                            
            else:
                profit_ratio = (current_row['close'] - buy_price) / buy_price
                
                if current_row['close'] > peak_price:
                    peak_price = current_row['close']
                    
                drawdown_from_peak = (peak_price - current_row['close']) / peak_price
                exit_signal_full = False
                
                if not half_sold and profit_ratio >= 0.03:
                    half_net_profit = profit_ratio - FEE_AND_TAX
                    stock_profit += (half_net_profit * 100 * position_size * 0.5)
                    half_sold = True
                    super_trend_mode = True
                
                if drawdown_from_peak >= 0.015:
                    exit_signal_full = True
                elif profit_ratio <= -0.02:
                    exit_signal_full = True
                else:
                    if super_trend_mode:
                        if current_row['close'] < current_row['ma_10']:
                            exit_signal_full = True
                            
                if exit_signal_full:
                    net_profit = profit_ratio - FEE_AND_TAX
                    trade_profit = 0.0
                    
                    if half_sold:
                        trade_profit = (net_profit * 100 * position_size * 0.5)
                        stock_profit += trade_profit
                        equity_curve.append(equity_curve[-1] * (1 + net_profit * position_size * 0.5))
                        if net_profit > 0:
                            stock_wins += 1
                    else:
                        trade_profit = (net_profit * 100 * position_size)
                        stock_profit += trade_profit
                        equity_curve.append(equity_curve[-1] * (1 + net_profit * position_size))
                        if net_profit > 0:
                            stock_wins += 1
                            
                    stock_trades += 1
                    in_pos = False
                    
        # MDD 계산
        mdd = 0.0
        if len(equity_curve) > 1:
            eq_series = pd.Series(equity_curve)
            cum_max = eq_series.cummax()
            dd = (eq_series - cum_max) / cum_max
            mdd = dd.min() * 100
            
        results[code] = {
            "name": STOCK_NAMES.get(code, code),
            "trades": stock_trades,
            "win_rate": (stock_wins / stock_trades * 100) if stock_trades > 0 else 0.0,
            "profit": stock_profit,
            "mdd": mdd
        }
        
        total_trades += stock_trades
        winning_trades += stock_wins
        total_profit_pct += stock_profit
        
    return results, {
        "trades": total_trades,
        "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0.0,
        "profit": total_profit_pct
    }

def main():
    conn = sqlite3.connect("kiwoom_data.db")
    codes = load_all_intraday_data(conn)
    
    if not codes:
        print("데이터가 없습니다.")
        conn.close()
        return
        
    std_res, std_summary = run_standard_backtest_vectorized(conn, codes)
    opt_res, opt_summary = run_optimized_backtest_vectorized(conn, codes)
    
    conn.close()
    
    print("\n# [주식 단타 Combo 1] 2.6개월 백테스트 비교 분석 리포트")
    print(f"**백테스트 검증 기간:** 2026-03-10 ~ 2026-05-29 (약 2.6개월, 55거래일)")
    print(f"**데이터 소스:** Kiwoom OpenAPI 3분봉 데이터 (총 41,400개 봉)")
    print("\n## 1. 종합 요약 비교")
    print("| 전략 구분 | 총 매매 횟수 | 평균 승률 | 누적 수익률 |")
    print("| --- | --- | --- | --- |")
    print(f"| **기본 전략 (Combo 1)** | {std_summary['trades']}회 | {std_summary['win_rate']:.1f}% | **{std_summary['profit']:+.2f}%** |")
    print(f"| **최적화 전략 (5대 솔루션)** | {opt_summary['trades']}회 | {opt_summary['win_rate']:.1f}% | **{opt_summary['profit']:+.2f}%** |")
    
    print("\n## 2. 종목별 상세 실적 비교")
    print("| 종목명 (코드) | 기본 매매수 | 기본 승률 | 기본 수익률 | 기본 MDD | 최적화 매매수 | 최적화 승률 | 최적화 수익률 | 최적화 MDD |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    
    for code in codes:
        s = std_res.get(code, {"name": STOCK_NAMES.get(code, code), "trades": 0, "win_rate": 0, "profit": 0, "mdd": 0})
        o = opt_res.get(code, {"name": STOCK_NAMES.get(code, code), "trades": 0, "win_rate": 0, "profit": 0, "mdd": 0})
        print(f"| {s['name']} ({code}) | {s['trades']}회 | {s['win_rate']:.1f}% | {s['profit']:+.2f}% | {s['mdd']:.1f}% | {o['trades']}회 | {o['win_rate']:.1f}% | {o['profit']:+.2f}% | {o['mdd']:.1f}% |")

if __name__ == "__main__":
    main()
