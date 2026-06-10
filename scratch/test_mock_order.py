import sys
import os
import json
import time
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop

# Load account
futures_account = "7034905131" # Default mock futures account
try:
    with open("config/config_local.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
        futures_account = cfg.get("accounts", {}).get("futures_account", futures_account)
except:
    pass

app = QApplication(sys.argv)
kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

def on_login(err_code):
    print(f"Login result: {err_code}")
    loop.exit()

kiwoom.OnEventConnect.connect(on_login)
print("Connecting to Kiwoom...")
kiwoom.dynamicCall("CommConnect()")
loop = QEventLoop()
loop.exec_()

# Resolve code
future_list = kiwoom.dynamicCall("GetFutureList()").strip()
order_code = "105V6000" # fallback
if future_list:
    codes = [c for c in future_list.split(";") if c and c.startswith("105")]
    if codes:
        order_code = codes[0]

print(f"Resolved code to order: {order_code}")
print(f"Using account: {futures_account}")

# Test LONG_ENTER parameter mapping:
# ord_kind = 1 (New)
# slby_tp = "2" (Buy)
ord_kind = 1
slby_tp = "2"
qty = 1

print("Sending SendOrderFO (LONG_ENTER) to Kiwoom...")
res = kiwoom.dynamicCall(
    "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
    ["FuturesLiveTest", "0200", futures_account, order_code, ord_kind, slby_tp, "3", qty, "0", ""]
)
print(f"Order result (res): {res}")

time.sleep(2)
sys.exit(0)
