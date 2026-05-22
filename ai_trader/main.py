import sys
import os

# PyQt5 플러그인 에러 방지용 환경변수 강제 세팅
import PyQt5
plugin_path = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins", "platforms")
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from kiwoom_api import KiwoomAPI

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False) # 창이 닫혀도 프로그램이 종료되지 않게 설정
    
    # 1. 키움증권 API 인스턴스 생성 및 창 띄우기
    kiwoom = KiwoomAPI()
    kiwoom.show() # 프로그램이 바로 종료되지 않도록 빈 창이라도 띄움
    
    # 이벤트 루프가 시작된 후 1초 뒤에 로그인 창을 띄우도록 예약
    from PyQt5.QtCore import QTimer
    QTimer.singleShot(1000, kiwoom.login)
    
    # 2. 로직 실행 및 이벤트 루프 진입
    # TODO: AI 전략 엔진 및 데이터 수신 연결
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
