import os
import requests

# 실행 전 환경변수 설정 필요:
#   set TELEGRAM_BOT_TOKEN=<your_token>
#   set TELEGRAM_CHAT_ID=<your_chat_id>
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_message(text):
    """
    텔레그램 봇 API를 통해 지정된 챗 아이디로 메시지를 발송합니다.
    오류가 발생하더라도 메인 시스템(매매)이 멈추지 않도록 예외 처리합니다.
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML" # HTML 태그 지원 (예: <b>굵게</b>)
    }
    
    if not BOT_TOKEN or not CHAT_ID:
        print("[텔레그램 알림] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.")
        return
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        print(f"[텔레그램 알림 오류] 메시지 전송 실패: {e}")

if __name__ == "__main__":
    # 단독 실행 시 테스트 메시지 발송
    send_message("🤖 <b>AI 트레이딩 봇</b>\n텔레그램 알림망 연동 테스트입니다.")
    print("테스트 메시지를 발송했습니다.")
