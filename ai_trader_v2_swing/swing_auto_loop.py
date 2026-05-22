import time
from datetime import datetime
import subprocess
import sys

def run_swing_screener():
    print(f"\n[오후 3시 10분] 종가 베팅 스윙 대장주 발굴을 시작합니다...")
    subprocess.run([sys.executable, "swing_screener.py"])
    print("[오후 3시 10분] 발굴 완료. order_manager가 매수를 집행합니다.")

if __name__ == "__main__":
    print("=======================================")
    print(" [Swing Auto Loop] 대장주 스윙 타이머")
    print(" - 매일 오후 3시 10분에 발굴기가 작동합니다.")
    print("=======================================")
    
    has_run_today = False
    
    while True:
        now = datetime.now()
        
        # 주말(토=5, 일=6) 제외
        if now.weekday() < 5:
            # 오후 3시 10분 ~ 3시 30분 사이이고, 오늘 아직 실행되지 않았다면 즉시 실행!
            # (봇을 3시 15분에 늦게 켜더라도 바로 스캔을 시작하게 됩니다)
            if now.hour == 15 and 10 <= now.minute <= 30 and not has_run_today:
                run_swing_screener()
                has_run_today = True
                
            # 자정이 되면 초기화 (다음 날을 위해)
            if now.hour == 0 and now.minute == 0:
                has_run_today = False
                
        time.sleep(1) # 1초마다 시간 체크
