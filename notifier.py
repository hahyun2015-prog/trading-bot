import os
import sys
import json
import requests

def _load_config():
    # 이 스크립트 위치 기준으로 config.json을 탐색하거나 절대경로로 로드
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, 'config', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[notifier] config.json 로드 실패: {e}")
        return {}

_CONFIG = _load_config()
_TELEGRAM = _CONFIG.get("telegram", {})
BOT_TOKEN = _TELEGRAM.get("bot_token")
CHAT_ID = _TELEGRAM.get("allowed_chat_id")

def send_message(text):
    """
    텔레그램 봇 API를 통해 지정된 챗 아이디로 메시지를 발송합니다.
    오류가 발생하더라도 메인 시스템(매매)이 멈추지 않도록 예외 처리합니다.
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

if __name__ == "__main__":
    send_message("🤖 <b>AI 트레이딩 시스템 (AMATS)</b>\n중앙 알림망 연동 테스트입니다.")
    print("테스트 메시지를 발송했습니다.")
