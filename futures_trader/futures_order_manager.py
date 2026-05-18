import os
import sys
import sqlite3
import time
import json
from datetime import datetime

sys.path.append(r"..\ai_trader")
try:
    import notifier
except ImportError:
    notifier = None

# PyQt5 환경 변수 에러 방지 (경로 절대 지정)
current_dir = os.path.dirname(os.path.abspath(__file__))
qt_plugin_path = os.path.abspath(os.path.join(current_dir, "..", "ai_trader", "venv32", "Lib", "site-packages", "PyQt5", "Qt5", "plugins"))
os.environ['QT_PLUGIN_PATH'] = qt_plugin_path
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(qt_plugin_path, "platforms")

from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer

class FuturesOrderManager:
    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        
        # 이벤트 연결
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)
        self.kiwoom.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        
        self.account_num = ""
        self.available_balance = 0 # 주문가능 현금 (선물옵션 예수금)
        self.margin_cap_ratio = 0.3 # 30% 캡(Cap) 적용 (3배 마진 버퍼)
        
        self.positions = {} # 현재 포지션 정보 기록 (코드: {종류(Long/Short), 단가, 수량})
        self.system_halted = False
        
        print("==========================================================")
        print("  국내선물 전용 실시간 주문 모듈 (Order Manager) 시작")
        print("==========================================================")
        print("[System] 키움증권 서버 로그인 대기 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        
        # DB 폴링 타이머 (signals 테이블 감시)
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll_signals)
        
        # 상태 익스포트 타이머 (10초)
        self.export_timer = QTimer()
        self.export_timer.timeout.connect(self.export_status)
        self.export_timer.start(10000)
        
        # 데이터 수집 타이머 (5분)
        self.data_timer = QTimer()
        self.data_timer.timeout.connect(self.request_futures_data)

    def export_status(self):
        """텔레그램 컨트롤러와 상태 공유를 위한 JSON 익스포트"""
        status_data = {
            "available_balance": self.available_balance,
            "positions": self.positions,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(r"c:\antigravity\노트븍활용\telegram_controller\futures_status.json", "w", encoding="utf-8") as f:
                json.dump(status_data, f, ensure_ascii=False, indent=2)
        except:
            pass
        
        # 키움증권 연결 상태 확인 타이머 (60초 주기)
        self.conn_check_timer = QTimer()
        self.conn_check_timer.timeout.connect(self.check_connection_status)
        self.conn_check_timer.start(60000)
        
        self.was_disconnected = False

    def check_connection_status(self):
        state = self.kiwoom.dynamicCall("GetConnectState()")
        if state == 0:
            if not self.was_disconnected:
                print("🚨 [에러] 키움증권 서버 통신 끊김 감지!")
                if notifier:
                    notifier.send_message("🚨 <b>[선물 봇 통신 끊김]</b>\n키움증권 서버와의 연결이 끊어졌습니다!\n60초 후 자동 재연결을 시도합니다.")
                self.was_disconnected = True
                
                # 자동 재연결 스크립트 실행 후 현재 프로세스는 종료 대기
                import subprocess
                subprocess.Popen("start auto_reconnect.bat", shell=True, cwd=r"c:\\antigravity\\노트븍활용\\futures_trader")
        else:
            if self.was_disconnected:
                print("✅ [복구] 키움증권 서버 통신 정상화.")
                if notifier:
                    notifier.send_message("✅ <b>[선물 봇 통신 복구]</b>\n키움증권 서버와의 연결이 다시 정상화되었습니다.")
                self.was_disconnected = False

    def request_futures_data(self):
        day_code = "10100000"
        night_code = "10500000" # 사용자 환경에 맞는 야간선물 코드로 변경 가능
        now = time.localtime()
        if now.tm_hour >= 17 or now.tm_hour < 6:
            self.api_req_code = night_code
            print(f"\n[Data] 야간장 5분봉 시세 수집 요청 (코드: {self.api_req_code})")
        else:
            self.api_req_code = day_code
            print(f"\n[Data] 주간장 5분봉 시세 수집 요청 (코드: {self.api_req_code})")
            
        self.db_save_code = self.api_req_code
        
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", self.api_req_code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", "5")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선물분차트조회", "opt50029", 0, "5029")
        
    def _on_login(self, err_code):
        if err_code == 0:
            print("[System] 로그인 성공!")
            
            # --- 최근월물 코드 동적 추출 (주문용 진짜 코드) ---
            future_list = self.kiwoom.dynamicCall("GetFutureList()").strip()
            self.real_day_code = "10100000"
            self.real_night_code = "10500000"
            if future_list:
                codes = [c for c in future_list.split(";") if c and c.startswith("101")]
                if codes:
                    self.real_day_code = codes[0] # 예: 101V6000
                    self.real_night_code = "105" + self.real_day_code[3:]
                    print(f" => [System] 최근월물 자동 인식 완료: 주간({self.real_day_code}), 야간({self.real_night_code})")
            
            # 계좌번호 추출
            accounts_str = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            accounts = [a for a in accounts_str.split(';') if a]
            
            print(f" => 전체 보유 계좌: {accounts}")
            
            # 파생상품 계좌 자동 선택 (보통 11(주식), 10, 31 외의 번호. 파생은 8 또는 5로 시작하는 경우가 많음)
            # 여기서는 편의상 주식계좌(11)가 아닌 첫 번째 계좌를 파생계좌로 가정합니다.
            self.account_num = accounts[0]
            for acc in accounts:
                if not acc.endswith('11'): # 주식 계좌가 아닌 것을 선물 계좌로 우선 설정
                    self.account_num = acc
                    break
                    
            print(f" => 사용 계좌 (선물전용): {self.account_num}")
            
            # 예수금 조회 (opw20010: 선옵예탁금조회)
            print("[System] 선물 계좌의 주문 가능 예수금을 조회합니다...")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.account_num)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "0000")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            
            # 파생상품 예수금 조회 TR (opw20010 사용)
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "예수금조회", "opw20010", 0, "2001")
            
        else:
            print(f"[System] 로그인 실패 (에러코드: {err_code})")
            
    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "예수금조회":
            # 선물 범용 예수금 조회(opw20010)에서 주문가능현금 획득
            available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "주문가능현금").strip()
            
            # 값이 없으면 다른 필드 탐색 (예: 추정예탁총액, 예탁금 등)
            if not available_cash or int(available_cash) == 0:
                available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "예탁금").strip()
                
            if available_cash:
                self.available_balance = int(available_cash)
            
            print(f" => [자금 관리] 현재 예수금: {self.available_balance:,} 원")
            
            safe_budget = int(self.available_balance * self.margin_cap_ratio)
            print(f" => [리스크 관리] 30% 캡(Cap) 적용 안전 가용 예산: {safe_budget:,} 원")
            
            # 폴링 타이머 시작 (3초 간격)
            print("\n[System] DB(futures_data.db) 시그널 모니터링을 시작합니다...")
            self.timer.start(3000)
            
            # 데이터 수집 즉시 1회 실행 후 타이머 시작
            print("[System] 5분봉 데이터 수집 타이머를 시작합니다...")
            self.request_futures_data()
            self.data_timer.start(300000) # 5분(300,000ms)

        elif rqname == "선물분차트조회":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f" => [Data] 수신된 5분봉 데이터 개수: {cnt}개")
            
            try:
                conn = sqlite3.connect("futures_data.db")
                cursor = conn.cursor()
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
                        ''', (getattr(self, 'db_save_code', "10100000"), date, open_p, high_p, low_p, close_p, vol))
                        inserted += 1
                    except sqlite3.IntegrityError:
                        pass # 중복 스킵
                conn.commit()
                conn.close()
                print(f" => [Data] DB 저장 완료 ({inserted}개 신규 캔들 업데이트)")
            except Exception as e:
                print(f" => [Data Error] DB 저장 중 에러: {e}")

    def _on_receive_msg(self, screen_no, rqname, trcode, msg):
        print(f"[Kiwoom Msg] {msg}")

    def _on_receive_chejan_data(self, gubun, item_cnt, fid_list):
        # 파생상품 체결 잔고 수신
        if gubun == "0": # 체결
            name = self.kiwoom.dynamicCall("GetChejanData(int)", 302).strip()
            status = self.kiwoom.dynamicCall("GetChejanData(int)", 913).strip()
            
            if status == "체결":
                exec_price = float(self.kiwoom.dynamicCall("GetChejanData(int)", 910).strip())
                exec_qty = int(self.kiwoom.dynamicCall("GetChejanData(int)", 911).strip())
                order_gubun = self.kiwoom.dynamicCall("GetChejanData(int)", 905).strip() # +매수, -매도
                
                print(f"[체결 확정] {name} | 가격: {exec_price} | 수량: {exec_qty} | 구분: {order_gubun}")
                
                if "매수" in order_gubun:
                    if "KOSPI200" not in self.positions:
                        self.positions["KOSPI200"] = {'type': 'LONG', 'qty': exec_qty, 'price': exec_price}
                    else:
                        if self.positions["KOSPI200"]['type'] == 'SHORT':
                            self.positions["KOSPI200"]['qty'] -= exec_qty
                            if self.positions["KOSPI200"]['qty'] <= 0:
                                del self.positions["KOSPI200"]
                        else:
                            self.positions["KOSPI200"]['qty'] += exec_qty
                            # 평단가 업데이트 가능 (여기서는 단순화)
                            
                    if notifier:
                        notifier.send_message(f"💰 <b>[선물 체결 알림] 코스피200</b>\n• 구분: 매수(롱진입/숏청산)\n• 체결가: {exec_price:,.2f}pt\n• 수량: {exec_qty}계약")
                        
                elif "매도" in order_gubun:
                    if "KOSPI200" not in self.positions:
                        self.positions["KOSPI200"] = {'type': 'SHORT', 'qty': exec_qty, 'price': exec_price}
                    else:
                        if self.positions["KOSPI200"]['type'] == 'LONG':
                            self.positions["KOSPI200"]['qty'] -= exec_qty
                            if self.positions["KOSPI200"]['qty'] <= 0:
                                del self.positions["KOSPI200"]
                        else:
                            self.positions["KOSPI200"]['qty'] += exec_qty
                            
                    if notifier:
                        notifier.send_message(f"💰 <b>[선물 체결 알림] 코스피200</b>\n• 구분: 매도(숏진입/롱청산)\n• 체결가: {exec_price:,.2f}pt\n• 수량: {exec_qty}계약")

    def poll_signals(self):
        if self.system_halted:
            return
            
        try:
            conn = sqlite3.connect("futures_data.db")
            cursor = conn.cursor()
            
            # PENDING 상태의 시그널 조회
            cursor.execute("SELECT id, code, signal_type, price FROM signals WHERE status = 'PENDING' LIMIT 1")
            row = cursor.fetchone()
            
            if row:
                signal_id, code, signal_type, price = row
                print(f"\n[🚨 신규 주문 포착] 종목코드: {code} | 시그널: {signal_type} | 현재가: {price}")
                
                # ------ 동적 포지션 사이징 로직 ------
                # 1계약 위탁증거금 계산 (승수 25만원, 증거금률 약 10% 가정)
                # 실제 증거금률은 거래소 고시이나, 보수적으로 10% 잡고 계산
                margin_per_contract = price * 250000 * 0.10
                safe_budget = self.available_balance * self.margin_cap_ratio
                
                qty = int(safe_budget // margin_per_contract)
                
                print(f"  => 1계약 추정 증거금: {margin_per_contract:,.0f} 원")
                print(f"  => 산출된 진입 계약 수: {qty} 계약")
                
                if qty <= 0:
                    print("  => [거절] 예수금 버퍼 부족으로 진입을 포기합니다.")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_NO_FUNDS' WHERE id = ?", (signal_id,))
                else:
                    # SendOrderFO 파라미터 매핑
                    # lOrdKind(주문종류): 1:신규, 2:청산
                    # sSlbyTp(매매구분): 1:매도, 2:매수
                    
                    if signal_type == "LONG_ENTER":
                        ord_kind, slby_tp = 1, 2 # 신규, 매수
                    elif signal_type == "SHORT_ENTER":
                        ord_kind, slby_tp = 1, 1 # 신규, 매도
                    elif signal_type == "LONG_EXIT":
                        ord_kind, slby_tp = 2, 1 # 청산, 매도 (전매도)
                    elif signal_type == "SHORT_EXIT":
                        ord_kind, slby_tp = 2, 2 # 청산, 매수 (환매수)
                    else:
                        print(f"  => 알 수 없는 시그널 타입({signal_type}). 무시합니다.")
                        cursor.execute("UPDATE signals SET status = 'ERROR' WHERE id = ?", (signal_id,))
                        conn.commit()
                        conn.close()
                        return
                        
                    # 실제 주문을 위한 고유 종목코드로 변환 (에러 방지)
                    order_code = code
                    if code == "10100000":
                        order_code = getattr(self, 'real_day_code', "10100000")
                    elif code == "10500000":
                        order_code = getattr(self, 'real_night_code', "10500000")
                        
                    print(f"  => [주문 전송] SendOrderFO 전송 (유형:{ord_kind}, 매매구분:{slby_tp}, 수량:{qty}, 코드:{order_code})")
                    
                    # SendOrderFO(주문명, 화면번호, 계좌, 코드, 주문종류, 매매구분, 거래구분(3:시장가), 수량, 가격(0), 원주문)
                    res = self.kiwoom.dynamicCall(
                        "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
                        ["FuturesOrder", "0101", self.account_num, order_code, ord_kind, str(slby_tp), "3", qty, "0", ""]
                    )
                    
                    if res == 0:
                        print("  => 성공적으로 키움 서버로 전송되었습니다.")
                        cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                    else:
                        print(f"  => 전송 실패 (에러코드: {res})")
                        cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                        
            conn.commit()
            conn.close()
            
        except sqlite3.OperationalError:
            # signals 테이블이 아직 없으면 무시
            pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    manager = FuturesOrderManager()
    sys.exit(app.exec_())
