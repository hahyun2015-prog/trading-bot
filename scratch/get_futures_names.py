import sys
import os
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

full_list = kiwoom.dynamicCall("GetFutureList()").strip()
codes = [c for c in full_list.split(";") if c]

out_lines = []
out_lines.append(f"Total codes: {len(codes)}")
for i, c in enumerate(codes):
    name = kiwoom.dynamicCall("GetMasterCodeName(QString)", c).strip()
    out_lines.append(f"[{i:03d}] Code: {c} -> Name: {name}")

# Write to file
workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out_path = os.path.join(workspace_root, "scratch", "futures_names.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines))

print("Wrote names to", out_path)
sys.exit(0)
