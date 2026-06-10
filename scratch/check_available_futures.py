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

print("GetFutureList():", kiwoom.dynamicCall("GetFutureList()"))
sys.exit(0)
