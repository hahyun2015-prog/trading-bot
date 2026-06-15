import os
import sys
import json
import sqlite3
import requests
import socket
from datetime import datetime, timedelta

# Force IPv4 globally to prevent DNS and connection timeout hangs on Windows
orig_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo

workspace_root = r"c:\Antigravity\AI_T_Agent"

def check_python():
    print("=== [1] 파이썬 환경 점검 ===")
    import struct
    bitness = struct.calcsize('P') * 8
    print(f"파이썬 실행 경로: {sys.executable}")
    print(f"파이썬 아키텍처: {bitness}-bit")
    print(f"파이썬 버전: {sys.version}")
    if bitness != 32:
        print("[WARNING] 키움 OpenAPI는 32비트 파이썬에서만 동작합니다!")
    else:
        print("[OK] 32비트 파이썬 가상환경(venv32) 확인 완료.")

def check_files():
    print("\n=== [2] 필수 파일 존재 여부 점검 ===")
    required_files = [
        "run_tca.bat", "run_era.bat", "run_sta.bat", "run_bqa.bat",
        "config\\config.json", "config\\config_local.json", "config\\active_strategy.json",
        "unified_data.db", "futures_data.db"
    ]
    all_exist = True
    for f in required_files:
        path = os.path.join(workspace_root, f)
        exists = os.path.exists(path)
        print(f"  {f}: {'존재함 [OK]' if exists else '없음 [FAIL]'}")
        if not exists:
            all_exist = False
    return all_exist

def check_db_integrity():
    print("\n=== [3] 데이터베이스 무결성(Integrity) 점검 ===")
    dbs = ["unified_data.db", "futures_data.db"]
    for db in dbs:
        db_path = os.path.join(workspace_root, db)
        if not os.path.exists(db_path):
            print(f"  {db}: 파일이 존재하지 않아 스킵합니다.")
            continue
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            res = cursor.fetchone()[0]
            print(f"  {db} 무결성 상태: {res}")
            if res == "ok":
                print(f"  [OK] {db} 상태 양호.")
            else:
                print(f"  [FAIL] {db}에 손상이 발견되었습니다!")
            conn.close()
        except Exception as e:
            print(f"  [FAIL] {db} 연결/조회 중 오류 발생: {e}")

def check_gdrive():
    print("\n=== [4] 구글 드라이브 동기화 폴더 점검 ===")
    gdrive_path = r"G:\내 드라이브\AI_Trading_Data"
    exists = os.path.exists(gdrive_path)
    print(f"  G: 드라이브 마운트 여부: {'정상 [OK]' if os.path.exists('G:') else '오류 [FAIL]'}")
    print(f"  AI_Trading_Data 폴더 존재 여부: {'존재함 [OK]' if exists else '없음 [FAIL]'}")
    if exists:
        try:
            # 쓰기 테스트
            test_file = os.path.join(gdrive_path, ".sync_check_temp.txt")
            with open(test_file, "w") as f:
                f.write(f"Sync check at {datetime.now()}")
            os.remove(test_file)
            print("  구글 드라이브 쓰기 권한: 정상 [OK]")
        except Exception as e:
            print(f"  [WARNING] 구글 드라이브 쓰기 오류: {e}")

def check_telegram():
    print("\n=== [5] 텔레그램 봇 연결 및 토큰 점검 ===")
    config_path = os.path.join(workspace_root, "config", "config.json")
    local_path = os.path.join(workspace_root, "config", "config_local.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                local = json.load(f)
            for k, v in local.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        
        tele = cfg.get("telegram", {})
        env = cfg.get("environment", "mock")
        dev_token = tele.get("dev_bot_token", "")
        bot_token = dev_token if (dev_token and env != "live") else tele.get("bot_token")
        chat_id = tele.get("allowed_chat_id")
        
        if not bot_token or not chat_id:
            print("  [FAIL] 텔레그램 봇 토큰이나 Chat ID가 설정되지 않았습니다.")
            return
            
        print(f"  환경: {env}")
        print(f"  사용할 Bot Token: {bot_token[:15]}...{bot_token[-5:] if len(bot_token) > 20 else ''}")
        print(f"  Allowed Chat ID: {chat_id}")
        
        # Test request to Telegram API
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get("ok"):
                bot_name = data["result"].get("username")
                print(f"  [OK] 텔레그램 봇 연결 성공! 봇 이름: @{bot_name}")
            else:
                print("  [FAIL] 텔레그램 API 응답 오류.")
        else:
            print(f"  [FAIL] 텔레그램 연결 실패 (HTTP {res.status_code})")
    except Exception as e:
        print(f"  [FAIL] 텔레그램 점검 실패: {e}")

def check_holiday():
    print("\n=== [6] 휴장일 및 내일 거래 여부 점검 ===")
    tomorrow_dt = datetime.now() + timedelta(days=1)
    tomorrow = tomorrow_dt.strftime("%Y-%m-%d")
    
    print(f"  내일 날짜: {tomorrow} ({'월화수목금토일'[tomorrow_dt.weekday()]}요일)")
    
    # 토/일 체크
    if tomorrow_dt.weekday() in (5, 6):
        print(f"  [휴장] 주말(토/일)이므로 내일은 거래일이 아닙니다.")
        return
        
    holiday_path = os.path.join(workspace_root, "config", "krx_holidays.json")
    if os.path.exists(holiday_path):
        try:
            with open(holiday_path, "r", encoding="utf-8") as f:
                h_data = json.load(f)
            year_str = str(tomorrow_dt.year)
            holidays = h_data.get(year_str, [])
            is_holiday = False
            holiday_name = ""
            for h in holidays:
                if h.get("date") == tomorrow:
                    is_holiday = True
                    holiday_name = h.get("name", "공휴일")
                    break
            if is_holiday:
                print(f"  [휴장] 내일은 한국거래소 휴장일({holiday_name})입니다.")
            else:
                print(f"  [OK] 내일({tomorrow})은 정상 영업/거래일입니다.")
        except Exception as e:
            print(f"  [WARNING] 휴장일 JSON 로드/확인 중 오류: {e}")
    else:
        print("  [WARNING] krx_holidays.json 파일이 없습니다. 영업일 체크를 건너뜁니다.")

def check_kiwoom_ocx():
    print("\n=== [7] 키움증권 ActiveX (KHOPENAPI) 등록 및 작동 상태 점검 ===")
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QAxContainer import QAxWidget
        
        # PyQt5 QApplication needs to be initialized
        app = QApplication.instance()
        if not app:
            app = QApplication([])
            
        kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        if kiwoom.isNull():
            print("  [FAIL] Kiwoom OpenAPI OCX 컨트롤을 생성하지 못했습니다. 키움 OpenAPI가 설치되지 않았거나 64비트 파이썬을 사용 중입니다.")
        else:
            print("  [OK] Kiwoom OpenAPI OCX 컨트롤이 32비트 파이썬에 성공적으로 생성 및 로드되었습니다.")
            # Verify if events are present
            try:
                # OnEventConnect is a standard event. Let's see if we can check if it exists or if we get attribute
                conn_event = getattr(kiwoom, "OnEventConnect")
                print("  [OK] OnEventConnect 이벤트 확인 완료. (정상 작동)")
            except AttributeError:
                print("  [FAIL] OnEventConnect 이벤트를 로드하지 못했습니다. (버전 처리 꼬임 혹은 비인증 세션 오류 가능성)")
    except Exception as e:
        print(f"  [FAIL] QAxWidget 로드 중 오류 발생: {e}")

if __name__ == '__main__':
    check_python()
    check_files()
    check_db_integrity()
    check_gdrive()
    check_telegram()
    check_holiday()
    check_kiwoom_ocx()
