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
        
        # 백업 감성 사전 정의
        self.positive_keywords = ["상승", "호재", "수주", "계약", "돌파", "실적개선", "흑자전환", "신고가", "수혜", "인수", "파트너십", "최대실적", "협력", "개발", "체결", "FDA", "승인"]
        self.negative_keywords = ["하락", "악재", "적자", "과징금", "횡령", "소송", "유상증자", "상장폐지", "불성실", "취소", "우려", "둔화", "계약해지", "급락", "분쟁", "경고", "피소"]

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
        """네이버 금융 종목 뉴스 헤드라인 수집"""
        url = f"https://finance.naver.com/item/news_news.naver?code={code}"
        headlines = []
        try:
            res = requests.get(url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")
            
            # 뉴스 타이틀 파싱 (td.title a)
            title_anchors = soup.select("td.title a")
            for a in title_anchors:
                title = a.text.strip()
                if title:
                    headlines.append(title)
                    
            # 중복 제거 및 최근 10개만 유지
            seen = set()
            unique_headlines = []
            for h in headlines:
                if h not in seen:
                    seen.add(h)
                    unique_headlines.append(h)
            return unique_headlines[:10]
            
        except Exception as e:
            print(f"[NSAA 크롤링 에러] {e}")
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

    def analyze_sentiment(self, code):
        """
        종목 코드를 받아 실시간 뉴스를 크롤링하고
        Gemini LLM(혹은 백업 사전)을 호출하여 0~100점의 감성 점수를 반환합니다.
        """
        headlines = self.crawl_news_headlines(code)
        if not headlines:
            return 50, "최근 뉴스가 존재하지 않습니다."
            
        # Gemini API 키가 없을 경우 백업 연동
        if not self.api_key:
            return self.analyze_sentiment_backup(headlines)
            
        # Gemini API REST 호출
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
        
        prompt = (
            "당신은 금융 감성 분석가입니다. 아래 제공된 특정 종목의 최근 기사 헤드라인 10개를 기반으로 "
            "해당 종목에 대한 종합적인 뉴스 감성 지수(Sentiment Score)를 산출해 주세요.\n\n"
            "기사 목록:\n" + "\n".join([f"- {h}" for h in headlines]) + "\n\n"
            "출력은 반드시 다음 스키마를 가진 단일 JSON 객체여야 합니다:\n"
            "{\n"
            "  \"score\": 0에서 100 사이의 정수 (50은 중립, 100은 극도의 호재, 0은 극도의 악재),\n"
            "  \"reason\": 감성 점수를 산출한 핵심적인 이유와 요약 설명 (한국어 1문장)\n"
            "}"
        )
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        try:
            res = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=7)
            if res.status_code == 200:
                res_data = res.json()
                text_response = res_data['candidates'][0]['content']['parts'][0]['text']
                parsed = json.loads(text_response.strip())
                
                score = int(parsed.get("score", 50))
                reason = parsed.get("reason", "Gemini 감성 분석 완료")
                return score, f"Gemini Flash 연동 | {reason}"
            else:
                print(f"[NSAA] Gemini API 오류 (HTTP {res.status_code}), 백업 분석기로 전환합니다.")
                return self.analyze_sentiment_backup(headlines)
        except Exception as e:
            print(f"[NSAA] Gemini API 연동 예외 ({e}), 백업 분석기로 전환합니다.")
            return self.analyze_sentiment_backup(headlines)

if __name__ == "__main__":
    agent = NewsSentimentAnalystAgent()
    # 삼성전자(005930) 테스트
    score, reason = agent.analyze_sentiment("005930")
    print(f"[NSAA Test] 삼성전자 뉴스감성 점수: {score} | 사유: {reason}")
