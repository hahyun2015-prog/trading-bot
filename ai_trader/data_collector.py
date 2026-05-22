import sys
import time
import csv
import os
import PyQt5

# PyQt5 플러그인 에러 방지 (32비트 환경)
plugin_path = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins", "platforms")
if 'venv32' in sys.executable or 'venv64' in sys.executable:
    qt_plugin_path = os.path.join(os.path.dirname(sys.executable), 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
    os.environ['QT_PLUGIN_PATH'] = qt_plugin_path

from PyQt5.QtWidgets import QApplication
from kiwoom_api import KiwoomAPI
from PyQt5.QtCore import QTimer

def run_data_collector():
    app = QApplication(sys.argv)
    
    # 윈도우 창이 모두 닫혀도 프로그램이 종료되지 않도록 설정 (시스템 트레이 백그라운드용)
    app.setQuitOnLastWindowClosed(False)
    
    print("=== [과거 차트 데이터 수집기] ===")
    api = KiwoomAPI()
    
    # 로그인 지연 호출
    QTimer.singleShot(1000, api.login)
    
    def save_csv(api_instance, filename):
        if api_instance.ohlcv_data:
            api_instance.ohlcv_data.reverse()
            with open(filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
                writer.writerows(api_instance.ohlcv_data)
            print(f"수집 완료! 총 {len(api_instance.ohlcv_data)}일 치 데이터가 '{filename}' 파일로 저장되었습니다.")
        else:
            print(f"수집된 데이터가 없습니다. ({filename})")

    def on_login_success():
        if hasattr(api, 'account_no'):
            print("로그인 확인 완료! 데이터 수집을 시작합니다.")
            
            # 1. 삼성전자 일봉 수집
            target_code = "005930" # 삼성전자
            print(f"\n[{target_code}] 주식 일봉 차트 대량 수집 시작...")
            api.req_historical_data(target_code, "opt10081", "주식일봉차트조회")
            save_csv(api, f"{target_code}_daily_chart.csv")
            
            # API 서버 부하 방지를 위한 1초 대기
            time.sleep(1)
            
            # 2. 코스피200 선물 일봉 수집
            future_code = "10100000" # 코스피200 최근월물 대표코드
            print(f"\n[{future_code}] 코스피200 선물 일봉 차트 대량 수집 시작...")
            api.req_historical_data(future_code, "opt50028", "선물일봉차트조회")
            save_csv(api, f"{future_code}_future_daily_chart.csv")
            
            # 수집 완료 후 프로그램 종료
            print("\n데이터 수집기를 종료합니다.")
            app.quit()
        else:
            QTimer.singleShot(1000, on_login_success)
            
    # 5초 후 로그인 성공 여부 지속 체크 시작
    QTimer.singleShot(5000, on_login_success)
    
    app.exec_()

if __name__ == "__main__":
    run_data_collector()
