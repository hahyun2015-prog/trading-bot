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

# Check GetOptionCode
for name, sc in [("삼성전자", "005930"), ("SK하이닉스", "000660")]:
    print(f"\n--- {name} ({sc}) ---")
    
    # Method 1: GetOptionCode("F", "0", sc, "")
    try:
        opt_code = kiwoom.dynamicCall("GetOptionCode(QString, QString, QString, QString)", ["F", "0", sc, ""]).strip()
        print(f"GetOptionCode('F', '0', {sc}, ''): {opt_code}")
    except Exception as e:
        print("GetOptionCode error:", e)
        
    # Method 2: GetFutureList search
    full_list = kiwoom.dynamicCall("GetFutureList()").strip()
    matched_codes = [c for c in full_list.split(";") if sc in c]
    print(f"GetFutureList matched with {sc}: {matched_codes}")

sys.exit(0)
