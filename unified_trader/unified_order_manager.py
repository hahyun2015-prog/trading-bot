import os
import sys
import sqlite3
import time
import json
from datetime import datetime

# PyQt5 환경 변수 에러 방지 (경로 절대 지정)
current_dir = os.path.dirname(os.path.abspath(__file__))
qt_plugin_path = os.path.abspath(os.path.join(current_dir, "..", "ai_trader", "venv32", "Lib", "site-packages", "PyQt5", "Qt5", "plugins"))
os.environ['QT_PLUGIN_PATH'] = qt_plugin_path
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(qt_plugin_path, "platforms")

from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer

sys.path.append(r"..\ai_trader")
try:
    import notifier
except ImportError:
    notifier = None

from theme_tracker import ThemeTracker
from screener import DayScreener
from swing_screener import SwingScreener

class UnifiedOrderManager:
    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)
        self.kiwoom.OnReceiveRealData.connect(self._on_receive_real_data)
        
        self.account_num = ""
        self.portfolio = {} # 코드 -> 포지션 상세정보
        self.pending_orders = {}
        self.system_halted = False
        
        # 자금 관리 (단타 60%, 스윙 40%)
        self.total_balance = 0
        self.initial_balance = 0
        self.daily_realized_loss = 0
        self.budget_day = 0
        self.budget_swing = 0
        
        self.max_day_positions = 5
        self.max_swing_positions = 3
        
        print("[UnifiedOrderManager] 키움증권 서버 로그인 대기 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        
        # 타이머 설정
        self.signal_timer = QTimer()
        self.signal_timer.timeout.connect(self.poll_signals)
        
        self.ma_timer = QTimer()
        self.ma_timer.timeout.connect(self.update_day_ma_data)
        self.ma_timer.start(10000)
        
        self.swing_time_timer = QTimer()
        self.swing_time_timer.timeout.connect(self.check_swing_close_time)
        self.swing_time_timer.start(1000)
        
        self.theme_mapping_dict = {}
        self.theme_mapping_timer = QTimer()
        self.theme_mapping_timer.timeout.connect(self.update_theme_mapping)
        self.theme_mapping_timer.start(60000)

        
        # 키움증권 연결 상태 확인 타이머 (60초 주기)
        self.conn_check_timer = QTimer()
        self.conn_check_timer.timeout.connect(self.check_connection_status)
        self.conn_check_timer.start(60000)
        
        self.pending_5ma_checks = []
        self.was_disconnected = False

    def check_connection_status(self):
        state = self.kiwoom.dynamicCall("GetConnectState()")
        if state == 0:
            if not self.was_disconnected:
                print("🚨 [에러] 키움증권 서버 통신 끊김 감지!")
                if notifier:
                    notifier.send_message("🚨 <b>[주식 봇 통신 끊김]</b>\n키움증권 서버와의 연결이 끊어졌습니다!\n60초 후 자동 재연결을 시도합니다.")
                self.was_disconnected = True
                
                # 자동 재연결 스크립트 실행 후 현재 프로세스는 종료 대기
                import subprocess
                subprocess.Popen("start auto_reconnect.bat", shell=True, cwd=r"c:\\antigravity\\노트븍활용\\unified_trader")
        else:
            if self.was_disconnected:
                print("✅ [복구] 키움증권 서버 통신 정상화.")
                if notifier:
                    notifier.send_message("✅ <b>[주식 봇 통신 복구]</b>\n키움증권 서버와의 연결이 다시 정상화되었습니다.")
                self.was_disconnected = False

    def _on_login(self, err_code):
        if err_code == 0:
            print("[UnifiedOrderManager] 로그인 성공!")
            accounts = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO").split(';')
            accounts = [a for a in accounts if a]
            
            self.account_num = accounts[0]
            for acc in accounts:
                if acc.endswith('11'):
                    self.account_num = acc
                    break
                    
            print(f" => 사용 계좌: {self.account_num}")
            
            # 예수금조회
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.account_num)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "예수금조회", "opw00001", 0, "0201")

            # 계좌평가잔고내역요청 (기존 보유 종목 불러오기)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.account_num)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "계좌평가잔고내역요청", "opw00018", 0, "0202")

            
            # 수집기 인스턴스 초기화 (단일 Kiwoom 객체 공유)
            print("[System] 시세 수집기 내부 모듈 초기화 중...")
            self.theme_tracker = ThemeTracker(kiwoom=self.kiwoom)
            self.day_screener = DayScreener(kiwoom=self.kiwoom)
            self.swing_screener = SwingScreener(kiwoom=self.kiwoom)
            
            # 단타 수집 루프 타이머 (4분 주기)
            self.day_loop_timer = QTimer()
            self.day_loop_timer.timeout.connect(self.run_day_collection_cycle)
            self.day_loop_timer.start(240000) # 4분
            
            # 스윙 수집 루프 타이머 (12분 주기)
            self.swing_loop_timer = QTimer()
            self.swing_loop_timer.timeout.connect(self.run_swing_collection_cycle)
            self.swing_loop_timer.start(720000) # 12분
            
            # 즉시 1회 실행
            QTimer.singleShot(5000, self.run_day_collection_cycle)
            QTimer.singleShot(30000, self.run_swing_collection_cycle)
            QTimer.singleShot(5000, self.update_theme_mapping)
            
        else:
            print(f"[UnifiedOrderManager] 로그인 실패 (에러: {err_code})")

    def update_theme_mapping(self):
        try:
            conn = sqlite3.connect("unified_data.db")
            cursor = conn.cursor()
            import datetime
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            # 만약 테이블이 없으면 생성될 때까지 무시됨
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='theme_mapping'")
            if not cursor.fetchone():
                return
                
            cursor.execute("SELECT theme, leader_code, leader_name, follower_code, follower_name FROM theme_mapping WHERE date = ?", (today,))
            rows = cursor.fetchall()
            for row in rows:
                theme, leader_code, leader_name, follower_code, follower_name = row
                leader_code = str(leader_code).strip().replace('A', '')
                follower_code = str(follower_code).strip().replace('A', '')
                
                if leader_code not in self.theme_mapping_dict:
                    self.theme_mapping_dict[leader_code] = {
                        'theme': theme, 'leader_name': leader_name,
                        'follower_code': follower_code, 'follower_name': follower_name,
                        'triggered': False
                    }
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0104", leader_code, "10;12", "1")
                    print(f"📡 [전략1] 대장주 감시 시작: {leader_name} -> 상한가 시 {follower_name} 매수 대기")
            conn.close()
        except Exception as e:
            pass

    def run_day_collection_cycle(self):
        now = datetime.now()
        time_val = now.hour * 100 + now.minute
        if 850 <= time_val <= 1535:
            print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] [단타 로직] 데이터 갱신 사이클 시작...")
            self.theme_tracker.theme_leaders = []
            self.theme_tracker.smart_money_stocks = []
            self.theme_tracker.crawl_naver_themes(top_n_themes=3, top_n_stocks=3)
            if self.theme_tracker.theme_leaders:
                self.theme_tracker.filter_smart_money()
                
            self.day_screener.run_screening()
            
            import subprocess
            print("[DAY 3/3] 64비트 전략 엔진(BB & RSI) 평가 중...")
            subprocess.Popen([r"..\ai_trader\venv64\Scripts\python.exe", "-u", "strategy_engine.py"])
            self.cleanup_old_signals()
            
    def run_swing_collection_cycle(self):
        now = datetime.now()
        time_val = now.hour * 100 + now.minute
        if 850 <= time_val <= 1535:
            print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] [스윙 로직] 데이터 갱신 사이클 시작...")
            self.swing_screener.candidates = []
            self.swing_screener.smart_money_stocks = []
            self.swing_screener.final_breakout_stocks = []
            self.swing_screener.fetch_top_volume_stocks(limit=30)
            if self.swing_screener.candidates:
                self.swing_screener.check_smart_money()
                if self.swing_screener.smart_money_stocks:
                    self.swing_screener.analyze_daily_chart()
                    self.swing_screener.save_signals()
            self.cleanup_old_signals()

    def cleanup_old_signals(self):
        try:
            conn = sqlite3.connect("unified_data.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE signals SET status = 'EXPIRED' WHERE status = 'PENDING' AND timestamp <= datetime('now', '-10 minutes', 'localtime')")
            conn.commit()
            conn.close()
        except:
            pass

    def _on_receive_msg(self, screen_no, rqname, trcode, msg):
        print(f"[Kiwoom System Msg] {msg}")

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "예수금조회":
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "d+2추정예수금").strip()
            if d2_deposit:
                self.total_balance = int(d2_deposit)
                if self.initial_balance == 0:
                    self.initial_balance = self.total_balance
                    
                # 예산 분배 (60% / 40%)
                self.budget_day = int(self.total_balance * 0.6)
                self.budget_swing = int(self.total_balance * 0.4)
                
            print(f"\n=> [자금 셋업] 총 예수금: {self.total_balance:,}원")
            print(f"   - 단타용(60%): {self.budget_day:,}원 (최대 {self.max_day_positions}종목)")
            print(f"   - 스윙용(40%): {self.budget_swing:,}원 (최대 {self.max_swing_positions}종목)")
            
            self.signal_timer.start(3000)
            
        elif rqname == "계좌평가잔고내역요청":
            rows = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f"\n=> [기존 보유 종목 연동] 총 {rows}개 종목 발견")
            for i in range(rows):
                code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "종목번호").strip()
                code = code.replace("A", "") # 'A005930' -> '005930'
                name = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "종목명").strip()
                qty = int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "보유수량").strip())
                buy_price = int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "매입가").strip())
                current_price = int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip())
                
                if code not in self.portfolio:
                    self.portfolio[code] = {
                        'name': name,
                        'strategy': 'SWING', # 기본값 스윙
                        'buy_price': buy_price,
                        'current_price': current_price,
                        'qty': qty,
                        'max_price': current_price,
                        'open_price': None,
                        'super_trend_mode': False,
                        'ma_10': 0, 'ma_20': 0
                    }
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    print(f"  - {name}({code}) | {qty}주 | 매입가: {buy_price:,}원")
            self.export_status()
            
        elif rqname == "스윙일봉5MA조회":
            code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "종목코드").strip()
            if code in self.portfolio and self.portfolio[code]['strategy'] == 'SWING':
                pos = self.portfolio[code]
                closes = []
                for i in range(5):
                    c = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()
                    if c: closes.append(abs(int(c)))
                
                if len(closes) == 5:
                    ma_5 = sum(closes) / 5
                    current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
                    if current_price == 0: current_price = closes[0]
                    
                    print(f"   => [스윙] {pos['name']} 현재가: {current_price:,} / 5MA: {ma_5:,.1f}")
                    
                    if current_price < ma_5:
                        print(f"   🚨 [스윙 청산] {pos['name']} 종가 5선 하향 이탈! 전량 매도.")
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[Unified_Swing_Sell_5MA]", "0103", self.account_num, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(f"📉 <b>[스윙 익절/청산] {pos['name']}</b>\n• 5일선 이탈로 전량 매도합니다.")
                    else:
                        print(f"   ✅ [스윙 홀딩] {pos['name']} 5MA 지지. 오버나잇 확정.")

    def export_status(self):
        """텔레그램 컨트롤러와 상태 공유를 위한 JSON 익스포트"""
        status_data = {
            "total_balance": self.total_balance,
            "budget_day": self.budget_day,
            "budget_swing": self.budget_swing,
            "daily_realized_loss": self.daily_realized_loss,
            "portfolio": self.portfolio,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(r"c:\antigravity\노트븍활용\telegram_controller\unified_status.json", "w", encoding="utf-8") as f:
                json.dump(status_data, f, ensure_ascii=False, indent=2)
        except:
            pass

    def update_day_ma_data(self):
        """단타 종목들의 실시간 10MA 업데이트"""
        self.export_status()
        day_codes = [c for c, p in self.portfolio.items() if p['strategy'] == 'DAY']
        if not day_codes: return
        
        try:
            conn = sqlite3.connect("unified_data.db")
            cursor = conn.cursor()
            for code in day_codes:
                cursor.execute(f"SELECT close FROM intraday_ohlcv WHERE code = '{code}' ORDER BY date DESC LIMIT 20")
                rows = cursor.fetchall()
                if len(rows) >= 10:
                    closes = [row[0] for row in reversed(rows)]
                    ma_10 = sum(closes[-10:]) / 10
                    prev_ma_10 = self.portfolio[code].get('ma_10', ma_10)
                    self.portfolio[code]['prev_ma_10'] = prev_ma_10
                    self.portfolio[code]['ma_10'] = ma_10
                    self.portfolio[code]['ma_10_is_up'] = ma_10 > prev_ma_10
                    
                    if len(rows) >= 20:
                        self.portfolio[code]['ma_20'] = sum(closes[-20:]) / 20
        except:
            pass
        finally:
            conn.close()

    def check_swing_close_time(self):
        now = datetime.now()
        if now.hour == 15 and now.minute == 14 and now.second == 0:
            print("\n[⏰ 종가 익절 감시] 15:14 스윙 종목 5MA 체크를 시작합니다.")
            self.pending_5ma_checks = [c for c, p in self.portfolio.items() if p['strategy'] == 'SWING']
            self._request_next_5ma()

    def _request_next_5ma(self):
        if not self.pending_5ma_checks: return
        code = self.pending_5ma_checks.pop(0)
        today = datetime.now().strftime("%Y%m%d")
        
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "기준일자", today)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "스윙일봉5MA조회", "opt10081", 0, "1082")
        
        if self.pending_5ma_checks:
            QTimer.singleShot(1000, self._request_next_5ma)

    def poll_signals(self):
        if self.system_halted: return
        
        conn = sqlite3.connect("unified_data.db")
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT id, code, name, strategy_type, price, open_price FROM signals WHERE status = 'PENDING' LIMIT 3")
            rows = cursor.fetchall()
            
            for row in rows:
                signal_id, code, name, strategy_type, price, open_price = row
                
                print(f"\n[🚨 신규 주문 포착] {name}({code}) - 전략: {strategy_type}")
                
                # 중복 진입 검사 (단타가 이미 샀는데 스윙이 사려거나, 그 반대 방지)
                if code in self.portfolio or code in self.pending_orders:
                    print(f" => [거절] 이미 다른 전략에 의해 보유/주문 중인 종목입니다.")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_DUPLICATE' WHERE id = ?", (signal_id,))
                    continue

                if strategy_type == 'DAY':
                    day_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] == 'DAY'])
                    if day_pos_count >= self.max_day_positions:
                        print(" => [거절] 단타 최대 보유 슬롯 가득 참.")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                        continue
                    budget_per_stock = self.budget_day // self.max_day_positions
                elif strategy_type == 'SWING':
                    swing_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] == 'SWING'])
                    if swing_pos_count >= self.max_swing_positions:
                        print(" => [거절] 스윙 최대 보유 슬롯 가득 참.")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                        continue
                    budget_per_stock = self.budget_swing // self.max_swing_positions
                elif strategy_type == 'MANUAL_SELL':
                    if code in self.portfolio and not self.portfolio[code].get('sell_ordered'):
                        pos = self.portfolio[code]
                        qty = pos['qty']
                        print(f" => [수동 매도] {name} 시장가 전량 매도 실행 ({qty}주)")
                        pos['sell_ordered'] = True
                        clean_code = str(code).strip().zfill(6)
                        res = self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[Manual_Sell]", "0103", self.account_num, 2, clean_code, qty, 0, "03", ""]
                        )
                        if res == 0:
                            cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                        else:
                            cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                            pos['sell_ordered'] = False
                    else:
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_NOT_OWNED' WHERE id = ?", (signal_id,))
                    continue
                else:
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_UNKNOWN' WHERE id = ?", (signal_id,))
                    continue
                    
                safe_price = price * 1.3 # 시장가 증거금 보수적 산정
                qty = int(budget_per_stock // safe_price)
                
                if qty <= 0:
                    print(" => [거절] 예수금 부족")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_NO_FUNDS' WHERE id = ?", (signal_id,))
                    continue
                    
                print(f" => [{strategy_type}] 할당 예산: {budget_per_stock:,}원 / 수량: {qty}주")
                
                self.pending_orders[code] = {'qty': qty, 'price': price, 'type': 'BUY', 'strategy': strategy_type, 'open_price': open_price}
                
                clean_code = str(code).strip().zfill(6)
                res = self.kiwoom.dynamicCall(
                    "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                    ["[Unified_Buy]", "0101", self.account_num, 1, clean_code, qty, 0, "03", ""]
                )
                
                if res == 0:
                    cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                else:
                    cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                    del self.pending_orders[code]
                    
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def _on_receive_chejan_data(self, gubun, item_cnt, fid_list):
        if gubun == "0":
            status = self.kiwoom.dynamicCall("GetChejanData(int)", 913).strip()
            name = self.kiwoom.dynamicCall("GetChejanData(int)", 302).strip()
            code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().replace("A", "")
            
            if status == "체결":
                exec_price = int(self.kiwoom.dynamicCall("GetChejanData(int)", 910).strip())
                exec_qty = int(self.kiwoom.dynamicCall("GetChejanData(int)", 911).strip())
                order_gubun = self.kiwoom.dynamicCall("GetChejanData(int)", 905).strip()
                
                print(f"[체결 확정] {name}({code}) | {exec_price:,}원 | {exec_qty}주 | {order_gubun}")
                
                if "매수" in order_gubun:
                    if code not in self.portfolio:
                        pending = self.pending_orders.get(code, {})
                        strat = pending.get('strategy', 'DAY')
                        open_p = pending.get('open_price', exec_price)
                        
                        self.portfolio[code] = {
                            'name': name, 'strategy': strat, 'buy_price': exec_price, 'qty': 0, 
                            'max_price': exec_price, 'open_price': open_p,
                            'super_trend_mode': False, 'ma_10': 0, 'ma_20': 0
                        }
                        
                    self.portfolio[code]['qty'] += exec_qty
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    
                    if notifier:
                        strat_name = "단타" if self.portfolio[code]['strategy'] in ['DAY', 'DAY_LEADER_FOLLOW'] else "스윙"
                        notifier.send_message(f"💰 <b>[{strat_name} 매수 체결] {name}</b>\n• 체결가: {exec_price:,}원\n• 수량: {exec_qty}주")
                        
                elif "매도" in order_gubun:
                    if code in self.portfolio:
                        pos = self.portfolio[code]
                        strat = pos['strategy']
                        pos['qty'] -= exec_qty
                        
                        profit = (exec_price - pos['buy_price']) * exec_qty
                        profit_pct = ((exec_price - pos['buy_price']) / pos['buy_price']) * 100
                        
                        # [실시간 복리 적용] 실현 손익을 총 자본금에 더하고 예산을 재분배합니다.
                        self.total_balance += profit
                        self.budget_day = int(self.total_balance * 0.6)
                        self.budget_swing = int(self.total_balance * 0.4)
                        
                        if profit < 0:
                            self.daily_realized_loss += abs(profit)
                            icon = "✂️"
                        else:
                            icon = "🚀"
                            
                        if notifier:
                            strat_name = "단타" if strat == 'DAY' else "스윙"
                            notifier.send_message(f"{icon} <b>[{strat_name} 매도 완료] {name}</b>\n• 단가: {exec_price:,}원\n• 손익률: {profit_pct:+.2f}%\n• 실현손익: {profit:,}원\n🔄 현재 총 자본금: {self.total_balance:,}원 (실시간 복리 갱신)")
                            
                        if pos['qty'] <= 0:
                            del self.portfolio[code]
                            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "0102", code)

    def _on_receive_real_data(self, code, real_type, real_data):
        if real_type == "주식체결":
            current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
            
            # --- 전략 1: 테마 대장주 상한가 감지 (2등주 매수) ---
            if code in self.theme_mapping_dict:
                mapping = self.theme_mapping_dict[code]
                if not mapping['triggered']:
                    change_rate_str = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 12).strip()
                    if change_rate_str:
                        change_rate = float(change_rate_str)
                        if change_rate >= 29.5:
                            mapping['triggered'] = True
                            follower_code = mapping['follower_code']
                            follower_name = mapping['follower_name']
                            
                            print(f"\n🚀 [전략 1 발동!] 대장주 {mapping['leader_name']} 상한가 도달!")
                            print(f" => 2등주 {follower_name}({follower_code}) 시장가 추격 매수 시작!")
                            
                            if follower_code not in self.portfolio and follower_code not in self.pending_orders:
                                day_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] in ['DAY', 'DAY_LEADER_FOLLOW']])
                                if day_pos_count < self.max_day_positions:
                                    budget_per_stock = self.budget_day // self.max_day_positions
                                    f_price_str = self.kiwoom.dynamicCall("GetMasterLastPrice(QString)", follower_code).strip()
                                    if f_price_str:
                                        f_price = abs(int(f_price_str))
                                        safe_price = f_price * 1.30 # 상한가 기준 보수적 증거금 계산
                                        qty = int(budget_per_stock // safe_price)
                                        if qty > 0:
                                            self.pending_orders[follower_code] = {'qty': qty, 'price': f_price, 'type': 'BUY', 'strategy': 'DAY_LEADER_FOLLOW', 'open_price': f_price}
                                            res = self.kiwoom.dynamicCall(
                                                "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                                                ["[ThemeFollower_Buy]", "0105", self.account_num, 1, follower_code, qty, 0, "03", ""]
                                            )
                                            if res == 0:
                                                if notifier:
                                                    notifier.send_message(f"🚀 <b>[전략1: 2등주 매수]</b>\n대장주 {mapping['leader_name']} 상한가 진입 감지!\n• {follower_name} 시장가 매수 주문 ({qty}주)")
                                            else:
                                                del self.pending_orders[follower_code]
            
            if code in self.portfolio:
                pos = self.portfolio[code]
                pos['current_price'] = current_price
                buy_price = pos['buy_price']
                profit_ratio = (current_price - buy_price) / buy_price
                strat = pos['strategy']
                
                sell_reason = None
                
                # --- 단타 로직 ---
                if strat in ['DAY', 'DAY_LEADER_FOLLOW']:
                    ma_10 = pos.get('ma_10', 0)
                    ma_20 = pos.get('ma_20', 0)
                    ma_10_is_up = pos.get('ma_10_is_up', False)
                    super_trend_mode = pos.get('super_trend_mode', False)
                    
                    if profit_ratio <= -0.02:
                        sell_reason = "고정 손절선(-2%) 도달"
                    else:
                        if super_trend_mode:
                            if current_price < ma_20 and ma_20 > 0:
                                sell_reason = "20MA 하향 돌파 (수익 극대화 종료)"
                            elif profit_ratio <= 0.015:
                                sell_reason = "+1.5% 최소 수익 보장선 이탈"
                        else:
                            if profit_ratio >= 0.03:
                                if ma_10 > 0 and ma_10_is_up and current_price >= ma_10:
                                    if not super_trend_mode:
                                        print(f"🌟 [{pos['name']}] 단타 수익 극대화 모드 진입!")
                                        pos['super_trend_mode'] = True
                                else:
                                    sell_reason = "+3% 목표가 도달 (추세 꺾임)"
                                    
                # --- 스윙 로직 ---
                elif strat == 'SWING':
                    # 장대양봉 시가 이탈 시 즉시 기계적 손절 (하드 스탑)
                    if pos['open_price'] and current_price < pos['open_price']:
                        sell_reason = f"기준봉 시가({pos['open_price']:,}원) 하향 이탈 (하드스탑)"
                        
                if sell_reason and not pos.get('sell_ordered'):
                    print(f"\n[🛡️ 자동 청산 발동] {pos['name']} - {sell_reason}")
                    pos['sell_ordered'] = True
                    self.kiwoom.dynamicCall(
                        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                        ["[Unified_Sell]", "0103", self.account_num, 2, code, pos['qty'], 0, "03", ""]
                    )
                    # 실제 삭제는 체결(_on_receive_chejan_data)에서 진행되도록 변경 (중복 주문은 sell_ordered로 방지)

if __name__ == "__main__":
    try:
        import ctypes
        # ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED = 0x80000003
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        print("[System] 윈도우 절전 모드 및 화면보호기 진입 방지 활성화 완료.")
    except Exception as e:
        print(f"[System] 절전 방지 실패: {e}")

    print("===================================================")
    print("  Unified Order Manager (Day 60% & Swing 40%)")
    print("===================================================")
    app = QApplication(sys.argv)
    manager = UnifiedOrderManager()
    sys.exit(app.exec_())
