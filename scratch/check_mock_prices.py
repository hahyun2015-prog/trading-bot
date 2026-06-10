import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop

app = QApplication(sys.argv)
kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

def on_login(err):
    print("Login code:", err)
    loop.exit()

kiwoom.OnEventConnect.connect(on_login)
kiwoom.dynamicCall("CommConnect()")
loop = QEventLoop()
loop.exec_()

# We will query opt50029 for A0166000
def request_candles(code):
    print(f"\nRequesting candles for {code}...")
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", "5")
    
    received_loop = QEventLoop()
    
    def on_receive(scr_no, rqname, trcode, record_name, prev_next, data_len, err_code, msg_wnd, source):
        if rqname == "test_query":
            cnt = kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f"Received {cnt} candles for {code}")
            if cnt > 0:
                date = kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "체결시간").strip()
                close = kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "현재가").strip()
                print(f"First candle: date={date}, close={close}")
            received_loop.exit()
            
    kiwoom.OnReceiveTrData.connect(on_receive)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "test_query", "opt50029", 0, "5029")
    received_loop.exec_()

request_candles("A0166000")
request_candles("A0566000")
sys.exit(0)
