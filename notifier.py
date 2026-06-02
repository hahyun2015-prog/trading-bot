import os
import sys
import json
import requests

# 윈도우 CP949 콘솔 인코딩 에러(이모지 출력 크래시) 원천 방지 래퍼 클래스
class SafeStreamWrapper:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        
    def write(self, data):
        if not data:
            return
        try:
            encoding = getattr(self.original_stream, 'encoding', 'cp949') or 'cp949'
            data.encode(encoding)
            self.original_stream.write(data)
        except UnicodeEncodeError:
            cleaned_data = ""
            for char in data:
                try:
                    char.encode(encoding)
                    cleaned_data += char
                except UnicodeEncodeError:
                    pass  # 인코딩이 불가능한 이모지만 안전하게 발라냄
            self.original_stream.write(cleaned_data)
            
    def flush(self):
        self.original_stream.flush()

sys.stdout = SafeStreamWrapper(sys.stdout)
sys.stderr = SafeStreamWrapper(sys.stderr)

def _load_config():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, 'config', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"[notifier] config.json 로드 실패: {e}")
        return {}
    # config_local.json 오버라이드 (동기화 제외 파일)
    local_path = os.path.join(current_dir, 'config', 'config_local.json')
    if os.path.exists(local_path):
        try:
            with open(local_path, 'r', encoding='utf-8') as f:
                local = json.load(f)
            for key, val in local.items():
                if isinstance(val, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(val)
                else:
                    cfg[key] = val
        except Exception:
            pass
    return cfg

_CONFIG = _load_config()
_TELEGRAM = _CONFIG.get("telegram", {})
_ENV = _CONFIG.get("environment", "mock")
# 모의투자 PC에 dev_bot_token이 있으면 그것을 사용 (2대 동시 가동 시 알림 채널 분리)
_DEV_TOKEN = _TELEGRAM.get("dev_bot_token", "")
BOT_TOKEN = _DEV_TOKEN if (_DEV_TOKEN and _ENV != "live") else _TELEGRAM.get("bot_token")
CHAT_ID = _TELEGRAM.get("allowed_chat_id")

import queue
import threading

_msg_queue = queue.Queue()

def _send_message_sync(text):
    """
    실제 동기식 전송을 수행하는 내부 함수
    """
    if not BOT_TOKEN or not CHAT_ID:
        print("[텔레그램 알림] config.json에 bot_token 또는 allowed_chat_id가 설정되지 않았습니다.")
        return
        
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML" # HTML 태그 지원
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        print(f"[텔레그램 알림 오류] 메시지 전송 실패: {e}")

def _worker():
    while True:
        text = _msg_queue.get()
        if text is None:
            break
        _send_message_sync(text)
        _msg_queue.task_done()

# 백그라운드 워커 스레드 시작
_worker_thread = threading.Thread(target=_worker, daemon=True)
_worker_thread.start()

def send_message(text):
    """
    텔레그램 알림 메시지를 큐에 즉시 삽입합니다. (비동기 처리)
    메인 스레드를 전혀 대기(블로킹)시키지 않습니다.
    """
    _msg_queue.put(text)

if __name__ == "__main__":
    send_message("🤖 <b>AI 트레이딩 시스템 (AMATS)</b>\n중앙 알림망 연동 테스트입니다.")
    print("테스트 메시지를 발송했습니다.")
