import os
import sys
import sqlite3
import time

# PyQt5 환경 변수 에러 방지 (절대 경로 강제 지정)
current_dir = os.path.dirname(os.path.abspath(__file__))
qt_plugin_path = os.path.abspath(os.path.join(current_dir, "..", "ai_trader", "venv32", "Lib", "site-packages", "PyQt5", "Qt5", "plugins"))
os.environ['QT_PLUGIN_PATH'] = qt_plugin_path
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(qt_plugin_path, "platforms")

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop
from PyQt5.QtTest import QTest

class FuturesDataDownloader:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        
        self.login_loop = None
        self.tr_loop = None
        
        self.db_conn = sqlite3.connect("futures_data.db")
        self._init_db()
        
        # 시간에 따라 주간/야간 종목코드 분기 (이제 download_data에서 순차적으로 둘 다 수집합니다)
        self.day_code = "10100000"   # 주간 코스피200
        self.night_code = "10500000" # 야간 코스피200
        
        print("[FuturesDataDownloader] 키움증권 서버 로그인 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        self.login_loop = QEventLoop()
        self.login_loop.exec_()
        
    def _init_db(self):
        cursor = self.db_conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS futures_ohlcv (
                code TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                UNIQUE(code, date)
            )
        ''')
        self.db_conn.commit()
        
    def _on_login(self, err_code):
        if err_code == 0:
            print("[FuturesDataDownloader] 로그인 성공")
        else:
            print(f"[FuturesDataDownloader] 로그인 실패 (에러코드: {err_code})")
        if self.login_loop:
            self.login_loop.exit()

    def download_data(self):
        cursor = self.db_conn.cursor()
        
        # 주간(10100000)과 야간(10500000)을 모두 수집합니다.
        for code in [self.day_code, self.night_code]:
            self.api_req_code = code
            self.db_save_code = code
            
            print(f"\n[다운로드 시작] 코스피200 선물 - 5분봉 (요청코드: {self.api_req_code})")
            
            self.has_next = True
            self.prev_next = "0"
            self.request_count = 0
            
            while self.has_next:
                self._request_tr(self.prev_next)
                time.sleep(0.6) # TR 제한(초당 5회) 안전 버퍼
                self.request_count += 1
                
                # 최대 30번 연속 조회 (약 1.5년치 5분봉 확보)
                if self.request_count >= 30:
                    print(f"    -> 최대 과거 조회 한도 도달 ({self.request_count}회). 다음 종목으로 넘어갑니다.")
                    break
            
        self.db_conn.close()
        print("\n모든 과거 데이터 수집이 완료되었습니다.")
        if hasattr(self, 'app'):
            self.app.quit()
        sys.exit(0)
        
    def _request_tr(self, prev_next="0"):
        print(f" -> TR 요청 중... (opt50029: 선물분차트조회, 연속조회: {prev_next})")
        
        # TR 입력값 설정
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", self.api_req_code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", "5") # 5분봉
        
        # 선물 TR 조회
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선물분차트조회", "opt50029", int(prev_next), "5029")
        
        self.tr_loop = QEventLoop()
        self.tr_loop.exec_()
        
    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "선물분차트조회":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f"    수신된 데이터 개수: {cnt}개 캔들")
            
            cursor = self.db_conn.cursor()
            inserted = 0
            
            for i in range(cnt):
                date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "체결시간").strip()
                open_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "시가").strip()))
                high_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "고가").strip()))
                low_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "저가").strip()))
                close_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()))
                vol = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "거래량").strip()))
                
                try:
                    cursor.execute('''
                        INSERT INTO futures_ohlcv (code, date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (self.db_save_code, date, open_p, high_p, low_p, close_p, vol))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass # 중복 스킵
                    
            self.db_conn.commit()
            print(f"    DB 저장 완료 ({inserted}개 신규)")
            
            if str(next_str).strip() == "2":
                self.has_next = True
                self.prev_next = "2"
            else:
                self.has_next = False
                
            if self.tr_loop:
                self.tr_loop.exit()

if __name__ == "__main__":
    downloader = FuturesDataDownloader()
    downloader.download_data()
