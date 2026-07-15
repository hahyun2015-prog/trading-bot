import os
import sys
import sqlite3
import time
import requests
from bs4 import BeautifulSoup

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

# PyQt5 플러그인 경로 에러 방지
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop
from PyQt5.QtTest import QTest

try:
    import notifier
except ImportError:
    notifier = None

class ThemeTracker:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        
        self.login_loop = None
        self.tr_loop = None
        
        self.theme_leaders = [] # 크롤링된 대장주 리스트
        self.smart_money_stocks = [] # 외국인/기관 순매수 확인된 최종 종목
        
        self.current_foreign_net = 0
        self.current_inst_net = 0
        
        # DB 경로 지정
        self.db_path = os.path.join(workspace_root, "unified_data.db")
        
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
                    change_tags = r.select("td.col_type2 span")
                    change_text = change_tags[0].text.strip() if change_tags else "0.00%"
                    try:
                        change_val = float(change_text.replace("%", "").replace("+", "").strip())
                        if "-" in change_text:
                            change_val = -abs(change_val)
                    except ValueError:
                        change_val = 0.0
                    themes.append({"name": name, "url": link, "change": change_text, "change_val": change_val})

            # 네이버 페이지의 기본 노출 순서를 그대로 믿지 않고, 등락률 기준으로 명시적 재정렬
            # — 페이지 기본 순서가 "오늘 가장 강한 테마"를 보장하지 않음
            themes.sort(key=lambda t: t["change_val"], reverse=True)
            top_themes = themes[:top_n_themes]
            
            for theme in top_themes:
                print(f" -> 주도 테마 포착: {theme['name']} ({theme['change']})")
                
                tres = requests.get(theme['url'], headers=headers)
                tsoup = BeautifulSoup(tres.content, "html.parser")
                
                stock_rows = tsoup.select("table.type_5 tbody tr")
                exclude_keywords = ["KODEX", "TIGER", "KBSTAR", "KINDEX", "KOSEF", "HANARO", "ARIRANG", "인버스", "레버리지", "선물", "스팩", "ETN"]
                MIN_TRADE_VALUE_MILLION = 10000  # 100억 원

                # 먼저 테마 내 전 종목을 등락률 기준으로 정렬할 후보 목록으로 모은 뒤, 그중 상위
                # top_n_stocks개만 채택 — 페이지 노출 순서가 "오늘 가장 강한 종목" 순서를 보장하지 않음
                theme_candidates = []
                for row in stock_rows:
                    tds = row.select("td")
                    if len(tds) >= 9:
                        name_td = tds[0].select("a")
                        if not name_td:
                            continue
                        stock_name = name_td[0].text.strip()
                        stock_code = name_td[0]["href"].split("code=")[1]

                        if any(kw in stock_name for kw in exclude_keywords):
                            continue

                        try:
                            change_text = tds[4].text.strip()
                            change_val = float(change_text.replace("%", "").replace("+", "").strip())
                            if "-" in change_text:
                                change_val = -abs(change_val)
                        except Exception:
                            change_val = 0.0

                        try:
                            # tds[8]은 거래대금 (백만 원 단위)
                            trade_value_text = tds[8].text.strip().replace(",", "")
                            trade_value_million = int(trade_value_text) if trade_value_text else 0
                        except Exception:
                            trade_value_million = 0

                        if trade_value_million < MIN_TRADE_VALUE_MILLION:
                            print(f" [DROP] {stock_name} -> 거래대금 부족 ({trade_value_million:,}백만 원 / 기준: {MIN_TRADE_VALUE_MILLION:,}백만 원)")
                            continue

                        theme_candidates.append({
                            "name": stock_name, "code": stock_code, "change_val": change_val
                        })

                theme_candidates.sort(key=lambda s: s["change_val"], reverse=True)
                for cand in theme_candidates[:top_n_stocks]:
                    self.theme_leaders.append({
                        "theme": theme['name'],
                        "code": cand["code"],
                        "name": cand["name"]
                    })

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
            
            QTest.qWait(500)
            
            self.current_foreign_net = 0
            self.current_inst_net = 0
            
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "일자", today)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "금액수량구분", "1")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "매매구분", "0")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "단위구분", "1")
            
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "종목별투자자기관별", "opt10059", 0, "1059")
            
            self.tr_loop = QEventLoop()
            self.tr_loop.exec_()
            
            # "쌍끌이" 매수를 표방하므로 둘 다 순매수일 때만 통과 (기존엔 OR라서 한쪽이 대량
            # 순매도해도 다른 쪽이 1주만 순매수하면 통과하는 모순이 있었음)
            if self.current_foreign_net > 0 and self.current_inst_net > 0:
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS top_volume_theme (
                date TEXT,
                code TEXT,
                name TEXT,
                volume TEXT,
                UNIQUE(date, code)
            )
        ''')
        
        import datetime
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # 당일 기존 데이터 삭제
        cursor.execute("DELETE FROM top_volume_theme WHERE date = ?", (today,))
        
        for item in self.smart_money_stocks:
            info = f"[{item['theme']}] 외인:{item['foreign_net']} 기관:{item['inst_net']}"
            cursor.execute('''
                INSERT OR REPLACE INTO top_volume_theme (date, code, name, volume)
                VALUES (?, ?, ?, ?)
            ''', (today, item['code'], item['name'], info))
            
        conn.commit()
        conn.close()
        print(f"\n[DB 저장 완료] 최정예 주도주 {len(self.smart_money_stocks)}종목이 'top_volume_theme' 테이블에 적재되었습니다.")
        
        if self.smart_money_stocks:
            msg = f"🔥 <b>[스마트 머니 주도주 포착]</b>\n총 {len(self.smart_money_stocks)}종목이 선정되었습니다.\n"
            for item in self.smart_money_stocks:
                msg += f"• {item['name']} ({item['theme']})\n"
            if notifier:
                notifier.send_message(msg)
        else:
            if notifier:
                notifier.send_message("⚠️ <b>[테마 포착]</b>\n현재 시장에 수급이 유입되는 대장주가 없습니다.")

if __name__ == "__main__":
    tracker = ThemeTracker()
    # 기존 3x3(최대 9종목)은 AND로 강화된 수급필터를 통과하기엔 후보 풀이 너무 좁아서 5x5(최대 25종목)로 확대
    tracker.crawl_naver_themes(top_n_themes=5, top_n_stocks=5)
    if tracker.theme_leaders:
        tracker.filter_smart_money()
    else:
        print("대장주 후보를 찾지 못해 종료합니다.")
