import os
import sys
import sqlite3
import time
import requests
from bs4 import BeautifulSoup

# PyQt5 플러그인 경로 에러 방지
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop
import notifier
from PyQt5.QtTest import QTest

class ThemeTracker:
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
        
        self.theme_leaders = [] # 크롤링된 대장주 리스트
        self.smart_money_stocks = [] # 외국인/기관 순매수 확인된 최종 종목
        
        # 임시 데이터 저장용
        self.current_foreign_net = 0
        self.current_inst_net = 0
        
        # 단독 실행일 때만 로그인 수행
        if self.is_standalone:
            print("[ThemeTracker] 키움증권 서버 로그인 중...")
            self.kiwoom.dynamicCall("CommConnect()")
            self.login_loop = QEventLoop()
            self.login_loop.exec_()
        
    def _on_login(self, err_code):
        if err_code == 0:
            print("[ThemeTracker] 로그인 성공")
        else:
            print(f"[ThemeTracker] 로그인 실패 (에러코드: {err_code})")
        if self.login_loop:
            self.login_loop.exit()

    def crawl_naver_themes(self, top_n_themes=3, top_n_stocks=3):
        """네이버 금융에서 당일 상승률 상위 테마 및 대장주 크롤링"""
        print(f"\n[Phase 1] 네이버 금융 실시간 주도 테마 상위 {top_n_themes}개 크롤링...")
        url = "https://finance.naver.com/sise/theme.naver"
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        try:
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.content, "html.parser")
            
            themes = []
            rows = soup.select("table.type_1 tr")
            for r in rows:
                cols = r.select("td.col_type1 a")
                if cols:
                    name = cols[0].text
                    link = "https://finance.naver.com" + cols[0]["href"]
                    # 등락률 파싱
                    change_tags = r.select("td.col_type2 span")
                    change_rate = change_tags[0].text.strip() if change_tags else "0.00%"
                    themes.append({"name": name, "url": link, "change": change_rate})
                    
            top_themes = themes[:top_n_themes]
            
            for theme in top_themes:
                print(f" -> 주도 테마 포착: {theme['name']} ({theme['change']})")
                
                # 각 테마 페이지 접속하여 대장주 추출
                tres = requests.get(theme['url'], headers=headers)
                tsoup = BeautifulSoup(tres.content, "html.parser")
                
                stock_rows = tsoup.select("table.type_5 tbody tr")
                stock_count = 0
                for row in stock_rows:
                    if stock_count >= top_n_stocks:
                        break
                        
                    name_td = row.select("td.name a")
                    if name_td:
                        stock_name = name_td[0].text.strip()
                        stock_code = name_td[0]["href"].split("code=")[1]
                        
                        # ETF, ETN, 스팩 등 파생/펀드 성격의 종목 제외
                        exclude_keywords = ["KODEX", "TIGER", "KBSTAR", "KINDEX", "KOSEF", "HANARO", "ARIRANG", "인버스", "레버리지", "선물", "스팩", "ETN"]
                        if any(kw in stock_name for kw in exclude_keywords):
                            continue
                        
                        self.theme_leaders.append({
                            "theme": theme['name'],
                            "code": stock_code,
                            "name": stock_name
                        })
                        stock_count += 1
                        
            print(f" => 총 {len(self.theme_leaders)}개의 테마 대장주 후보 발굴 완료.\n")
            
        except Exception as e:
            print(f"크롤링 중 오류 발생: {e}")

    def filter_smart_money(self):
        """발굴된 대장주를 대상으로 외국인/기관 순매수 여부 필터링"""
        print("[Phase 2] 스마트 머니 (외국인/기관) 쌍끌이 매수 필터링 시작...")
        import datetime
        today = datetime.datetime.now().strftime("%Y%m%d")
        
        for item in self.theme_leaders:
            code = item['code']
            name = item['name']
            theme = item['theme']
            
            # TR 요청 (초당 5회 제한 고려하여 대기)
            QTest.qWait(500)
            
            self.current_foreign_net = 0
            self.current_inst_net = 0
            
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "일자", today)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "금액수량구분", "1") # 1: 금액
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "매매구분", "0") # 0: 순매수
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "단위구분", "1") # 1: 단주
            
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "종목별투자자기관별", "opt10059", 0, "1059")
            
            self.tr_loop = QEventLoop()
            self.tr_loop.exec_()
            
            # 외국인 또는 기관 중 하나라도 강한 순매수(금액 > 0)일 경우 합격
            if self.current_foreign_net > 0 or self.current_inst_net > 0:
                print(f" [PASS] {name} ({theme}) -> 외인: {self.current_foreign_net:,} / 기관: {self.current_inst_net:,}")
                item['foreign_net'] = self.current_foreign_net
                item['inst_net'] = self.current_inst_net
                self.smart_money_stocks.append(item)
            else:
                print(f" [DROP] {name} ({theme}) -> 수급 이탈 (외인: {self.current_foreign_net:,} / 기관: {self.current_inst_net:,})")
                
        print(f"\n => 최종 {len(self.smart_money_stocks)}개의 '스마트 머니 주도주' 압축 완료.")
        self._save_to_db()

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "종목별투자자기관별":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            if cnt > 0:
                # 0번째 인덱스가 당일 데이터
                foreign = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "외국인투자자").strip()
                inst = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "기관계").strip()
                
                try:
                    self.current_foreign_net = int(foreign) if foreign else 0
                    self.current_inst_net = int(inst) if inst else 0
                except ValueError:
                    self.current_foreign_net = 0
                    self.current_inst_net = 0
                    
            if self.tr_loop:
                self.tr_loop.exit()

    def _save_to_db(self):
        conn = sqlite3.connect("unified_data.db")
        cursor = conn.cursor()
        
        # 1. 기존 스마트머니 종목 저장 (top_volume_theme)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS top_volume_theme (
                date TEXT,
                code TEXT,
                name TEXT,
                volume TEXT,
                UNIQUE(date, code)
            )
        ''')
        
        # 2. 테마 2등주 짝짓기용 대장-부대장 매핑 테이블 (theme_mapping)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS theme_mapping (
                date TEXT,
                theme TEXT,
                leader_code TEXT,
                leader_name TEXT,
                follower_code TEXT,
                follower_name TEXT,
                UNIQUE(date, theme)
            )
        ''')
        
        import datetime
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        cursor.execute("DELETE FROM top_volume_theme WHERE date = ?", (today,))
        cursor.execute("DELETE FROM theme_mapping WHERE date = ?", (today,))
        
        # 스마트 머니 종목 저장
        for item in self.smart_money_stocks:
            info = f"[{item['theme']}] 외인:{item['foreign_net']} 기관:{item['inst_net']}"
            cursor.execute('''
                INSERT OR REPLACE INTO top_volume_theme (date, code, name, volume)
                VALUES (?, ?, ?, ?)
            ''', (today, item['code'], item['name'], info))
            
        # 대장주-2등주 매핑 저장 (수급 통과 여부 상관없이 모멘텀 트레이딩용으로 크롤링 데이터 원본 사용)
        theme_groups = {}
        for item in self.theme_leaders:
            t = item['theme']
            if t not in theme_groups:
                theme_groups[t] = []
            theme_groups[t].append(item)
            
        mapping_count = 0
        for t, stocks in theme_groups.items():
            if len(stocks) >= 2:
                leader = stocks[0]
                follower = stocks[1]
                cursor.execute('''
                    INSERT OR REPLACE INTO theme_mapping (date, theme, leader_code, leader_name, follower_code, follower_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (today, t, leader['code'], leader['name'], follower['code'], follower['name']))
                mapping_count += 1
            
        conn.commit()
        conn.close()
        print(f"\n[DB 저장 완료] 최정예 주도주 {len(self.smart_money_stocks)}종목 저장 완료.")
        print(f"[DB 저장 완료] 테마 짝짓기용 1,2등주 {mapping_count}개 세트 저장 완료.")
        
        # 텔레그램 알림 전송
        if self.smart_money_stocks:
            msg = f"🔥 <b>[스마트 머니 주도주 포착]</b>\n총 {len(self.smart_money_stocks)}종목이 선정되었습니다.\n"
            for item in self.smart_money_stocks:
                msg += f"• {item['name']} ({item['theme']})\n"
            notifier.send_message(msg)
        else:
            notifier.send_message("⚠️ <b>[테마 포착]</b>\n현재 시장에 수급이 유입되는 대장주가 없습니다.")

if __name__ == "__main__":
    tracker = ThemeTracker()
    tracker.crawl_naver_themes(top_n_themes=3, top_n_stocks=3)
    if tracker.theme_leaders:
        tracker.filter_smart_money()
    else:
        print("대장주 후보를 찾지 못해 종료합니다.")
