import subprocess
import time
from datetime import datetime
import sys

def is_trading_hours(now):
    """현재 시간이 국내선물 주/야간 거래 시간인지 확인합니다."""
    # 주간: 08:50 ~ 15:45 (개장 전후 버퍼 포함)
    # 야간: 17:50 ~ 05:10 (익일)
    hour = now.hour
    minute = now.minute
    time_val = hour * 100 + minute
    
    # 주간장 체크
    if 850 <= time_val <= 1545:
        return True
    
    # 야간장 체크 (당일 저녁 ~ 자정 전)
    if 1750 <= time_val <= 2359:
        return True
        
    # 야간장 체크 (자정 이후 ~ 새벽 5시 10분)
    if 0 <= time_val <= 510:
        return True
        
    return False

def run_loop():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [LOOP] 실시간 선물 트레이딩 루프 시작")
    
    # 1. 실시간 전략 엔진 분석 (데이터 수집은 Order Manager가 수행)
    print("\n[1/1] 전략 엔진(BB & RSI Divergence) 평가 중...")
    subprocess.run([r"..\ai_trader\venv64\Scripts\python.exe", "-u", "futures_strategy_engine.py"])
    
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [OK] 사이클 완료. 다음 갱신 대기 중...")

if __name__ == "__main__":
    print("===================================================")
    print("  Futures Auto-Trader Loop (Day & Night Mode)")
    print("===================================================")
    print("- 데이터 수집 및 전략 분석이 주야간장 시간에 맞춰 반복됩니다.")
    print("- 주문 처리를 담당하는 'order_manager.py'를 별도로 실행해 주세요.")
    print("===================================================\n")
    
    # 기본 대기 시간 5분
    WAIT_SECONDS = 60 * 5 
    
    while True:
        now = datetime.now()
        
        if is_trading_hours(now):
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
            print(f"[{now.strftime('%H:%M:%S')}] 선물 거래 가능 시간이 아닙니다. 5분 대기합니다...")
            time.sleep(WAIT_SECONDS)
