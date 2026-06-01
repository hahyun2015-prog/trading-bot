import os
import sys
import sqlite3
import time

if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop
from PyQt5.QtTest import QTest

class DataDownloader:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        
        self.login_loop = None
        self.tr_loop = None
        
        self.db_conn = sqlite3.connect("kiwoom_data.db")
        self._init_db()
        
        self.target_stocks = [
            {"code": "005930", "name": "삼성전자"},
            {"code": "000660", "name": "SK하이닉스"},
            {"code": "005380", "name": "현대차"},
            {"code": "012450", "name": "한화에어로스페이스"},
            {"code": "274090", "name": "AP위성"},
            {"code": "027360", "name": "에이텍"},
            {"code": "036930", "name": "주성엔지니어링"},
            {"code": "131290", "name": "토비스"},
            {"code": "001450", "name": "현대해상"},
            {"code": "003690", "name": "코리안리"},
            {"code": "000370", "name": "한화손해보험"}
        ]
        
        self.current_stock_code = ""
        self.has_next = False
        self.request_count = 0
        self.max_requests_per_stock = 8 # 8번 = 약 7200개 (약 1.6개월 이상)
        
        print("[DataDownloader] 키움증권 서버 로그인 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        self.login_loop = QEventLoop()
        self.login_loop.exec_()
        
    def _init_db(self):
        cursor = self.db_conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_ohlcv (
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
        self.db_conn.commit()
        
    def _on_login(self, err_code):
        if err_code == 0:
            print("[DataDownloader] 로그인 성공")
        else:
            print(f"[DataDownloader] 로그인 실패 (에러코드: {err_code})")
        if self.login_loop:
            self.login_loop.exit()
 
    def download_data(self):
        cursor = self.db_conn.cursor()
        
        for stock in self.target_stocks:
            self.current_stock_code = stock["code"]
            print(f"\n[다운로드 시작] {stock['name']} ({self.current_stock_code}) - 과거 1.6개월 3분봉")
            
            # 이전 백테스트 데이터 삭제
            cursor.execute("DELETE FROM backtest_ohlcv WHERE code = ?", (self.current_stock_code,))
            cursor.execute("DELETE FROM intraday_ohlcv WHERE code = ?", (self.current_stock_code,))
            self.db_conn.commit()
            
            self.has_next = False
            self.request_count = 0
            
            self._request_tr(next_flag=0)
            
            while self.has_next and self.request_count < self.max_requests_per_stock:
                print(f" -> 추가 데이터 수집 대기 중 (안전한 조회를 위해 4초 대기)...")
                QTest.qWait(4000) # TR 제한 방지 (매우 중요)
                self._request_tr(next_flag=2)
                
            print(f" => {stock['name']} 다운로드 완료.")
            
        self.db_conn.close()
        print("\n모든 데이터 수집이 완료되었습니다.")
        
    def _request_tr(self, next_flag):
        self.request_count += 1
        print(f" -> TR 요청 중... ({self.request_count}/{self.max_requests_per_stock})")
        
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", self.current_stock_code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "틱범위", "3") # 3분봉
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식기봉차트조회", "opt10080", next_flag, "1080")
        
        self.tr_loop = QEventLoop()
        self.tr_loop.exec_()
        
    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "주식기봉차트조회":
            self.has_next = (next_str == '2')
            
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f"    수신된 데이터 개수: {cnt}개 캔들")
            
            cursor = self.db_conn.cursor()
            inserted = 0
            
            for i in range(cnt):
                date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "체결시간").strip()
                open_p = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "시가").strip()))
                high_p = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "고가").strip()))
                low_p = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "저가").strip()))
                close_p = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()))
                vol = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "거래량").strip()))
                
                try:
                    cursor.execute('''
                        INSERT INTO backtest_ohlcv (code, date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (self.current_stock_code, date, open_p, high_p, low_p, close_p, vol))
                    cursor.execute('''
                        INSERT INTO intraday_ohlcv (code, date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (self.current_stock_code, date, open_p, high_p, low_p, close_p, vol))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass # 중복 스킵
                    
            self.db_conn.commit()
            print(f"    DB 저장 완료 ({inserted}개 신규)")
            
            if self.tr_loop:
                self.tr_loop.exit()

if __name__ == "__main__":
    downloader = DataDownloader()
    downloader.download_data()
