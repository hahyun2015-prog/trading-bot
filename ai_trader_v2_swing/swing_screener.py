import os
import sys
import sqlite3
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime

if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtTest import QTest
import notifier

class SwingScreener:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        
        self.login_loop = None
        self.tr_loop = None
        
        self.candidates = []
        self.smart_money_stocks = []
        self.final_breakout_stocks = []
        
        self.current_foreign_net = 0
        self.current_inst_net = 0
        self.daily_chart_data = []
        
        # DB 초기화
        self._init_db()
        
        print("[SwingScreener] 키움증권 서버 로그인 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        self.login_loop = QEventLoop()
        self.login_loop.exec_()
        
    def _init_db(self):
        conn = sqlite3.connect("swing_data.db")
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                name TEXT,
                signal_type TEXT,
                strategy_name TEXT,
                price INTEGER,
                open_price INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'PENDING'
            )
        ''')
        conn.commit()
        conn.close()

    def _on_login(self, err_code):
        if err_code == 0:
            print("[SwingScreener] 로그인 성공")
        else:
            print(f"[SwingScreener] 로그인 실패 (에러코드: {err_code})")
        if self.login_loop:
            self.login_loop.exit()
            
    def fetch_top_volume_stocks(self, limit=30):
        print(f"\n[1단계] 거래대금 상위 및 상승률 5% 이상 종목 추출 (Naver Finance)...")
        url = "https://finance.naver.com/sise/sise_quant_high.naver"
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.content, "html.parser")
            rows = soup.select("table.type_2 tbody tr")
            
            count = 0
            for r in rows:
                if count >= limit:
                    break
                cols = r.select("td")
                if len(cols) > 5:
                    name_tag = cols[1].select_one("a")
                    if not name_tag: continue
                    name = name_tag.text.strip()
                    code = name_tag["href"].split("code=")[1]
                    
                    change_rate_str = cols[4].text.strip().replace('%', '').replace('+', '').replace('-', '')
                    change_rate = float(change_rate_str) if change_rate_str else 0
                    
                    if "+" in cols[4].text and change_rate >= 5.0:
                        self.candidates.append({"code": code, "name": name, "change": change_rate})
                        count += 1
                        print(f" -> 후보 발굴: {name} (+{change_rate}%)")
        except Exception as e:
            print(f"크롤링 오류: {e}")
            
        print(f" => 총 {len(self.candidates)}개 거래대금 상위 주도주 후보 발굴 완료.")
        
    def check_smart_money(self):
        print(f"\n[2단계] 외국인/기관 수급 확인 (opt10059)...")
        today = datetime.now().strftime("%Y%m%d")
        
        for item in self.candidates:
            code = item['code']
            QTest.qWait(500) # TR 제한 방지
            
            self.current_foreign_net = 0
            self.current_inst_net = 0
            
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "일자", today)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "금액수량구분", "1")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "매매구분", "0")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "단위구분", "1")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "수급조회", "opt10059", 0, "1059")
            
            self.tr_loop = QEventLoop()
            self.tr_loop.exec_()
            
            if self.current_foreign_net > 0 or self.current_inst_net > 0:
                print(f" [PASS] {item['name']} (외인: {self.current_foreign_net:,} / 기관: {self.current_inst_net:,})")
                item['foreign_net'] = self.current_foreign_net
                item['inst_net'] = self.current_inst_net
                self.smart_money_stocks.append(item)
            else:
                print(f" [DROP] {item['name']} (수급 이탈)")
                
        print(f" => 수급 합격: {len(self.smart_money_stocks)}종목.")

    def analyze_daily_chart(self):
        print(f"\n[3단계] 일봉 차트 분석 (횡보 후 장대양봉 및 신고가 판별)...")
        today = datetime.now().strftime("%Y%m%d")
        
        for item in self.smart_money_stocks:
            code = item['code']
            QTest.qWait(500)
            
            self.daily_chart_data = []
            
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "기준일자", today)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "일봉조회", "opt10081", 0, "1081")
            
            self.tr_loop = QEventLoop()
            self.tr_loop.exec_()
            
            if len(self.daily_chart_data) < 60:
                print(f" [DROP] {item['name']} (상장일 부족)")
                continue
                
            # 데이터 분석
            # daily_chart_data: [0] 당일, [1] 전일...
            # 항목: (date, open, high, low, close, volume)
            current = self.daily_chart_data[0]
            
            # 최근 20일 거래량 평균 (오늘 제외)
            prev_20_vols = [d[5] for d in self.daily_chart_data[1:21]]
            avg_vol_20 = sum(prev_20_vols) / len(prev_20_vols) if prev_20_vols else 1
            
            # 최근 60일 최고가
            high_60d = max([d[2] for d in self.daily_chart_data[1:61]])
            
            # 조건 1: 오늘 거래량이 최근 20일 평균의 1.5배 이상 (장대 거래량)
            is_volume_burst = current[5] > (avg_vol_20 * 1.5)
            
            # 조건 2: 오늘 종가가 최근 60일 전고점을 돌파했거나 근접했는지 (신고가 근접)
            is_breakout = current[4] >= (high_60d * 0.98)
            
            if is_volume_burst and is_breakout:
                print(f" [★매수 급소 발견★] {item['name']} - 60일 신고가 돌파 & 거래량 폭발!")
                item['current_price'] = current[4]
                item['open_price'] = current[1] # 진입 장대양봉의 시가 (중요!)
                self.final_breakout_stocks.append(item)
            else:
                print(f" [DROP] {item['name']} (차트 조건 미달: 거래량 폭발={is_volume_burst}, 신고가 돌파={is_breakout})")

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "수급조회":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            if cnt > 0:
                foreign = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "외국인투자자").strip()
                inst = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "기관계").strip()
                try:
                    self.current_foreign_net = int(foreign) if foreign else 0
                    self.current_inst_net = int(inst) if inst else 0
                except:
                    pass
            if self.tr_loop:
                self.tr_loop.exit()
                
        elif rqname == "일봉조회":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            # 최근 120일치만 수집
            limit = min(cnt, 120)
            for i in range(limit):
                date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "일자").strip()
                o = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "시가").strip()))
                h = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "고가").strip()))
                l = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "저가").strip()))
                c = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()))
                v = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "거래량").strip()))
                self.daily_chart_data.append((date, o, h, l, c, v))
                
            if self.tr_loop:
                self.tr_loop.exit()

    def save_signals(self):
        if not self.final_breakout_stocks:
            print("\n[완료] 조건에 맞는 스윙 대장주가 없습니다.")
            return
            
        conn = sqlite3.connect("swing_data.db")
        cursor = conn.cursor()
        
        msg_lines = ["🚀 <b>[스윙 대장주 종가 베팅 포착]</b>"]
        
        for item in self.final_breakout_stocks:
            cursor.execute('''
                INSERT INTO signals (code, name, signal_type, strategy_name, price, open_price)
                VALUES (?, ?, 'BUY', '신고가_돌파_스윙', ?, ?)
            ''', (item['code'], item['name'], item['current_price'], item['open_price']))
            
            msg_lines.append(f"• <b>{item['name']}</b> ({item['current_price']:,}원)")
            msg_lines.append(f"  - 손절가(시가): {item['open_price']:,}원 이탈 시")
            
        conn.commit()
        conn.close()
        
        print(f"\n[완료] 총 {len(self.final_breakout_stocks)}개의 스윙 대장주 시그널이 DB에 전송되었습니다.")
        notifier.send_message("\n".join(msg_lines))

if __name__ == "__main__":
    print("===========================================")
    print(" [Swing Screener] 주도주 종가 베팅 발굴기")
    print("===========================================")
    screener = SwingScreener()
    screener.fetch_top_volume_stocks(limit=30)
    
    if screener.candidates:
        screener.check_smart_money()
        if screener.smart_money_stocks:
            screener.analyze_daily_chart()
            screener.save_signals()
            
    print("발굴 종료.")
