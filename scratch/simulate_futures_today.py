import sqlite3
import pandas as pd

def check_futures_ohlcv(code):
    print(f"\n==================== {code} OHLCV ====================")
    conn = sqlite3.connect('futures_data.db')
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = ? ORDER BY date ASC",
        conn,
        params=(code,)
    )
    conn.close()
    
    if df.empty:
        print("데이터 없음")
        return
        
    df['date_dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    df['date_str'] = df['date'].str[:8]
    
    # 6월 4일과 6월 5일 데이터 필터링
    df_filtered = df[df['date_str'].isin(['20260604', '20260605'])]
    
    # 일별 데이터 요약
    daily = df.groupby('date_str').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    daily['range'] = daily['high'] - daily['low']
    daily['prev_range'] = daily['range'].shift(1)
    
    print("\n--- 일별 집계 ---")
    print(daily.tail(5))
    
    print("\n--- 6월 5일 5분봉 상세 내역 (최초 10개, 최종 5개) ---")
    df_today = df_filtered[df_filtered['date_str'] == '20260605']
    if not df_today.empty:
        print("최초 10개 봉:")
        print(df_today.head(10)[['date', 'open', 'high', 'low', 'close', 'volume']])
        print("\n최종 5개 봉:")
        print(df_today.tail(5)[['date', 'open', 'high', 'low', 'close', 'volume']])
    else:
        print("6월 5일 봉 데이터 없음")

check_futures_ohlcv('10100000')
check_futures_ohlcv('10500000')
