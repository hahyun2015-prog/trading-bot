import requests

# 사용자 제공 텔레그램 봇 토큰 및 챗 아이디
BOT_TOKEN = "8710417841:AAGm1AZxo-u9RTQX_MeRRDpz_ggvS4mvexk"
CHAT_ID = "8578720404"

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
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        print(f"[텔레그램 알림 오류] 메시지 전송 실패: {e}")

if __name__ == "__main__":
    # 단독 실행 시 테스트 메시지 발송
    send_message("🤖 <b>AI 트레이딩 봇</b>\n텔레그램 알림망 연동 테스트입니다.")
    print("테스트 메시지를 발송했습니다.")
