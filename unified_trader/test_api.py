import sys
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QAxContainer import QAxWidget
import time

class MyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.kw = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1", self)
        self.kw.OnEventConnect.connect(self.on_connect)
        print("ActiveX Created.")
        
    def login(self):
        print("Testing CommConnect()...")
        ret = self.kw.dynamicCall("CommConnect()")
        print("CommConnect returned:", ret)

    def on_connect(self, err_code):
        print("Login event received! Err_code:", err_code)

def main():
    app = QApplication(sys.argv)
    win = MyWindow()
    # It might need show() for Kiwoom to get a handle
    win.show() 
    win.login()
    
    # Wait for a bit
    end_time = time.time() + 5
    while time.time() < end_time:
        app.processEvents()
        time.sleep(0.1)
        
    sys.exit(0)

if __name__ == "__main__":
    main()
