import os
import sys
import time

if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop

class Tester:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive)
        self.login_loop = None
        self.tr_loop = None
        
        self.kiwoom.dynamicCall("CommConnect()")
        self.login_loop = QEventLoop()
        self.login_loop.exec_()
        
    def _on_login(self, err):
        print("Login:", err)
        if self.login_loop: self.login_loop.exit()
        
    def req(self):
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", "10500000")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", "5")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "야간선물조회", "opt50029", 0, "5029")
        self.tr_loop = QEventLoop()
        self.tr_loop.exec_()
        
    def _on_receive(self, scr, rq, tr, rec, next_str):
        if rq == "야간선물조회":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", tr, rq)
            print(f"Count: {cnt}")
            night_count = 0
            for i in range(cnt):
                dt = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", tr, rq, i, "체결시간").strip()
                if dt.endswith("045000") or dt.endswith("180500") or dt.endswith("230000") or dt.endswith("010000"):
                    close = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", tr, rq, i, "현재가").strip()
                    print(f"Night Date: {dt}, Close: {close}")
                    night_count += 1
            print(f"Total night records found: {night_count}")
            if self.tr_loop: self.tr_loop.exit()

if __name__ == "__main__":
    t = Tester()
    t.req()
