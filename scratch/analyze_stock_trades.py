import sqlite3
import pandas as pd
import numpy as np

db_path = r"c:\Antigravity\AI_T_Agent\unified_data.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. 테이블 목록 조회
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in cursor.fetchall()]
print("=== unified_data.db 테이블 목록 ===")
print(tables)
print("-" * 50)

# 2. stock_trades 테이블이 존재하는지 확인
if "stock_trades" in tables:
    df = pd.read_sql("SELECT * FROM stock_trades", conn)
    print(f"Total trades in stock_trades: {len(df)}")
    print(df.head(10))
    print("-" * 50)
    
    # 성과 분석 (DAY vs SWING)
    for strat in df['strategy_type'].unique():
        df_strat = df[df['strategy_type'] == strat].copy()
        total_trades = len(df_strat)
        if total_trades == 0:
            continue
            
        winning_trades = len(df_strat[df_strat['pnl'] > 0])
        win_rate = (winning_trades / total_trades) * 100
        total_pnl = df_strat['pnl'].sum()
        avg_pnl = df_strat['pnl'].mean()
        max_pnl = df_strat['pnl'].max()
        min_pnl = df_strat['pnl'].min()
        
        # MDD 계산
        cap = 10000000 # 1000만원 시작 기준 가정
        balance_history = [cap]
        for p in df_strat['pnl']:
            balance_history.append(balance_history[-1] + p)
        arr = np.array(balance_history)
        peak = np.maximum.accumulate(arr)
        mdd = np.min((arr - peak) / peak) * 100
        
        print(f"★ 전략: {strat} ★")
        print(f"  - 거래 건수: {total_trades}건")
        print(f"  - 승률: {win_rate:.2f}% ({winning_trades}/{total_trades})")
        print(f"  - 누적 손익: {total_pnl:+,}원")
        print(f"  - 평균 손익: {avg_pnl:+,.2f}원")
        print(f"  - 최대 이익: {max_pnl:+,}원 / 최대 손실: {min_pnl:+,}원")
        print(f"  - 전략 MDD: {mdd:.2f}%")
        print("-" * 50)
else:
    print("stock_trades 테이블이 존재하지 않습니다.")
    
    # 다른 매매 관련 테이블이 있는지 탐색
    for t in tables:
        if "trade" in t.lower() or "order" in t.lower() or "deal" in t.lower() or "history" in t.lower():
            print(f"매매 관련 추정 테이블: {t}")
            try:
                df_temp = pd.read_sql(f"SELECT * FROM {t} LIMIT 5", conn)
                print(df_temp)
                print("-" * 50)
            except Exception as e:
                print(f"조회 실패: {e}")

conn.close()
