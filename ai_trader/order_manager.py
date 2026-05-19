import os
import sys
import sqlite3
import json

def _load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"environment": "mock"}

_CONFIG = _load_config()
IS_LIVE = _CONFIG.get("environment") == "live"

# PyQt5 환경 변수 에러 방지 (항상 PyQt5 임포트 전에 실행되어야 함)
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer
import notifier
from datetime import datetime

class OrderManager:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)
        self.kiwoom.OnReceiveRealData.connect(self._on_receive_real_data)
        
        self.account_num = ""
        self.portfolio = {} # { '005930': {'name': '삼성전자', 'buy_price': 50000, 'qty': 10, 'max_price': 52000, 'target_price': 51500, 'stop_price': 49000} }
        self.pending_orders = {}
        self.system_halted = False
        self.daily_realized_loss = 0
        self.available_balance = 0
        self.initial_balance = 0
        self.max_positions = 8
        
        env_label = "실전매매" if IS_LIVE else "모의투자"
        print(f"[OrderManager] 환경: {env_label} (config.json: environment={_CONFIG.get('environment')})")
        # 실전(1) / 모의투자(2) 서버 선택
        server_code = "1" if IS_LIVE else "2"
        self.kiwoom.dynamicCall("KOA_Functions(QString, QString)", "SetServerGBCode", server_code)
        print("[OrderManager] 키움증권 서버 로그인 대기 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        
        # 3초마다 DB 큐(Queue) 폴링 타이머
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll_signals)
        
        # 10초마다 10MA 갱신 타이머
        self.ma_timer = QTimer()
        self.ma_timer.timeout.connect(self.update_ma_data)
        self.ma_timer.start(10000)
        
        # 30분마다 수익 현황 텔레그램 전송 타이머
        self.report_timer = QTimer()
        self.report_timer.timeout.connect(self.send_portfolio_report)
        self.report_timer.start(1800000) # 30분 (1800000ms)

        # 매일 09:00 kill switch 자동 해제 (1분 주기로 시각 감시)
        self.kill_switch_reset_timer = QTimer()
        self.kill_switch_reset_timer.timeout.connect(self._check_daily_reset)
        self.kill_switch_reset_timer.start(60000)
        
    def update_ma_data(self):
        if not self.portfolio:
            return
            
        conn = sqlite3.connect("kiwoom_data.db")
        cursor = conn.cursor()
        
        try:
            for code in list(self.portfolio.keys()):
                cursor.execute("SELECT close FROM intraday_ohlcv WHERE code = ? ORDER BY date DESC LIMIT 20", (code,))
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
        except Exception as e:
            print(f"[MA Update Error] {e}")
        finally:
            conn.close()
        
    def send_portfolio_report(self):
        if not self.portfolio:
            return
            
        import unicodedata
        
        def get_display_width(s):
            return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(s))
            
        def pad_string(s, width, align='left'):
            s_str = str(s)
            display_width = get_display_width(s_str)
            padding = max(0, width - display_width)
            if align == 'left':
                return s_str + ' ' * padding
            else:
                return ' ' * padding + s_str

        msg = "📊 <b>[보유 종목 수익 현황]</b>\n<pre>\n"
        msg += f"{pad_string('종목명', 14)} {pad_string('수익률', 9, 'right')} {pad_string('손익금', 11, 'right')}\n"
        msg += "─" * 36 + "\n"
        
        total_buy = 0
        total_eval = 0
        
        for code, pos in self.portfolio.items():
            name = pos['name']
            buy_price = pos['buy_price']
            qty = pos['qty']
            curr_price = pos.get('current_price', buy_price)
            
            buy_amt = buy_price * qty
            eval_amt = curr_price * qty
            profit = eval_amt - buy_amt
            if buy_price > 0:
                profit_pct = (curr_price - buy_price) / buy_price * 100
            else:
                profit_pct = 0
                
            total_buy += buy_amt
            total_eval += eval_amt
            
            # Formatting
            short_name = name[:7] # 이름이 너무 길면 자름
            pct_str = f"{profit_pct:+.2f}%"
            profit_str = f"{profit:,}"
            
            msg += f"{pad_string(short_name, 14)} {pad_string(pct_str, 9, 'right')} {pad_string(profit_str, 11, 'right')}\n"
            
        total_profit = total_eval - total_buy
        total_profit_pct = (total_profit / total_buy * 100) if total_buy > 0 else 0
        
        msg += "─" * 36 + "\n"
        msg += f"{pad_string('총 합산', 14)} {pad_string(f'{total_profit_pct:+.2f}%', 9, 'right')} {pad_string(f'{total_profit:,}', 11, 'right')}\n"
        msg += "</pre>"
        
        import notifier
        notifier.send_message(msg)

    def _check_daily_reset(self):
        now = datetime.now()
        if now.hour == 9 and now.minute == 0:
            if self.system_halted or self.daily_realized_loss > 0:
                self.system_halted = False
                self.daily_realized_loss = 0
                print("[Kill Switch 해제] 새 거래일 09:00 - 시스템 재가동, 일일 손실 초기화")
                notifier.send_message("🔄 <b>[Kill Switch 자동 해제]</b>\n새 거래일이 시작되어 시스템이 재가동됩니다.")

    def _on_login(self, err_code):
        if err_code == 0:
            print("[OrderManager] 로그인 성공!")
            # 계좌번호 추출
            accounts = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO").split(';')
            accounts = [a for a in accounts if a] # 빈 문자열 제거
            
            print(f" => 보유 계좌 목록: {accounts}")
            
            # 주식 계좌(주로 끝자리가 '11'이거나 첫 번째 계좌)를 자동으로 선택합니다.
            # (선물옵션 계좌 등 파생 계좌가 선택되는 것을 방지)
            self.account_num = accounts[0]
            for acc in accounts:
                if acc.endswith('11'):
                    self.account_num = acc
                    break
                
            print(f" => 사용 계좌 (주식전용): {self.account_num}")
            
            # 예수금상세현황요청 (opw00001)
            print("[OrderManager] 계좌 예수금을 조회합니다...")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.account_num)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "") # 모의투자는 공백
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "예수금조회", "opw00001", 0, "0201")
        else:
            print(f"[OrderManager] 로그인 실패 (에러코드: {err_code})")

    def poll_signals(self):
        """DB의 signals 테이블에서 PENDING 상태인 주문을 찾아 실행하고 시간 청산 로직을 검사합니다."""
        if self.system_halted:
            return
            
        # 기존 DB 시그널 검사 로직
            
        conn = sqlite3.connect("kiwoom_data.db")
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT id, code, name, signal_type, price, strategy_name FROM signals WHERE status = 'PENDING' LIMIT 5")
            rows = cursor.fetchall()
            
            for row in rows:
                signal_id, code, name, signal_type, price, strategy_name = row
                
                print(f"\n[🚨 신규 주문 포착!] {name}({code}) - {signal_type} ({strategy_name})")
                
                # 주문 전송 로직 (자금 관리 및 비중 조절)
                order_type = 1 if signal_type == "BUY" else 2 # 1: 신규매수, 2: 신규매도
                
                if signal_type == "BUY":
                    # 1. 최대 보유 종목 수 제한
                    if len(self.portfolio) >= self.max_positions:
                        print(f" => [거절] 최대 보유 종목 수({self.max_positions}개) 초과. 신규 매수 스킵.")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                        continue
                        
                    # 2. 예산 할당 및 수량 계산 (남은 예수금 전체 기준이 아닌 1/N 캡)
                    budget_per_stock = self.available_balance // self.max_positions
                    
                    # [버그 픽스] 키움증권은 '시장가(03)' 주문 시 상한가(현재가*1.3) 기준으로 증거금을 요구합니다.
                    # 따라서 단순히 (예산//현재가)로 수량을 잡으면 증거금 부족으로 '거부' 처리됩니다.
                    safe_price_for_margin = price * 1.3 
                    qty = int(budget_per_stock // safe_price_for_margin)
                    
                    if qty <= 0:
                        print(f" => [거절] 예수금 부족으로 수량 산출 불가 (예산: {budget_per_stock:,}원, 상한가기준 필요금액: {safe_price_for_margin:,.0f}원).")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_NO_FUNDS' WHERE id = ?", (signal_id,))
                        continue
                        
                    print(f" => [자금 관리] 할당 예산: {budget_per_stock:,}원 / 산출 수량: {qty}주 (시장가 증거금 고려)")
                    # 체결 전 가결제: qty 산출에 사용한 safe_price 기준으로 차감해야 초과 매수 방지
                    self.available_balance -= int(qty * safe_price_for_margin)
                    self.pending_orders[code] = {'qty': qty, 'price': price, 'type': 'BUY'}
                    
                else: # SELL
                    if code in self.portfolio:
                        qty = self.portfolio[code]['qty']
                    else:
                        print(f" => [거절] 보유 중인 종목이 아님. 매도 스킵.")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_NOT_OWNED' WHERE id = ?", (signal_id,))
                        continue

                price_param = 0 # 시장가 주문 시 0
                hoga_gb = "03" # 03: 시장가, 00: 지정가
                
                # SendOrder(사용자구분명, 화면번호, 계좌번호, 주문유형, 종목코드, 주문수량, 주문가격, 거래구분, 원주문번호)
                clean_code = str(code).strip().zfill(6)
                res = self.kiwoom.dynamicCall(
                    "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                    ["AI_Order", "0101", self.account_num.strip(), order_type, clean_code, qty, price_param, hoga_gb, ""]
                )
                
                if res == 0:
                    print(f" => 주문 전송 성공! (DB 상태를 EXECUTED로 변경)")
                    cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                else:
                    print(f" => 주문 전송 실패 (에러코드: {res}). 상태를 FAILED로 변경.")
                    cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                    # 전송 자체가 실패했다면 가결제 금액 즉시 환불
                    if signal_type == "BUY":
                        self.available_balance += (qty * price)
                        if code in self.pending_orders:
                            del self.pending_orders[code]
                    
            conn.commit()
        except sqlite3.OperationalError:
            # 아직 signals 테이블이 생성되지 않은 경우 무시
            pass
        finally:
            conn.close()

    def _on_receive_msg(self, screen_no, rqname, trcode, msg):
        print(f"[Kiwoom System Msg] {msg}")

    def _on_receive_chejan_data(self, gubun, item_cnt, fid_list):
        """체결 잔고 수신 이벤트"""
        if gubun == "0": # 0: 접수/체결
            order_no = self.kiwoom.dynamicCall("GetChejanData(int)", 9203).strip()
            status = self.kiwoom.dynamicCall("GetChejanData(int)", 913).strip()
            name = self.kiwoom.dynamicCall("GetChejanData(int)", 302).strip()
            code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().replace("A", "")
            
            if status == "체결":
                exec_price = int(self.kiwoom.dynamicCall("GetChejanData(int)", 910).strip())
                exec_qty = int(self.kiwoom.dynamicCall("GetChejanData(int)", 911).strip())
                order_gubun = self.kiwoom.dynamicCall("GetChejanData(int)", 905).strip() # +매수, -매도
                
                print(f"[체결 완료] {name}({code}) | 주문번호: {order_no} | 체결가: {exec_price:,} | 체결량: {exec_qty} | 구분: {order_gubun}")
                
                if "매수" in order_gubun:
                    if code not in self.portfolio:
                        self.portfolio[code] = {
                            'name': name, 'buy_price': exec_price, 'qty': 0, 'max_price': exec_price,
                            'buy_time': datetime.now(), 'super_trend_mode': False,
                            'ma_10': 0, 'prev_ma_10': 0, 'ma_10_is_up': False
                        }
                    self.portfolio[code]['qty'] += exec_qty
                    self.portfolio[code]['max_price'] = max(self.portfolio[code]['max_price'], exec_price)
                    
                    # 실시간 시세 구독 (화면번호 0102)
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    print(f" => [리스크 관리] {name} 자동 청산(20MA + 1.5% 보장) 모니터링 시작 (매수가: {exec_price:,}원)")
                    
                    notifier.send_message(f"💰 <b>[매수 체결 완료] {name}</b>\n• 체결가: {exec_price:,}원\n• 수량: {exec_qty}주")
                    
                    
                elif "매도" in order_gubun:
                    if code in self.portfolio:
                        self.portfolio[code]['qty'] -= exec_qty
                        
                        # 손익 계산 및 예수금 원복
                        profit = (exec_price - self.portfolio[code]['buy_price']) * exec_qty
                        profit_pct = ((exec_price - self.portfolio[code]['buy_price']) / self.portfolio[code]['buy_price']) * 100
                        self.available_balance += (exec_price * exec_qty) # 매도 대금 현금화
                        
                        if profit < 0:
                            self.daily_realized_loss += abs(profit)
                            result_icon = "📉"
                        else:
                            result_icon = "🚀"
                            
                        notifier.send_message(f"{result_icon} <b>[매도 체결 완료] {name}</b>\n• 매도단가: {exec_price:,}원\n• 손익률: {profit_pct:+.2f}%\n• 실현손익: {profit:,}원")
                            
                        if self.portfolio[code]['qty'] <= 0:
                            del self.portfolio[code]
                            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "0102", code)
                            print(f" => [리스크 관리] {name} 자동 청산 모니터링 해제 (전량 매도)")
                            
                        # MDD 킬 스위치 감시 (시작 예수금 대비 -3% 누적 손실 시)
                        kill_switch_limit = self.initial_balance * 0.03
                        if self.daily_realized_loss >= kill_switch_limit and not self.system_halted:
                            print(f"\n[💀 KILL SWITCH 발동!] 일일 최대 허용 손실(-3%) 초과 ({self.daily_realized_loss:,}원 / 한도: {kill_switch_limit:,}원)")
                            print("모든 신규 매수를 차단하고 시스템 진입을 중지합니다 (System Halted).")
                            self.system_halted = True
                            
            elif status == "거부":
                print(f"[주문 거부] {name} | 주문번호: {order_no} | 키움증권 서버에서 주문을 거부했습니다.")
                if code in self.pending_orders and self.pending_orders[code].get('type') == 'BUY':
                    # 가결제했던 금액 환불
                    refund = self.pending_orders[code]['qty'] * self.pending_orders[code]['price']
                    self.available_balance += refund
                    print(f" => [자금 복구] 주문 거부로 인해 예수금 {refund:,}원이 원복되었습니다. (현재 예수금: {self.available_balance:,}원)")
                    del self.pending_orders[code]
            else:
                print(f"[주문 접수] {name} | 주문번호: {order_no} | 상태: {status}")

    def _on_receive_real_data(self, code, real_type, real_data):
        """실시간 시세 수신 이벤트 (+10MA 수익 극대화 / +1.5% 하드스탑 / -2% 손절 판별)"""
        if real_type == "주식체결":
            # 현재가 추출 (부호 제거)
            current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
            
            if code in self.portfolio:
                self.portfolio[code]['current_price'] = current_price
                pos = self.portfolio[code]
                if pos.get('pending_sell'):
                    return
                buy_price = pos['buy_price']
                profit_ratio = (current_price - buy_price) / buy_price
                
                ma_10 = pos.get('ma_10', 0)
                ma_20 = pos.get('ma_20', 0)
                ma_10_is_up = pos.get('ma_10_is_up', False)
                super_trend_mode = pos.get('super_trend_mode', False)
                
                sell_reason = None
                
                # 1. 고정 손절선 (-2%)
                if profit_ratio <= -0.02:
                    sell_reason = f"고정 손절선 도달 ({profit_ratio*100:.2f}%)"
                else:
                    if super_trend_mode:
                        # 2. 수익 극대화 모드 중: 20이평선 이탈 또는 +1.5% 마지노선 이탈 시 익절
                        if current_price < ma_20 and ma_20 > 0:
                            sell_reason = f"20이평선 하향 돌파 (수익률 {profit_ratio*100:.2f}%)"
                        elif profit_ratio <= 0.015:
                            sell_reason = f"+1.5% 최소 수익 보장선 이탈 (수익률 {profit_ratio*100:.2f}%)"
                    else:
                        # 3. +3% 목표가 도달 시
                        if profit_ratio >= 0.03:
                            if ma_10 > 0 and ma_10_is_up and current_price >= ma_10:
                                if not super_trend_mode:
                                    print(f"🌟 [{pos['name']}] 10선 상승 추세 감지! +3% 익절 보류 (수익 극대화 모드 진입)")
                                    self.portfolio[code]['super_trend_mode'] = True
                            else:
                                sell_reason = f"+3% 목표가 도달 (추세 꺾임, 즉시 익절)"
                    
                if sell_reason:
                    print(f"\n[🛡️ 자동 청산 발동!] {pos['name']} - {sell_reason}")
                    print(f"매수가({buy_price:,}원) -> 현재가({current_price:,}원). 전량 시장가 매도 전송! 🚀")
                    
                    res = self.kiwoom.dynamicCall(
                        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                        ["[AI_Auto_Exit]", "0103", self.account_num, 2, code, pos['qty'], 0, "03", ""]
                    )
                    
                    if res == 0:
                        self.portfolio[code]['pending_sell'] = True

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "예수금조회":
            # d+2 추정예수금 (주문가능금액)
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "d+2추정예수금").strip()
            if d2_deposit:
                self.available_balance = int(d2_deposit)
                if self.initial_balance == 0:
                    self.initial_balance = self.available_balance
            print(f" => [자금 관리] 주문 가능 예수금(D+2): {self.available_balance:,}원")
            
            # DB 폴링 시작
            print("[OrderManager] DB 큐 모니터링을 시작합니다. (64비트 엔진의 Signal 감지 대기 중...)")
            self.timer.start(3000) # 3초 간격

if __name__ == "__main__":
    manager = OrderManager()
    manager.app.exec_()
