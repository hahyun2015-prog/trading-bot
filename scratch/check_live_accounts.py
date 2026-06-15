import sys
import os
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop

# PyQt5 플러그인 경로 설정
_exe_dir = os.path.dirname(sys.executable)
_qt_base = os.path.join(_exe_dir, "Lib", "site-packages", "PyQt5")
_qt_plugin_path = os.path.join(_qt_base, "Qt5", "plugins")
if not os.path.exists(_qt_plugin_path):
    _qt_plugin_path = os.path.join(_qt_base, "Qt", "plugins")
if os.path.exists(_qt_plugin_path):
    os.environ["QT_PLUGIN_PATH"] = _qt_plugin_path
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_qt_plugin_path, "platforms")

class KiwoomAccountChecker:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self.on_connect)
        self.loop = QEventLoop()
        
    def check(self):
        print("[Kiwoom Checker] 로그인 요청 중... (화면에 로그인 창이 뜨면 로그인을 진행해 주세요)")
        res = self.kiwoom.dynamicCall("CommConnect()")
        if res != 0:
            print("[Kiwoom Checker] CommConnect 호출 실패")
            return None
        self.loop.exec_()
        
    def on_connect(self, err_code):
        print(f"[Kiwoom Checker] 로그인 콜백 수신: 에러코드 {err_code}")
        if err_code == 0:
            raw_accounts = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            user_id = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "USER_ID")
            user_name = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "USER_NAME")
            server_gubun = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "GetServerGubun")
            
            # server_gubun이 '1' 이면 모의투자, 없거나 다른 값이면 실전투자
            is_mock = (server_gubun.strip() == "1")
            env_label = "MOCK (모의투자)" if is_mock else "REAL/LIVE (실전매매)"
            
            accounts = [a.strip() for a in raw_accounts.split(';') if a.strip()]
            print("\n==============================================")
            print(f" User ID: {user_id}")
            print(f" Server Type: {env_label}")
            print(f" Account List (Total {len(accounts)}):")
            for i, acc in enumerate(accounts):
                # 계좌 정보 유형 대략 분류 (끝자리 11은 위탁/주식 등)
                acc_type = "주식/위탁" if acc.endswith("11") else "선물옵션/기타"
                print(f"   [{i}] 계좌번호: {acc} ({acc_type})")
            print("==============================================")
        else:
            print(f"[Kiwoom Checker] 로그인 실패 (에러코드: {err_code})")
        self.loop.exit()

if __name__ == "__main__":
    checker = KiwoomAccountChecker()
    checker.check()
