import sqlite3
import pandas as pd
import ta
import notifier

def load_intraday_data(code, conn):
    """SQLite DB에서 특정 종목의 분봉 데이터를 불러옵니다."""
    query = "SELECT date, open, high, low, close, volume FROM intraday_ohlcv WHERE code = ? ORDER BY date ASC"
    df = pd.read_sql_query(query, conn, params=(code,))
    if not df.empty:
        # 키움증권 일자 필드(예: 20260511132000 등)를 datetime으로 파싱 (형식에 따라 다를 수 있으나 pandas가 자동 인식)
        # 문자열 형식을 명확히 하기 위해 errors='coerce' 사용
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        # 혹시 형식이 다르면 기본 파서 사용
        if df['date'].isnull().all():
            df['date'] = pd.to_datetime(df['date'])
        
        df.set_index('date', inplace=True)
    return df

def calculate_vwap(df):
    """당일 기준 VWAP (Volume Weighted Average Price) 계산"""
    # 날짜별로 그룹화하여 VWAP 계산
    df['date_only'] = df.index.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['vwap'] = df.groupby('date_only').apply(
        lambda x: (x['typ_price'] * x['volume']).cumsum() / x['volume'].cumsum()
    ).reset_index(level=0, drop=True)
    return df

def apply_rsi(df, window=14):
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=window).rsi()
    return df

def check_hidden_bullish_divergence(df, window=5):
    """히든 강세 다이버전스 판별: 주가 저점은 높아지나, RSI 저점은 낮아지는 현상"""
    if len(df) < window * 2:
        return False
        
    recent_period = df.iloc[-window:]
    prev_period = df.iloc[-window*2:-window]
    
    recent_price_low = recent_period['low'].min()
    prev_price_low = prev_period['low'].min()
    
    recent_rsi_low = recent_period['rsi'].min()
    prev_rsi_low = prev_period['rsi'].min()
    
    if pd.isna(recent_rsi_low) or pd.isna(prev_rsi_low):
        return False
        
    # 주가 저점은 전저점 대비 높거나 같게 지지되는데, RSI 저점은 전저점보다 낮아짐(에너지 응축)
    if recent_price_low >= prev_price_low and recent_rsi_low < prev_rsi_low:
        return True
    return False

def check_vwap_pullback(df):
    """VWAP 눌림목 + 거래량 감소(Dry Up) 패턴 판별"""
    if len(df) < 10:
        return False
        
    latest = df.iloc[-1]
    
    if pd.isna(latest['vwap']):
        return False
        
    # 주가가 VWAP 근처인지 (VWAP 대비 상하 1.5% 이내)
    vwap_ratio = abs(latest['close'] - latest['vwap']) / latest['vwap']
    near_vwap = vwap_ratio <= 0.015 
    
    # 거래량 감소 확인 (최근 5봉 평균 거래량이 직전 5봉 평균보다 적은지)
    recent_vol = df.iloc[-5:]['volume'].mean()
    prev_vol = df.iloc[-10:-5]['volume'].mean()
    vol_dry_up = recent_vol < prev_vol
    
    return near_vwap and vol_dry_up

def analyze_combo1_signals(df):
    """조합 1 (VWAP 눌림목 + 히든 강세 다이버전스) 기반 매수 신호 판별"""
    if len(df) < 30:
        return []
        
    latest = df.iloc[-1]
    signals = []
    
    # 1. VWAP 및 거래량 마름 확인
    is_pullback = check_vwap_pullback(df)
    
    # 2. 히든 강세 다이버전스 확인
    is_divergence = check_hidden_bullish_divergence(df)
    
    # 3. 추가 필터: 종가가 10분 이평선 위에 있거나 근접해 있는지 (최소한의 추세 유지)
    ma_10 = df['close'].rolling(window=10).mean().iloc[-1]
    is_uptrend = latest['close'] >= (ma_10 * 0.99) # 10이평에서 크게 이탈하지 않아야 함
    
    # Combo 1 달성 조건
    if is_pullback and is_divergence and is_uptrend:
        signals.append("A급 타점 (VWAP 눌림목 + 히든 다이버전스 동시 발생)")
        
    return signals

def run_strategy_engine():
    conn = sqlite3.connect("kiwoom_data.db")
    cursor = conn.cursor()
    
    # 기존 signals 테이블 유지하되, PENDING 상태의 오래된 신호 정리 (옵션)
    # PENDING 상태의 오래된 신호 정리 및 signals 테이블 생성
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            signal_type TEXT,
            strategy_name TEXT,
            price INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'PENDING'
        )
    ''')
    
    cursor.execute("SELECT code, name FROM top_volume_theme")
    universe = cursor.fetchall()
    
    print(f"총 {len(universe)}개 주도주를 대상으로 Combo 1 (VWAP + 다이버전스) 정밀 분석을 시작합니다.")
    
    signal_count = 0
    for code, name in universe:
        df = load_intraday_data(code, conn)
        
        if df.empty or len(df) < 30:
            print(f" [{name}] 분봉 데이터 부족으로 분석 생략")
            continue
            
        df = calculate_vwap(df)
        df = apply_rsi(df)
        
        # 1. 매수 시그널 검사
        buy_signals = analyze_combo1_signals(df)
        if buy_signals:
            current_price = int(df.iloc[-1]['close'])
            signal_msg = " | ".join(buy_signals)
            
            print(f"\n★ [매수 시그널 포착!] {name}({code})")
            
            cursor.execute('''
                INSERT INTO signals (code, name, signal_type, strategy_name, price)
                VALUES (?, ?, 'BUY', ?, ?)
            ''', (code, name, signal_msg, current_price))
            signal_count += 1
            
            noti_msg = f"🎯 <b>[A급 타점 발견] {name}</b>\n• 현재가: {current_price:,}원\n• 근거: Combo 1 (VWAP 눌림목 + 다이버전스)"
            notifier.send_message(noti_msg)
            

    conn.commit()
    conn.close()
    
    print(f"\n[분석 완료] 총 {signal_count}개의 최적 타점(Combo 1)이 발굴되었습니다.")
    print("발굴된 신호는 order_manager가 즉시 모의투자 계좌로 주문 집행합니다.")

if __name__ == "__main__":
    run_strategy_engine()
