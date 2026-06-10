import sqlite3
import pandas as pd
import numpy as np
import ta
import time
from datetime import datetime
import sys
import json
import os
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
        # 환경설정 로드 시도 (상위 폴더의 config 기준)
        target_day = "10100000"
        target_night = "10500000"
        try:
            # 여러 경로 후보 탐색
            paths = ["../config/config_local.json", "config/config_local.json", "../config/config.json", "config/config.json"]
            for p in paths:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        futures_settings = cfg.get("futures_settings", {})
                        if futures_settings:
                            target_day = futures_settings.get("target_code_day", target_day)
                            target_night = futures_settings.get("target_code_night", target_night)
                            break
        except Exception as e:
            print(f"설정 파일 로드 실패 (기본값 사용): {e}")

        now = time.localtime()
        if now.tm_hour >= 17 or now.tm_hour < 6:
            self.target_code = target_night
        else:
            self.target_code = target_day
        
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
                msg = f"🚀 <b>[선물 매수(롱) 진입] 변동성 돌파</b>\n• 현재가: {price:,.2f}pt\n• 근거: 당일 시가 기준 상방 돌파 (K=0.5)"
            elif signal_type == 'SHORT_ENTER':
                msg = f"📉 <b>[선물 매도(숏) 진입] 변동성 돌파</b>\n• 현재가: {price:,.2f}pt\n• 근거: 당일 시가 기준 하방 돌파 (K=0.5)"
            elif signal_type == 'LONG_EXIT':
                msg = f"✅ <b>[선물 롱 청산] 오버나이트 갭 수익</b>\n• 현재가: {price:,.2f}pt\n• 사유: 익일 아침 시가 강제 청산"
            elif signal_type == 'SHORT_EXIT':
                msg = f"✅ <b>[선물 숏 청산] 오버나이트 갭 수익</b>\n• 현재가: {price:,.2f}pt\n• 사유: 익일 아침 시가 강제 청산"
            else:
                msg = None
            
            if msg:
                notifier.send_message(msg)

    def run_analysis(self):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 선물 전략 엔진 분석 시작 (변동성 돌파 K=0.5)...")
        conn = sqlite3.connect(self.db_path)
        query = "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code = ? ORDER BY date ASC"
        df = pd.read_sql_query(query, conn, params=(self.target_code,))
        conn.close()
        
        if len(df) < 50:
            print("데이터가 충분하지 않습니다 (최소 50봉 필요).")
            return
            
        # 키움 일자 필드를 datetime으로 변환
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
        # 혹시 중복된 캔들이 들어왔을 경우 최신 캔들만 남김
        df.drop_duplicates(subset=['date'], keep='last', inplace=True)
        df.set_index('date', inplace=True)
        
        # 래리 윌리엄스 변동성 돌파 로직을 위한 일봉 데이터 집계
        df['date_only'] = df.index.date
        daily_stats = df.groupby('date_only').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
        daily_stats['range'] = daily_stats['high'] - daily_stats['low']
        # 전일 변동폭 계산
        daily_stats['prev_range'] = daily_stats['range'].shift(1)
        
        # 일봉 데이터를 다시 5분봉 데이터 프레임에 결합 (결측치는 이전 값으로 채움)
        df = df.join(daily_stats[['prev_range']], on='date_only')
        
        # 최신 캔들과 당일 시가 추출
        curr_row = df.iloc[-1]
        current_price = curr_row['close']
        current_time = df.index[-1]
        date_only = curr_row['date_only']
        day_open = daily_stats.loc[date_only, 'open']
        prev_range = curr_row['prev_range']
        
        if pd.isna(prev_range):
            print("전일 변동폭 데이터가 아직 수집되지 않았습니다.")
            return

        # 핫-리로드: active_strategy.json에서 승인된 K값 읽어오기
        K = 0.5 # 기본값
        strategy_file = r"G:\내 드라이브\AI_Trading_Data\active_strategy.json"
        if os.path.exists(strategy_file):
            try:
                with open(strategy_file, "r", encoding="utf-8") as f:
                    strat_data = json.load(f)
                    K = strat_data.get("K", 0.5)
            except Exception as e:
                print(f"전략 파일 읽기 에러: {e}")
                
        print(f" -> 적용 파라미터: K = {K}")
        target_price_long = day_open + (prev_range * K)
        target_price_short = day_open - (prev_range * K)
        
        now_dt = datetime.now()
        
        # 시간 검사 (오버나이트 홀딩 후 익일 아침 시가 청산)
        # 선물 주간장 개장 시간: 08:45
        # 아침 08:45 ~ 08:50 사이에 전일 보유 포지션을 전량 청산하여 갭 수익 확보
        is_morning_open = False
        if now_dt.hour == 8 and 45 <= now_dt.minute <= 50:
            is_morning_open = True
            
        # 가상 포지션 조회
        position, entry_price = self.get_current_position()
        print(f" -> 현재 상태: 추정 포지션 [{position}], 당일시가 [{day_open:.2f}], 전일변동폭 [{prev_range:.2f}]")
        print(f" -> 목표 타점: 상방돌파 [{target_price_long:.2f}] / 하방돌파 [{target_price_short:.2f}] / 현재가 [{current_price:.2f}]")
        
        # 1. 청산 로직 (오버나이트 갭 수익 청산)
        if position != 0:
            if is_morning_open:
                if position == 1:
                    self.emit_signal('LONG_EXIT', current_price)
                    print(" -> [청산] 익일 아침 시가 갭 수익 강제 청산 (롱 익/손절)")
                elif position == -1:
                    self.emit_signal('SHORT_EXIT', current_price)
                    print(" -> [청산] 익일 아침 시가 갭 수익 강제 청산 (숏 익/손절)")
            else:
                print(" -> [홀딩] 익일 아침 08:45(시가)까지 포지션을 오버나이트 홀딩합니다.")
            return # 청산 상태이거나 포지션 유지 중이므로 신규 진입 생략
            
        # 2. 신규 진입 로직 (변동성 돌파)
        if position == 0 and not is_morning_open:
            # 매수 (Long) 조건: 고가가 상방 돌파 목표가를 터치했을 때
            if curr_row['high'] >= target_price_long:
                self.emit_signal('LONG_ENTER', current_price)
                print(f" -> [진입] 롱 포지션 변동성 돌파! (돌파선: {target_price_long:.2f})")
                
            # 매도 (Short) 조건: 저가가 하방 돌파 목표가를 터치했을 때
            elif curr_row['low'] <= target_price_short:
                self.emit_signal('SHORT_ENTER', current_price)
                print(f" -> [진입] 숏 포지션 변동성 하락 돌파! (돌파선: {target_price_short:.2f})")

if __name__ == "__main__":
    engine = FuturesStrategyEngine()
    engine.run_analysis()
