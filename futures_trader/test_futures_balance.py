import os
import sys

# PyQt5 환경 변수 에러 방지
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop

class TestFuturesBalance:
    def __init__(self):
        self.log_file = open('kiwoom_test_log.txt', 'w', encoding='utf-8')
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)
        
        self.login_loop = QEventLoop()
        self.tr_loop = QEventLoop()
        
        self.log("로그인 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        self.login_loop.exec_()

    def log(self, msg):
        self.log_file.write(msg + '\n')
        self.log_file.flush()
        print(msg)
        
    def _on_login(self, err_code):
        if err_code == 0:
            self.log("로그인 성공!")
            accounts_str = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            accounts = [a for a in accounts_str.split(';') if a]
            self.log(f"전체 계좌: {accounts}")
            
            for account_num in accounts:
                self.log(f"\n[{account_num}] 계좌 예수금 조회 시도 (opw20010)")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", account_num)
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "0000")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선옵예탁금조회", "opw20010", 0, "2003")
                self.tr_loop.exec_()
                
            for account_num in accounts:
                self.log(f"\n[{account_num}] 계좌 예수금 조회 시도 (opw00001)")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", account_num)
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "0000")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식예수금조회", "opw00001", 0, "2002")
                self.tr_loop.exec_()
                
            self.app.quit()
        else:
            self.log(f"로그인 실패: {err_code}")
            self.login_loop.quit()

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "선옵예탁금조회":
            fields = ["예탁금", "예수금", "현금주문가능금액", "주문가능금액", "추정예탁자산", "추정예탁총액", "주문가능현금", "추정예탁금"]
            self.log("  [opw20010] 결과값:")
            for f in fields:
                val = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, f).strip()
                if val:
                    self.log(f"    {f}: '{val}'")
            self.tr_loop.quit()
        elif rqname == "주식예수금조회":
            deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "주문가능금액").strip()
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "d+2추정예수금").strip()
            self.log(f"  [opw00001] 주문가능금액: {deposit}, d+2추정예수금: {d2_deposit}")
            self.tr_loop.quit()

    def _on_receive_msg(self, screen_no, rqname, trcode, msg):
        self.log(f"  [MSG] {msg}")

if __name__ == "__main__":
    tester = TestFuturesBalance()
