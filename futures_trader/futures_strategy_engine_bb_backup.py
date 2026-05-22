import sqlite3
import pandas as pd
import numpy as np
import ta
import time
from datetime import datetime
import sys
sys.path.append(r"..\ai_trader")
try:
    import notifier
except ImportError:
    notifier = None

class FuturesStrategyEngine:
    def __init__(self):
        self.db_path = "futures_data.db"
        self._set_target_code()
        self._init_db()
        
    def _set_target_code(self):
        now = time.localtime()
        if now.tm_hour >= 17 or now.tm_hour < 6:
            self.target_code = "10500000"
        else:
            self.target_code = "10100000"
        
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                signal_type TEXT,
                price REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'PENDING'
            )
        ''')
        conn.commit()
        conn.close()

    def get_current_position(self):
        """최근 발생 및 체결된 시그널을 기반으로 가상의 현재 포지션을 추론합니다."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # 최근 신호(PENDING 또는 EXECUTED) 조회 (FAILED 제외)
        cursor.execute('''
            SELECT signal_type, price FROM signals 
            WHERE code = ? AND status IN ('PENDING', 'EXECUTED')
            ORDER BY id DESC LIMIT 1
        ''', (self.target_code,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return 0, 0.0 # position, entry_price
            
        signal_type, price = row
        if signal_type == 'LONG_ENTER':
            return 1, price
        elif signal_type == 'SHORT_ENTER':
            return -1, price
        elif signal_type in ('LONG_EXIT', 'SHORT_EXIT'):
            return 0, 0.0
            
        return 0, 0.0
        
    def emit_signal(self, signal_type, price):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO signals (code, signal_type, price)
            VALUES (?, ?, ?)
        ''', (self.target_code, signal_type, price))
        conn.commit()
        conn.close()
        print(f"\n[[SIGNAL] 시그널 발생] {self.target_code} | 타입: {signal_type} | 현재가: {price:,.2f}")
        
        if notifier:
            if signal_type == 'LONG_ENTER':
                msg = f"🚀 <b>[선물 매수(롱) 신호] 코스피200</b>\n• 현재가: {price:,.2f}pt\n• 근거: 볼린저 하단 터치 & RSI 조건"
            elif signal_type == 'SHORT_ENTER':
                msg = f"📉 <b>[선물 매도(숏) 신호] 코스피200</b>\n• 현재가: {price:,.2f}pt\n• 근거: 볼린저 상단 터치 & RSI 조건"
            elif signal_type == 'LONG_EXIT':
                msg = f"✅ <b>[선물 롱 청산 신호] 코스피200</b>\n• 현재가: {price:,.2f}pt\n• 사유: 목표가 도달 또는 손절/시간청산"
            elif signal_type == 'SHORT_EXIT':
                msg = f"✅ <b>[선물 숏 청산 신호] 코스피200</b>\n• 현재가: {price:,.2f}pt\n• 사유: 목표가 도달 또는 손절/시간청산"
            else:
                msg = None
            
            if msg:
                notifier.send_message(msg)

    def run_analysis(self):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 선물 전략 엔진 분석 시작...")
        conn = sqlite3.connect(self.db_path)
        query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = ? ORDER BY date ASC"
        df = pd.read_sql_query(query, conn, params=(self.target_code,))
        conn.close()
        
        if len(df) < 30:
            print("데이터가 충분하지 않습니다 (최소 30봉 필요).")
            return
            
        # 키움 일자 필드를 datetime으로 변환
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        df.set_index('date', inplace=True)
        
        # 지표 계산: 볼린저 밴드 (20, 2)
        indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_m'] = indicator_bb.bollinger_mavg()
        df['bb_h'] = indicator_bb.bollinger_hband()
        df['bb_l'] = indicator_bb.bollinger_lband()
        
        # RSI (14)
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        
        # 최신 캔들과 이전 캔들 정보 추출
        if len(df) < 11:
            return
            
        curr_row = df.iloc[-1]
        current_price = curr_row['close']
        current_time = df.index[-1]
        now_dt = datetime.now()
        
        # 시간 검사 (장 마감 임박 여부)
        # 주간장 마감 임박: 15:30 이후 ~ 15:45 마감
        # 야간장 마감 임박: 04:50 이후 ~ 05:00 마감
        is_closing_time = False
        if now_dt.hour == 15 and now_dt.minute >= 30:
            is_closing_time = True
        elif now_dt.hour == 4 and now_dt.minute >= 50:
            is_closing_time = True
            
        # 히든 다이버전스 판별용
        prev_5_rsi_min = df['rsi'].iloc[-11:-6].min()
        curr_5_rsi_min = df['rsi'].iloc[-6:-1].min()
        prev_5_low_min = df['low'].iloc[-11:-6].min()
        curr_5_low_min = df['low'].iloc[-6:-1].min()
        bullish_div = curr_5_low_min <= prev_5_low_min and curr_5_rsi_min > prev_5_rsi_min
            
        prev_5_rsi_max = df['rsi'].iloc[-11:-6].max()
        curr_5_rsi_max = df['rsi'].iloc[-6:-1].max()
        prev_5_high_max = df['high'].iloc[-11:-6].max()
        curr_5_high_max = df['high'].iloc[-6:-1].max()
        bearish_div = curr_5_high_max >= prev_5_high_max and curr_5_rsi_max < prev_5_rsi_max
            
        # 가상 포지션 조회
        position, entry_price = self.get_current_position()
        print(f" -> 현재 상태: 추정 포지션 [{position}], 진입단가 [{entry_price}], 최근 종가 [{current_price}]")
        
        STOP_LOSS_PT = 1.0  # 백테스트 최적 손절값
        TAKE_PROFIT_PT = 2.0 # 백테스트 최적 익절값 (옵션 A)

        # 1. 청산 로직
        if position != 0:
            if position == 1:
                # 롱 포지션 청산: 손절, 고정익절(+2pt), 장마감
                if current_price <= entry_price - STOP_LOSS_PT:
                    self.emit_signal('LONG_EXIT', current_price)
                    print(" -> [청산] 롱 포지션 손절 도달 (-1.0pt)")
                elif current_price - entry_price >= TAKE_PROFIT_PT:
                    self.emit_signal('LONG_EXIT', current_price)
                    print(" -> [청산] 롱 포지션 익절 도달 (+2.0pt)")
                elif is_closing_time:
                    self.emit_signal('LONG_EXIT', current_price)
                    print(" -> [청산] 장 마감 임박 강제 청산")
                    
            elif position == -1:
                # 숏 포지션 청산: 손절, 고정익절(+2pt), 장마감
                if current_price >= entry_price + STOP_LOSS_PT:
                    self.emit_signal('SHORT_EXIT', current_price)
                    print(" -> [청산] 숏 포지션 손절 도달 (-1.0pt)")
                elif entry_price - current_price >= TAKE_PROFIT_PT:
                    self.emit_signal('SHORT_EXIT', current_price)
                    print(" -> [청산] 숏 포지션 익절 도달 (+2.0pt)")
                elif is_closing_time:
                    self.emit_signal('SHORT_EXIT', current_price)
                    print(" -> [청산] 장 마감 임박 강제 청산")
            return # 청산 발생 시 진입은 생략
            
        # 2. 신규 진입 로직 (Strict: 밴드 정확히 터치 & 엄격한 RSI)
        if position == 0 and not is_closing_time:
            # 매수 (Long) 조건: 하단 밴드 터치/이탈 및 (RSI 30 이하 또는 강세 다이버전스)
            if curr_row['low'] <= curr_row['bb_l'] and (curr_row['rsi'] <= 30 or bullish_div):
                self.emit_signal('LONG_ENTER', current_price)
                print(" -> [진입] 롱 포지션 조건 만족 (Strict RSI/BB 적용)")
                
            # 매도 (Short) 조건: 상단 밴드 터치/돌파 및 (RSI 70 이상 또는 약세 다이버전스)
            elif curr_row['high'] >= curr_row['bb_h'] and (curr_row['rsi'] >= 70 or bearish_div):
                self.emit_signal('SHORT_ENTER', current_price)
                print(" -> [진입] 숏 포지션 조건 만족 (Strict RSI/BB 적용)")

if __name__ == "__main__":
    engine = FuturesStrategyEngine()
    engine.run_analysis()
