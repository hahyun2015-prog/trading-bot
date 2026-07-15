import socket
# Force IPv4 globally to prevent IPv6 DNS resolution hangs and connection timeouts on Windows
orig_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    except Exception:
        return orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo

import os
import sys
import json
import time
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

MAX_RETRIES = 3

def _send_message_sync(text, max_retries=MAX_RETRIES):
    """
    실제 동기식 전송을 수행하는 내부 함수.
    429(Too Many Requests) 및 일시적 네트워크 오류 시 지수 백오프로 재시도한다.
    (2026-07-01: 진입/청산 무한루프 사고로 하루 수천 건의 알림이 몰리며 429가 6천건 이상
     발생, 재시도 없이 그대로 유실되어 긴급 알림까지 못 받을 뻔한 사고 이후 도입)
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

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 429:
                # 텔레그램이 응답 바디에 권장 대기시간(retry_after, 초)을 내려주면 그것을 우선 사용
                try:
                    retry_after = float(response.json().get("parameters", {}).get("retry_after", 0))
                except Exception:
                    retry_after = 0
                wait = max(retry_after, 2 ** attempt)
                if attempt < max_retries:
                    print(f"[텔레그램 알림] 429 Rate Limit — {wait:.1f}초 대기 후 재시도 ({attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
                print(f"[텔레그램 알림 오류] 429 재시도 {max_retries}회 모두 실패 — 메시지 유실: {text[:50]}...")
                return
            response.raise_for_status()
            return  # 성공
        except Exception as e:
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"[텔레그램 알림] 전송 실패({e}) — {wait}초 후 재시도 ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                print(f"[텔레그램 알림 오류] {max_retries}회 재시도 후 최종 실패: {e}")
                return

def _worker():
    # 텔레그램은 동일 채팅 기준 초당 약 1건으로 속도 제한을 둠 — 그 이상으로 몰아 보내면
    # 429가 발생하므로, 전송 사이 최소 간격을 둬서 애초에 제한에 안 걸리도록 예방한다.
    MIN_INTERVAL = 1.1
    last_sent = 0.0
    while True:
        text = _msg_queue.get()
        if text is None:
            break
        elapsed = time.time() - last_sent
        if elapsed < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - elapsed)
        _send_message_sync(text)
        last_sent = time.time()
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
