import subprocess
import time
from datetime import datetime
import sys
import sqlite3

def run_day_trading_scripts():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [단타 로직] 데이터 갱신 및 시그널 분석 시작...")
    
    print("\n[DAY 1/3] 테마 및 주도주 탐색 중...")
    subprocess.run([sys.executable, "-u", "theme_tracker.py"])
    
    print("\n[DAY 2/3] 주도주 3분봉 데이터 수집 중...")
    subprocess.run([sys.executable, "-u", "screener.py"])
    
    print("\n[DAY 3/3] Combo 1(VWAP+Divergence) 매수 시그널 분석 중...")
    subprocess.run([sys.executable, "-u", "strategy_engine.py"])
    print("[단타 로직] 사이클 완료.")

def run_swing_trading_scripts():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [스윙 로직] 주도주 종가 베팅 발굴 시작...")
    subprocess.run([sys.executable, "-u", "swing_screener.py"])
    print("[스윙 로직] 사이클 완료.")

def cleanup_old_signals():
    try:
        conn = sqlite3.connect("unified_data.db")
        cursor = conn.cursor()
        # 10분 이상 지난 PENDING 신호는 취소 처리
        cursor.execute("UPDATE signals SET status = 'EXPIRED' WHERE status = 'PENDING' AND timestamp <= datetime('now', '-10 minutes', 'localtime')")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[cleanup_old_signals 오류] {e}")

if __name__ == "__main__":
    print("===================================================")
    print("  Unified Auto-Trader Data & Analysis Loop (64-bit)")
    print("===================================================")
    print("- 단타 로직은 4분마다, 스윙 로직은 12분마다 교차 실행됩니다.")
    print("- 키움증권 TR 제한(시간당 1000회)을 초과하지 않도록 자동 조율합니다.")
    print("===================================================\n")
    
    # 루프 타이머 변수
    minutes_elapsed = 0
    
    while True:
        now = datetime.now()
        
        # 주식장 시간 확인 (08:50 ~ 15:35)
        # 종가베팅을 위해 15:35까지는 실행
        time_val = now.hour * 100 + now.minute
        
        if 850 <= time_val <= 1535:
            # 단타 로직: 매 4분마다 실행 (0, 4, 8, 12...)
            if minutes_elapsed % 4 == 0:
                run_day_trading_scripts()
                cleanup_old_signals()
                
            # 스윙 로직: 매 12분마다 실행 (0, 12, 24...) 
            # 단타와 겹치면 단타가 끝난 직후 순차적으로 실행됨
            if minutes_elapsed % 12 == 0:
                run_swing_trading_scripts()
                cleanup_old_signals()
                
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 다음 갱신 대기 중 (1분)...")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 장 운영 시간이 아닙니다. 대기 중...")
            
        time.sleep(60)
        minutes_elapsed += 1
