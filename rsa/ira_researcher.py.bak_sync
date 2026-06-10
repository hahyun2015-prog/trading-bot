import os
import sys
import requests
import re
from bs4 import BeautifulSoup

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

class IndustryResearcherAgent:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # 현재 시장 주도/성장 사이클 업종 키워드 및 가점
        self.growth_sectors = {
            "반도체": 20,
            "전기제품": 20, # 이차전지, 변압기 등 전력설비 포함
            "조선": 15,
            "우주항공": 15,
            "디스플레이": 10,
            "자동차": 10,
            "컴퓨터": 15,  # AI 관련 서버, 하드웨어
            "소프트웨어": 15, # AI 솔루션
            "제약": 10,    # 바이오 헬스케어
            "바이오": 15,
            "방산": 15,
            "기계": 10
        }

    def analyze_industry(self, code):
        """
        네이버 금융에서 종목의 업종 정보를 파싱하여 섹터 성장성 및 사이클 점수를 평가합니다.
        반환값: (industry_score, reason_summary)
        """
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        try:
            res = requests.get(url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")
            
            # 종목 분석 페이지에서 업종명 추출하기
            # 우측 "동일업종 비교" 또는 상단 업종 링크 탐색
            industry_anchor = None
            
            # 1. 동일업종 비교 테이블 근처의 업종명 찾기
            compare_div = soup.select_one("div.section.trade_compare")
            if compare_div:
                title_tag = compare_div.select_one("h4.h_sub em a")
                if title_tag:
                    industry_anchor = title_tag
            
            # 2. 대안 탐색 (페이지 내 동일업종명 검색)
            if not industry_anchor:
                for a in soup.select("a"):
                    if "sameAreaLnk" in a.get("class", []):
                        industry_anchor = a
                        break
            
            if not industry_anchor:
                # 3. 텍스트 검색 기반
                text_match = re.search(r"동일업종\s+-\s+([^<\n]+)", soup.text)
                if text_match:
                    industry_name = text_match.group(1).strip()
                else:
                    return 75, "동일업종 파싱 실패 (평균 매크로 점수 부여)"
            else:
                industry_name = industry_anchor.text.strip()
                
            # 등락률 파싱 (동일업종 등락률)
            change_rate = 0.0
            if compare_div:
                change_span = compare_div.select_one("h4.h_sub em span")
                if change_span:
                    change_text = change_span.text.strip().replace("%", "").replace("+", "")
                    try:
                        change_rate = float(change_text)
                    except ValueError:
                        pass
            
            # 기본 점수 설정
            base_score = 75
            bonus = 0
            reasons = []
            
            # 성장 사이클 매칭 검사
            matched = False
            for sector, val in self.growth_sectors.items():
                if sector in industry_name:
                    bonus += val
                    reasons.append(f"주도 섹터 성장 가점({sector} +{val}점)")
                    matched = True
                    break
                    
            if not matched:
                reasons.append("일반 경기순환 업종")
                
            # 업종 당일 등락률 반영
            if change_rate > 2.0:
                bonus += 10
                reasons.append(f"업종 당일 강세(+{change_rate:.1f}%)")
            elif change_rate > 0.0:
                bonus += 5
                reasons.append(f"업종 당일 수급 호조(+{change_rate:.1f}%)")
            elif change_rate < -2.0:
                bonus -= 10
                reasons.append(f"업종 당일 약세 감점({change_rate:.1f}%)")
                
            final_score = min(100, max(30, base_score + bonus))
            reason_summary = f"업종: [{industry_name}] | {', '.join(reasons)}"
            
            return final_score, reason_summary
            
        except Exception as e:
            return 70, f"업종 분석 오류: {e} (디폴트 업종 점수 부여)"

if __name__ == "__main__":
    agent = IndustryResearcherAgent()
    # 삼성전자(005930) 테스트
    score, reason = agent.analyze_industry("005930")
    print(f"[IRA Test] 삼성전자 업종점수: {score} | 사유: {reason}")
    
    # 현대차(005380) 테스트
    score2, reason2 = agent.analyze_industry("005380")
    print(f"[IRA Test] 현대차 업종점수: {score2} | 사유: {reason2}")
