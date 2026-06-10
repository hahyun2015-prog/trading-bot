import sqlite3
import pandas as pd

conn = sqlite3.connect('futures_data.db')
df = pd.read_sql_query(
    "SELECT date, open, high, low, close FROM futures_ohlcv WHERE code = '10500000' AND date LIKE '20260605%' ORDER BY date ASC",
    conn
)
conn.close()

print("=== 6월 5일 5분봉 데이터 ===")
print(df)

# 시각화 또는 시뮬레이션
day_open = df.iloc[0]['open'] # 첫 봉의 시가
prev_range = 36.04
K = 0.30
target_long = day_open + prev_range * K
target_short = day_open - prev_range * K

print(f"\n설정:")
print(f"  day_open: {day_open:.2f}")
print(f"  prev_range: {prev_range:.2f}")
print(f"  K: {K:.2f}")
print(f"  target_long: {target_long:.2f}")
print(f"  target_short: {target_short:.2f}")

pos = 0
entry_price = 0.0
trades = []

# 시뮬레이션 루프
for idx, row in df.iterrows():
    t = row['date']
    o, h, l, c = row['open'], row['high'], row['low'], row['close']
    
    # 08:45 ~ 08:50 사이 청산 (이날은 신규 진입일이므로 첫 캔들에서는 청산 안됨)
    # 포지션 보유 중인 경우
    if pos != 0:
        # 손절/익절 체크
        pnl_pt = (c - entry_price) if pos == 1 else (entry_price - c)
        
        # 3.5pt 손절, 5.0pt 익절
        sl_hit = False
        tp_hit = False
        
        if pos == 1:
            if l <= entry_price - 3.5:
                sl_hit = True
            if h >= entry_price + 5.0:
                tp_hit = True
        else:
            if h >= entry_price + 3.5:
                sl_hit = True
            if l <= entry_price - 5.0:
                tp_hit = True
                
        if sl_hit and tp_hit:
            print(f"[{t}] SL과 TP 모두 터치됨. 방향 확인 필요. h={h}, l={l}, entry={entry_price}")
            # 안전하게 청산 처리
            pos = 0
            trades.append({'time': t, 'type': 'EXIT_BOTH', 'price': entry_price - 3.5 if pos==1 else entry_price + 3.5})
        elif sl_hit:
            print(f"[{t}] 손절(SL) 터치! 가격: {entry_price - 3.5 if pos==1 else entry_price + 3.5}")
            trades.append({'time': t, 'type': 'EXIT_SL', 'price': entry_price - 3.5 if pos==1 else entry_price + 3.5})
            pos = 0
        elif tp_hit:
            print(f"[{t}] 익절(TP) 터치! 가격: {entry_price + 5.0 if pos==1 else entry_price - 5.0}")
            trades.append({'time': t, 'type': 'EXIT_TP', 'price': entry_price + 5.0 if pos==1 else entry_price - 5.0})
            pos = 0
            
    # 진입 체크 (포지션 없을 때)
    if pos == 0:
        # 08:45:00 첫 봉부터 감시
        if h >= target_long:
            pos = 1
            entry_price = target_long
            print(f"[{t}] 롱 진입 (가격: {entry_price:.2f})")
            trades.append({'time': t, 'type': 'LONG_ENTER', 'price': entry_price})
        elif l <= target_short:
            pos = -1
            entry_price = target_short
            print(f"[{t}] 숏 진입 (가격: {entry_price:.2f})")
            trades.append({'time': t, 'type': 'SHORT_ENTER', 'price': entry_price})

if pos != 0:
    # 장 마감 청산 또는 홀딩
    print(f"장 종료 시점 포지션 보유 중: {'LONG' if pos==1 else 'SHORT'}, 평단: {entry_price:.2f}, 종가: {df.iloc[-1]['close']:.2f}")
