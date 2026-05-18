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
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop
from PyQt5.QtTest import QTest

class DayScreener:
    def __init__(self, kiwoom=None):
        if kiwoom is None:
            self.app = QApplication(sys.argv)
            self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
            self.kiwoom.OnEventConnect.connect(self._on_login)
            self.is_standalone = True
        else:
            self.kiwoom = kiwoom
            self.is_standalone = False
            
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        
        self.login_loop = None
        self.tr_loop = None
        self.current_code = ""
        self.ohlcv_data = []
        
        self._init_db()
        
        if self.is_standalone:
            print("[DayScreener] 키움증권 서버 로그인 중...")
            self.kiwoom.dynamicCall("CommConnect()")
            self.login_loop = QEventLoop()
            self.login_loop.exec_()

    def _init_db(self):
        self.conn = sqlite3.connect("unified_data.db")
        cursor = self.conn.cursor()
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
        self.conn.commit()

    def _on_login(self, err_code):
        if err_code == 0:
            print("[DayScreener] 로그인 성공")
        if self.login_loop:
            self.login_loop.exit()

    def run_screening(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT code, name FROM top_volume_theme")
        target_universe = cursor.fetchall()
        
        if not target_universe:
            print("[DayScreener] top_volume_theme 에 종목이 없습니다.")
            return
            
        print(f"\n[DayScreener] 총 {len(target_universe)}개 주도주의 3분봉 데이터 수집을 시작합니다.")
        
        for idx, (code, name) in enumerate(target_universe):
            print(f"[{idx+1}/{len(target_universe)}] {name}({code}) 3분봉 데이터 요청 중...")
            self.current_code = code
            self.ohlcv_data = []
            
            QTest.qWait(500)
            
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "틱범위", "3")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식분봉차트조회", "opt10080", 0, "1080")
            
            self.tr_loop = QEventLoop()
            self.tr_loop.exec_()
            
            if self.ohlcv_data:
                db_data = [[code] + list(row) for row in self.ohlcv_data]
                cursor.executemany('''
                    REPLACE INTO intraday_ohlcv (code, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', db_data)
                self.conn.commit()
                print(f"  -> {name} 3분봉 데이터 {len(self.ohlcv_data)}건 DB 저장 완료.")
            else:
                print(f"  -> {name} 데이터가 없습니다.")
                
            QTest.qWait(500)
            
        print("\n[DayScreener] 데이터 수집 완료.")

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "주식분봉차트조회":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            # 최근 100봉 정도만 저장
            limit = min(cnt, 100)
            for i in range(limit):
                date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "체결시간").strip()
                o = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "시가").strip()))
                h = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "고가").strip()))
                l = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "저가").strip()))
                c = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()))
                v = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "거래량").strip()))
                self.ohlcv_data.append((date, o, h, l, c, v))
                
            if self.tr_loop:
                self.tr_loop.exit()

if __name__ == "__main__":
    screener = DayScreener()
    screener.run_screening()

