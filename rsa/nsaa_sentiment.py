import os
import sys
import requests
import json
from bs4 import BeautifulSoup

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

class NewsSentimentAnalystAgent:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        self.api_key = self._load_gemini_key()

        # 오류 추적 (rsa_coordinator가 분석 후 조회)
        self.errors = []   # [{"code", "name", "type", "detail"}]

        # 백업 감성 사전 정의
        self.positive_keywords = ["상승", "호재", "수주", "계약", "돌파", "실적개선", "흑자전환", "신고가", "수혜", "인수", "파트너십", "최대실적", "협력", "개발", "체결", "FDA", "승인"]
        self.negative_keywords = ["하락", "악재", "적자", "과징금", "횡령", "소송", "유상증자", "상장폐지", "불성실", "취소", "우려", "둔화", "계약해지", "급락", "분쟁", "경고", "피소"]

    def reset_errors(self):
        """RSA 분석 사이클 시작 전 오류 목록 초기화"""
        self.errors = []

    def _load_gemini_key(self):
        config_path = os.path.join(workspace_root, "config", "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                key = config.get("api_settings", {}).get("gemini_api_key", "")
                if key and "YOUR_GEMINI" not in key:
                    return key
            except Exception as e:
                print(f"[NSAA] 설정 파일 로드 오류: {e}")
        return ""

    def crawl_news_headlines(self, code):
        """네이버 모바일 주식 API로 종목 뉴스 헤드라인 수집"""
        # 네이버 모바일 주식 뉴스 JSON API (JS 렌더링 불필요)
        url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize=15&page=1"
        mobile_headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'
        }
        try:
            res = requests.get(url, headers=mobile_headers, timeout=7)
            if res.status_code == 200:
                data = res.json()
                # 응답 구조: [{"total":N,"items":[...]}] 또는 {"items":[...]} 모두 처리
                if isinstance(data, list) and data:
                    items = data[0].get("items", [])
                elif isinstance(data, dict):
                    items = data.get("items", data.get("result", []))
                else:
                    items = []
                headlines = []
                seen = set()
                for item in items:
                    title = item.get("title", "").strip()
                    if title and title not in seen:
                        headlines.append(title)
                        seen.add(title)
                if headlines:
                    return headlines[:10]
        except Exception as e:
            print(f"[NSAA 모바일 API 오류] {e}")

        # 폴백: 네이버 금융 메인 페이지에서 관련 뉴스 추출
        fallback_url = f"https://finance.naver.com/item/main.naver?code={code}"
        try:
            res2 = requests.get(fallback_url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(res2.content, "html.parser")
            headlines = []
            seen = set()
            for a in soup.select("ul.newsList li a, .section_news a, td.title a"):
                t = a.text.strip()
                if len(t) > 10 and t not in seen:
                    headlines.append(t)
                    seen.add(t)
            return headlines[:10]
        except Exception as e2:
            print(f"[NSAA 폴백 크롤링 오류] {e2}")
            return []

    def analyze_sentiment_backup(self, headlines):
        """Gemini API 실패 시 작동하는 키워드 기반 백업 감성 분석기"""
        if not headlines:
            return 50, "수집된 뉴스가 없습니다 (중립 점수 부여)"
            
        score = 50
        pos_count = 0
        neg_count = 0
        
        for h in headlines:
            for pk in self.positive_keywords:
                if pk in h:
                    pos_count += 1
            for nk in self.negative_keywords:
                if nk in h:
                    neg_count += 1
                    
        total = pos_count + neg_count
        if total > 0:
            # 긍정 비율에 비례하여 20~90점 사이로 분배
            ratio = pos_count / total
            score = int(20 + ratio * 70)
        else:
            score = 50 # 완전 중립
            
        reason = f"백업 사전 연동 | 긍정 키워드 감지: {pos_count}건, 부정 키워드 감지: {neg_count}건"
        return score, reason

    # 우선순위 모델 목록 — 앞에서부터 시도, 실패 시 다음으로 폴백
    _GEMINI_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ]

    def analyze_sentiment(self, code, name=""):
        """
        종목 코드를 받아 실시간 뉴스를 크롤링하고
        Gemini LLM(혹은 키워드 백업)을 호출하여 0~100점의 감성 점수를 반환합니다.
        오류 발생 시 self.errors에 기록하여 RSA 완료 후 텔레그램으로 보고합니다.
        """
        label = f"{name}({code})" if name else code

        headlines = self.crawl_news_headlines(code)
        if not headlines:
            self.errors.append({
                "code": code, "name": name,
                "type": "뉴스 없음",
                "detail": "API/크롤링 모두 실패 — 중립 50점 적용"
            })
            return 50, "최근 뉴스 수집 실패 (중립 점수 적용)"

        if not self.api_key:
            self.errors.append({
                "code": code, "name": name,
                "type": "Gemini 키 없음",
                "detail": "키워드 백업 분석기 사용"
            })
            return self.analyze_sentiment_backup(headlines)

        prompt = (
            "당신은 주식 뉴스 감성 분석 전문가입니다.\n"
            "아래 종목 관련 기사 헤드라인을 읽고, 종합 투자 심리를 점수로 평가하세요.\n\n"
            "헤드라인 목록:\n" + "\n".join([f"- {h}" for h in headlines]) + "\n\n"
            "반드시 아래 형식의 단일 JSON 객체(배열 {} 형식, [] 아님)로만 응답하세요:\n"
            "{\"score\": <0~100 정수. 50=중립, 80이상=강한호재, 20이하=강한악재>, "
            "\"reason\": \"<핵심 이유 한 문장 (한국어)>\"}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }

        last_error = ""
        quota_exceeded = False

        for model in self._GEMINI_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
            try:
                res = requests.post(url, json=payload,
                                    headers={'Content-Type': 'application/json'}, timeout=15)
                if res.status_code == 200:
                    text_response = res.json()['candidates'][0]['content']['parts'][0]['text']
                    parsed = json.loads(text_response.strip())
                    if isinstance(parsed, list):
                        parsed = parsed[0]
                    score = max(0, min(100, int(parsed.get("score", 50))))
                    reason = parsed.get("reason", "Gemini 분석 완료")
                    print(f"  [NSAA] {model} 완료: {label} → {score}점")
                    return score, f"Gemini({model}) | {reason}"

                elif res.status_code == 429:
                    quota_exceeded = True
                    last_error = f"{model} quota 초과(429)"
                    print(f"  [NSAA] {last_error} — 다음 모델 시도")
                    continue
                else:
                    last_error = f"{model} HTTP {res.status_code}"
                    print(f"  [NSAA] {last_error} — 다음 모델 시도")
                    continue

            except Exception as e:
                last_error = f"{model} 예외: {e}"
                print(f"  [NSAA] {last_error} — 다음 모델 시도")
                continue

        # 모든 Gemini 모델 실패 → 키워드 백업 + 오류 기록
        error_type = "Gemini quota 초과" if quota_exceeded else "Gemini 연결 실패"
        self.errors.append({
            "code": code, "name": name,
            "type": error_type,
            "detail": f"키워드 백업 사용 (마지막 오류: {last_error})"
        })
        print(f"  [NSAA] {label} 모든 Gemini 실패 → 키워드 백업 적용")
        return self.analyze_sentiment_backup(headlines)

if __name__ == "__main__":
    agent = NewsSentimentAnalystAgent()
    # 삼성전자(005930) 테스트
    score, reason = agent.analyze_sentiment("005930")
    print(f"[NSAA Test] 삼성전자 뉴스감성 점수: {score} | 사유: {reason}")
