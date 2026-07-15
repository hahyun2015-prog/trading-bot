import os
import sys
import time
import sqlite3
import json
import subprocess
from datetime import datetime, timedelta
import pandas as pd
from ta.trend import ADXIndicator
from ta.volatility import BollingerBands

# Ensure AI_T_Agent root is in path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

try:
    import notifier
except ImportError as e:
    print("[ERROR] notifier.py import failed:", e)
    notifier = None

DB_PATH = os.path.join(current_dir, "futures_data.db")
CONFIG_PATH = os.path.join(current_dir, "config", "active_strategy.json")
AUTO_RECONNECT_BAT = os.path.join(current_dir, "era", "auto_reconnect_era.bat")

def load_active_strategy():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Regime Monitor] active_strategy.json 로드 실패: {e}")
        return {}

def save_active_strategy(config):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        print("[Regime Monitor] active_strategy.json 저장 완료.")
        return True
    except Exception as e:
        print(f"[Regime Monitor] active_strategy.json 저장 실패: {e}")
        return False

def calculate_metrics(until_date_str=None):
    """
    until_date_str: 'YYYYMMDD235959' 형식. 특정 시간 이전의 데이터만 사용하여 계산 (장 시작 전 분석용)
    """
    if not os.path.exists(DB_PATH):
        print(f"[Regime Monitor] DB 파일을 찾을 수 없습니다: {DB_PATH}")
        return None, None
        
    conn = sqlite3.connect(DB_PATH)
    
    # KOSPI200 주간선물(10100000) 최근 5분봉 데이터 로드
    if until_date_str:
        query = f"""
        SELECT * FROM (
            SELECT date, open, high, low, close, volume 
            FROM futures_ohlcv 
            WHERE code = '10100000' AND date <= '{until_date_str}'
            ORDER BY date DESC 
            LIMIT 20000
        ) ORDER BY date ASC
        """
    else:
        query = """
        SELECT * FROM (
            SELECT date, open, high, low, close, volume 
            FROM futures_ohlcv 
            WHERE code = '10100000' 
            ORDER BY date DESC 
            LIMIT 20000
        ) ORDER BY date ASC
        """
        
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty or len(df) < 500:
        print("[Regime Monitor] 지표 계산을 위한 데이터가 부족합니다.")
        return None, None
        
    df['datetime'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('datetime', inplace=True)
    
    # 주간 거래 세션만 필터링 (08:45 ~ 15:45)
    df_day = df.between_time('08:45', '15:45')
    
    # 일봉으로 리샘플링
    daily = df_day.resample('D').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    if len(daily) < 30:
        print("[Regime Monitor] 리샘플링된 일봉 데이터가 부족합니다.")
        return None, None
        
    # ADX(14) 및 Bollinger Band Width(20, 2.0) 계산
    adx_ind = ADXIndicator(high=daily['high'], low=daily['low'], close=daily['close'], window=14)
    daily['adx'] = adx_ind.adx()
    
    bb = BollingerBands(close=daily['close'], window=20, window_dev=2.0)
    daily['bb_w'] = bb.bollinger_wband()
    
    latest = daily.iloc[-1]
    return float(latest['adx']), float(latest['bb_w'])

def run_reconnect_script():
    if not os.path.exists(AUTO_RECONNECT_BAT):
        print(f"[Regime Monitor] 재시작 스크립트 없음: {AUTO_RECONNECT_BAT}")
        return False
    try:
        # Windows 환경에서 새 콘솔 창에서 백그라운드로 안전하게 실행
        subprocess.Popen([AUTO_RECONNECT_BAT], shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
        print("[Regime Monitor] auto_reconnect_era.bat 백그라운드 구동 명령 전송 완료.")
        return True
    except Exception as e:
        print(f"[Regime Monitor] 재시작 스크립트 실행 오류: {e}")
        return False

def get_regime_name(adx):
    if adx > 25:
        return "추세장 (Trend)"
    elif adx < 20:
        return "박스권 (Range)"
    else:
        return "중립장 (Neutral)"

def get_recommended_strategy(adx):
    if adx > 25:
        return "parabolic_sar"
    elif adx < 20:
        return "bollinger_band"
    else:
        return None  # Neutral 상태는 변경 안 함

def send_telegram(msg):
    print(f"[Telegram Notification] {msg}")
    if notifier:
        notifier.send_message(msg)

def perform_morning_analysis():
    print("[Regime Monitor] 07:10 아침 분석을 구동합니다...")
    # 어제 날짜 23시 59분 59초 이전 데이터만 사용
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    until_date = f"{yesterday}235959"
    
    adx, bb_w = calculate_metrics(until_date)
    if adx is None:
        send_telegram("⚠️ <b>[시장성격 분석 에러]</b> 데이터를 분석할 수 없습니다.")
        return None
        
    regime = get_regime_name(adx)
    recommended = get_recommended_strategy(adx)
    
    config = load_active_strategy()
    current_strat = config.get("futures_strategy_type", "unknown")
    
    msg_header = f"🤖 <b>[시장 성격 아침 분석 알림]</b>\n"
    msg_body = f"• 판별 시점: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n" \
               f"• 일봉 ADX: <b>{adx:.2f}</b> ({regime})\n" \
               f"• BB Width: <b>{bb_w:.2f}%</b>\n" \
               f"• 현재 활성 전략: <code>{current_strat}</code>\n"
               
    if recommended and recommended != current_strat:
        # 전략 변경 필요
        config["futures_strategy_type"] = recommended
        config["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if save_active_strategy(config):
            msg_body += f"★ <b>전략 자동 전환 수행: {current_strat} ➔ {recommended}</b>\n" \
                       f"⚠️ 60초 대기 후 ERA 주문 엔진을 재시작합니다."
            send_telegram(msg_header + msg_body)
            # 엔진 재시작 구동
            run_reconnect_script()
        else:
            msg_body += f"❌ 전략 설정 업데이트에 실패하여 이전 전략을 유지합니다."
            send_telegram(msg_header + msg_body)
    else:
        # 전략 유지
        msg_body += f"✓ 시장 성격에 부합하여 기존 전략(<b>{current_strat}</b>)을 유지합니다."
        send_telegram(msg_header + msg_body)
        
    return adx

def perform_intraday_check(morning_adx, last_adx, intraday_switched):
    """
    장중 5분마다 실시간 ADX를 모니터링하여 급변 여부를 체크합니다.
    """
    adx, bb_w = calculate_metrics(None)
    if adx is None:
        return last_adx, intraday_switched
        
    # 아침 ADX 기준 급변 확인 (차이 5.0 이상)
    adx_diff = adx - morning_adx
    
    # 텔레그램 알림용 급변 감지
    if abs(adx - last_adx) >= 5.0:
        send_telegram(
            f"⚠️ <b>[장중 시장성격 급변 감지]</b>\n"
            f"• 현재 시점: {datetime.now().strftime('%H:%M')}\n"
            f"• 실시간 ADX: <b>{adx:.2f}</b> (직전 체크 대비 변동: {adx - last_adx:+.2f})\n"
            f"• BB Width: <b>{bb_w:.2f}%</b>"
        )
        
    # 만약 Regime이 바뀌었고, 오늘 아직 장중 전환을 하지 않았다면 전환 수행
    if not intraday_switched:
        recommended = get_recommended_strategy(adx)
        config = load_active_strategy()
        current_strat = config.get("futures_strategy_type", "unknown")
        
        if recommended and recommended != current_strat:
            config["futures_strategy_type"] = recommended
            config["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if save_active_strategy(config):
                regime = get_regime_name(adx)
                send_telegram(
                    f"🚨 <b>[장중 전략 실시간 자동 전환]</b>\n"
                    f"시장 성격이 실시간으로 급변하여 전략을 전환합니다.\n"
                    f"• 실시간 ADX: <b>{adx:.2f}</b> ({regime})\n"
                    f"• 전략 전환: <b>{current_strat} ➔ {recommended}</b>\n"
                    f"⚠️ 60초 대기 후 ERA 주문 엔진을 실시간 재시작합니다."
                )
                run_reconnect_script()
                intraday_switched = True
                
    return adx, intraday_switched

def main():
    print("==========================================================")
    print("    AMATS Market Regime & Auto-Switching Monitor Started  ")
    print("==========================================================")
    
    # 최초 구동 시 현재 데이터 기반으로 어제 기준 ADX 구함
    morning_adx = None
    last_adx = None
    intraday_switched = False
    
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    init_adx, _ = calculate_metrics(f"{yesterday}235959")
    if init_adx is not None:
        morning_adx = init_adx
        last_adx = init_adx
        print(f"[Regime Monitor] 초기 아침 기준 ADX 감지 완료: {morning_adx:.2f}")
    
    # 07:10 분석 및 장중(09:00 ~ 15:45) 5분 주기 루프
    # 하루 일과가 끝나면 다음 날 07:10까지 대기
    while True:
        try:
            now = datetime.now()
            # 주말(토요일=5, 일요일=6)에는 대기
            if now.weekday() >= 5:
                print("[Regime Monitor] 주말이므로 대기합니다 (1시간 대기)...")
                time.sleep(3600)
                continue
                
            current_time_str = now.strftime("%H:%M")
            
            # 1. 아침 07:10 정각 분석 수행
            if current_time_str == "07:10":
                morning_adx = perform_morning_analysis()
                if morning_adx is not None:
                    last_adx = morning_adx
                intraday_switched = False # 새로운 거래일 시작 시 장중 전환 여부 초기화
                print("[Regime Monitor] 아침 분석 완료. 60초 대기합니다...")
                time.sleep(65)
                continue
                
            # 2. 장중 실시간 감시 (09:00 ~ 15:45)
            # 5분 간격 체크
            start_time = datetime_time = now.time()
            is_market_hours = (now.hour == 8 and now.minute >= 45) or (9 <= now.hour < 15) or (now.hour == 15 and now.minute <= 45)
            
            if is_market_hours and morning_adx is not None:
                # 5분 단위 실행을 위해, 분의 일의 자리가 0 또는 5일 때 실행
                if now.minute % 5 == 0 and now.second < 15:
                    print(f"[Regime Monitor] 장중 실시간 체크 실행 중 ({current_time_str})...")
                    last_adx, intraday_switched = perform_intraday_check(morning_adx, last_adx, intraday_switched)
                    time.sleep(20) # 중복 실행 방지 대기
                    continue
            
            # 기본 대기 주기 (15초)
            time.sleep(15)
            
        except KeyboardInterrupt:
            print("[Regime Monitor] 사용자에 의해 모니터가 종료되었습니다.")
            break
        except Exception as e:
            print(f"[Regime Monitor] 메인 루프 예외 발생: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
