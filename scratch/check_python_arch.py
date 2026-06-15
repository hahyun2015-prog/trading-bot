import struct
import os
import datetime
import win32com.client
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtWidgets import QApplication

app = QApplication([])

print("=== Python Diagnostics ===")
arch = struct.calcsize("P") * 8
print(f"Python Architecture: {arch}-bit")

# Check Kiwoom ActiveX CLSID
clsid = "{A1574A0D-9B58-466E-BF08-BD37C5E0D10B}" # KHOpenAPICtrl.KHOpenAPI.1
print("\n=== ActiveX / Kiwoom Check ===")
try:
    widget = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
    control_loaded = widget.setControl("KHOPENAPI.KHOpenAPICtrl.1")
    print(f"QAxWidget load control result: {control_loaded}")
    if control_loaded:
        print("QAxWidget successfully loaded KHOpenAPI control!")
        # Check if OnEventConnect exists
        has_attr = hasattr(widget, "OnEventConnect")
        print(f"Has OnEventConnect attribute: {has_attr}")
    else:
        print("QAxWidget failed to load KHOpenAPI control.")
except Exception as e:
    print(f"Error loading QAxWidget: {e}")

try:
    # Try win32com client dispatch
    kh = win32com.client.Dispatch("KHOPENAPI.KHOpenAPICtrl.1")
    print("win32com successfully loaded KHOpenAPI control!")
except Exception as e:
    print(f"win32com Dispatch failed: {e}")

# Check files mod times
print("\n=== File Modification Times ===")
for path in [r'c:\Antigravity\AI_T_Agent\era\era_crash.log', r'c:\Antigravity\AI_T_Agent\era\era_order_manager.log']:
    if os.path.exists(path):
        mtime = os.path.getmtime(path)
        dt = datetime.datetime.fromtimestamp(mtime)
        print(f"{os.path.basename(path)}: Last modified at {dt}")
    else:
        print(f"{os.path.basename(path)}: File does not exist")

# Show last 20 lines of log
log_path = r'c:\Antigravity\AI_T_Agent\era\era_order_manager.log'
if os.path.exists(log_path):
    print("\n=== Last 20 lines of era_order_manager.log ===")
    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
        for line in lines[-20:]:
            print(line.strip())
