import sys
import sqlite3
import os
import time
import PyQt5

# PyQt5 플러그인 경로 설정 (환경 변수 충돌 방지)
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

from kiwoom_api import KiwoomAPI

def init_db():
    conn = sqlite3.connect("kiwoom_data.db")
    cursor = conn.cursor()
    # 종목 유니버스 테이블 생성
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS kospi_universe (
            code TEXT PRIMARY KEY,
            name TEXT
        )
    ''')
    # 3분봉 데이터 테이블 생성
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS intraday_ohlcv (
            code TEXT,
            date TEXT,
            open INTEGER,
            high INTEGER,
            low INTEGER,
            close INTEGER,
            volume INTEGER,
            UNIQUE(code, date)
        )
    ''')
    conn.commit()
    return conn

def run_screener():
    app = QApplication(sys.argv)
    api = KiwoomAPI()
    
    conn = init_db()
    
    QTimer.singleShot(1000, api.login)
    
    def on_login_success():
        if hasattr(api, 'account_no'):
            print("로그인 확인 완료! 코스피 종목 스크리닝을 시작합니다.")
            
            # 1. DB에서 top_volume_theme 종목 가져오기
            cursor = conn.cursor()
            cursor.execute("SELECT code, name FROM top_volume_theme")
            target_universe = cursor.fetchall()
            
            if not target_universe:
                print("top_volume_theme 에 종목이 없습니다. theme_tracker.py 를 먼저 실행하세요.")
                conn.close()
                app.quit()
                return
                
            print(f"\n[데이터 수집] 총 {len(target_universe)}개 주도주의 3분봉 데이터 수집을 시작합니다.")
            
            for idx, (code, name) in enumerate(target_universe):
                print(f"[{idx+1}/{len(target_universe)}] {name}({code}) 3분봉 데이터 요청 중...")
                api.req_historical_data_single_page(code, "opt10080", "주식분봉차트조회", tick_range="3")
                
                if api.ohlcv_data:
                    # 데이터 구조: [date, open, high, low, close, volume]
                    db_data = [[code] + row for row in api.ohlcv_data]
                    
                    cursor.executemany('''
                        REPLACE INTO intraday_ohlcv (code, date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', db_data)
                    conn.commit()
                    print(f"  -> {name} 3분봉 데이터 {len(api.ohlcv_data)}건 DB 저장 완료.")
                else:
                    print(f"  -> {name} 데이터가 없습니다.")
                
                # 초당 5회 제한을 피하기 위해 3.6초 대기 (시간당 1000건 기준)
                # (테스트 5종목일 때는 짧게 대기해도 무방하나, 안전하게 1초 대기)
                time.sleep(1.0)
            
            print("\n데이터 수집을 마쳤습니다. 스크리너를 종료합니다.")
            conn.close()
            app.quit()
        else:
            QTimer.singleShot(1000, on_login_success)

    QTimer.singleShot(2000, on_login_success)
    app.exec_()

if __name__ == "__main__":
    run_screener()
