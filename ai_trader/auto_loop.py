import subprocess
import time
from datetime import datetime
import sys

def run_loop():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 AI 트레이딩 사이클 시작")
    
    # 1. 테마/수급 트래커 (32-bit)
    print("\n[1/3] 실시간 주도 테마 및 스마트 머니 수급 탐색 중...")
    subprocess.run([r".\venv32\Scripts\python.exe", "theme_tracker.py"])
    
    # 2. 3분봉 스크리너 (32-bit)
    print("\n[2/3] 주도주 3분봉 실시간 차트 데이터 수집 중...")
    subprocess.run([r".\venv32\Scripts\python.exe", "screener.py"])
    
    # 3. 전략 엔진 (64-bit)
    print("\n[3/3] Combo 1 (VWAP+다이버전스) 전략 엔진 타점 분석 중...")
    subprocess.run([r".\venv64\Scripts\python.exe", "strategy_engine.py"])
    
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ 사이클 완료. 다음 갱신까지 3분 대기합니다.")

if __name__ == "__main__":
    print("===================================================")
    print("  AI Quant Auto-Trader Loop (Continuous Mode)")
    print("===================================================")
    print("✓ 주도주 탐색, 분봉 수집, 타점 분석이 무한 반복됩니다.")
    print("✓ 실제 주문을 처리하는 'order_manager.py'는 별도 창에서 실행 중이어야 합니다.")
    print("===================================================\n")
    
    # 3분(180초) 주기
    WAIT_SECONDS = 60 * 3 
    
    while True:
        now = datetime.now()
        
        # 한국 주식시장 정규 시간 (오전 9시 ~ 오후 3시 30분)
        # 휴일 체크 로직 등은 추후 고도화 가능. 우선 시간대로만 제한.
        # 장 시작 전(08:50)부터 15:30까지만 루프를 돌도록 설정
        current_time_val = now.hour * 100 + now.minute
        
        if 850 <= current_time_val <= 1530:
            try:
                run_loop()
                time.sleep(WAIT_SECONDS)
            except KeyboardInterrupt:
                print("\n[시스템 종료] 사용자 요청으로 루프를 정지합니다.")
                sys.exit(0)
            except Exception as e:
                print(f"\n[오류 발생] 루프 실행 중 에러: {e}")
                print("1분 후 재시도합니다.")
                time.sleep(60)
        else:
            print(f"[{now.strftime('%H:%M:%S')}] 정규 장 시간이 아닙니다 (오전 9시 ~ 오후 3시 30분). 3분 대기합니다...")
            time.sleep(WAIT_SECONDS)
