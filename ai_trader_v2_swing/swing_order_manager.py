import os
import sys
import sqlite3
import time
from datetime import datetime

if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer, QTime
import notifier

class SwingOrderManager:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        self.kiwoom.OnReceiveRealData.connect(self._on_receive_real_data)
        
        self.account_num = ""
        self.portfolio = {} # { '005930': {'name': '삼성전자', 'buy_price': 50000, 'qty': 10, 'open_price': 49000, '5ma_checked_today': False} }
        self.pending_orders = {}
        
        self.available_balance = 0
        self.max_positions = 3 # 최대 3종목 스윙
        
        print("[SwingOrderManager] 키움증권 서버 로그인 대기 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        
        # 3초마다 DB 큐(Queue) 폴링 타이머 (신규 진입 감시)
        self.db_timer = QTimer()
        self.db_timer.timeout.connect(self.poll_signals)
        
        # 1초마다 15:15 타임 타이머 (종가 무렵 5MA 익절 감시)
        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self.check_daily_close_time)
        self.time_timer.start(1000)
        
        self.pending_5ma_checks = []
        
    def _on_login(self, err_code):
        if err_code == 0:
            print("[SwingOrderManager] 로그인 성공!")
            accounts = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO").split(';')
            accounts = [a for a in accounts if a]
            
            # 두 번째 계좌(모의투자) 사용 가정
            if len(accounts) > 1:
                self.account_num = accounts[1]
            else:
                self.account_num = accounts[0]
                
            print(f" => 사용 계좌: {self.account_num}")
            
            # 예수금 조회
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.account_num)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "예수금조회", "opw00001", 0, "0201")
        else:
            print(f"[SwingOrderManager] 로그인 실패 (에러코드: {err_code})")

    def poll_signals(self):
        conn = sqlite3.connect("swing_data.db")
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT id, code, name, signal_type, price, open_price FROM signals WHERE status = 'PENDING' LIMIT 3")
            rows = cursor.fetchall()
            
            for row in rows:
                signal_id, code, name, signal_type, price, open_price = row
                
                print(f"\n[🚨 종가 베팅 주문 포착!] {name}({code})")
                
                if len(self.portfolio) >= self.max_positions:
                    print(f" => [거절] 최대 보유 종목 수({self.max_positions}개) 초과.")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                    continue
                    
                budget_per_stock = self.available_balance // self.max_positions
                qty = int(budget_per_stock // price)
                
                if qty <= 0:
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_NO_FUNDS' WHERE id = ?", (signal_id,))
                    continue
                    
                print(f" => [자금 관리] 할당 예산: {budget_per_stock:,}원 / 수량: {qty}주")
                
                self.available_balance -= (qty * price)
                self.pending_orders[code] = {'open_price': open_price}
                
                # 시장가 매수 전송
                res = self.kiwoom.dynamicCall(
                    "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                    ["[AI_Swing_Buy]", "0101", self.account_num, 1, code, qty, 0, "03", ""]
                )
                
                if res == 0:
                    cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                else:
                    cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                    
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def check_daily_close_time(self):
        """오후 3시 14분이 되면, 보유 종목의 5일 이평선(5MA)을 조회합니다."""
        now = datetime.now()
        
        # 15:14:00 에 딱 한 번 실행
        if now.hour == 15 and now.minute == 14 and now.second == 0:
            print("\n[⏰ 종가 익절 감시 타임] 오후 3시 14분입니다. 보유 종목 5MA 체크를 시작합니다.")
            
            self.pending_5ma_checks = list(self.portfolio.keys())
            self._request_next_5ma()
            
    def _request_next_5ma(self):
        if not self.pending_5ma_checks:
            return
            
        code = self.pending_5ma_checks.pop(0)
        today = datetime.now().strftime("%Y%m%d")
        
        print(f" -> [{self.portfolio[code]['name']}] 일봉 5MA 조회 요청 중...")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "기준일자", today)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "일봉5MA조회", "opt10081", 0, "1082")
        
        # 다음 종목 조회는 1초 뒤에 (TR 제한 방지)
        if self.pending_5ma_checks:
            QTimer.singleShot(1000, self._request_next_5ma)

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "예수금조회":
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "d+2추정예수금").strip()
            if d2_deposit:
                self.available_balance = int(d2_deposit)
            print(f" => 스윙 주문 가능 예수금: {self.available_balance:,}원")
            self.db_timer.start(3000)
            
        elif rqname == "일봉5MA조회":
            code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "종목코드").strip()
            if code in self.portfolio:
                pos = self.portfolio[code]
                closes = []
                for i in range(5):
                    c = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()
                    if c: closes.append(abs(int(c)))
                
                if len(closes) == 5:
                    ma_5 = sum(closes) / 5
                    current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
                    if current_price == 0: current_price = closes[0] # 실시간 없으면 TR 당일 종가 사용
                    
                    print(f"   => {pos['name']} 현재가: {current_price:,} / 5MA: {ma_5:,.1f}")
                    
                    # 5MA 이탈 시 전량 시장가 매도 (익절/청산)
                    if current_price < ma_5:
                        print(f"   🚨 [5MA 이탈 청산] {pos['name']} 종가 무렵 5선 하향 이탈! 전량 매도 전송.")
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[AI_Swing_Sell_5MA]", "0103", self.account_num, 2, code, pos['qty'], 0, "03", ""]
                        )
                        notifier.send_message(f"📉 <b>[스윙 익절/청산] {pos['name']}</b>\n• 5일선 이탈로 전량 매도합니다.")
                    else:
                        print(f"   ✅ [홀딩] {pos['name']} 5MA 위에서 추세 유지 중. 내일로 넘어갑니다(오버나잇).")

    def _on_receive_chejan_data(self, gubun, item_cnt, fid_list):
        if gubun == "0":
            order_no = self.kiwoom.dynamicCall("GetChejanData(int)", 9203).strip()
            status = self.kiwoom.dynamicCall("GetChejanData(int)", 913).strip()
            name = self.kiwoom.dynamicCall("GetChejanData(int)", 302).strip()
            code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().replace("A", "")
            
            if status == "체결":
                exec_price = int(self.kiwoom.dynamicCall("GetChejanData(int)", 910).strip())
                exec_qty = int(self.kiwoom.dynamicCall("GetChejanData(int)", 911).strip())
                order_gubun = self.kiwoom.dynamicCall("GetChejanData(int)", 905).strip()
                
                if "매수" in order_gubun:
                    if code not in self.portfolio:
                        open_p = self.pending_orders.get(code, {}).get('open_price', exec_price)
                        self.portfolio[code] = {'name': name, 'buy_price': exec_price, 'qty': 0, 'open_price': open_p}
                        
                    self.portfolio[code]['qty'] += exec_qty
                    
                    # 실시간 감시 등록
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0202", code, "10", "1")
                    print(f" => [스윙 진입] {name} 매수 완료. (손절가/장대양봉 시가: {self.portfolio[code]['open_price']:,}원)")
                    notifier.send_message(f"🎯 <b>[스윙 베팅 완료] {name}</b>\n• 체결가: {exec_price:,}원\n• 손절가(시가): {self.portfolio[code]['open_price']:,}원")
                    
                elif "매도" in order_gubun:
                    if code in self.portfolio:
                        self.portfolio[code]['qty'] -= exec_qty
                        self.available_balance += (exec_price * exec_qty)
                        
                        if self.portfolio[code]['qty'] <= 0:
                            del self.portfolio[code]
                            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "0202", code)
                            
                        notifier.send_message(f"🚀 <b>[스윙 청산 완료] {name}</b>\n• 매도단가: {exec_price:,}원")

    def _on_receive_real_data(self, code, real_type, real_data):
        """실시간 장중 손절 감시 (시가 이탈)"""
        if real_type == "주식체결":
            current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
            
            if code in self.portfolio:
                pos = self.portfolio[code]
                
                # 장중 언제라도 진입 당시 장대양봉의 '시가(open_price)'를 깨면 즉각 손절
                if current_price < pos['open_price']:
                    print(f"\n[🛡️ 하드 스탑 발동!] {pos['name']} - 장대양봉 시가({pos['open_price']:,}원) 이탈! 전량 매도 🚀")
                    
                    res = self.kiwoom.dynamicCall(
                        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                        ["[AI_Swing_HardStop]", "0103", self.account_num, 2, code, pos['qty'], 0, "03", ""]
                    )
                    
                    if res == 0:
                        notifier.send_message(f"✂️ <b>[스윙 기계적 손절] {pos['name']}</b>\n• 시가 이탈로 전량 손절합니다.")
                        # 체결 전 중복 주문 방지를 위해 포트폴리오에서 임시 제거
                        pos['open_price'] = 0 
                        self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "0202", code)

if __name__ == "__main__":
    manager = SwingOrderManager()
    manager.app.exec_()
