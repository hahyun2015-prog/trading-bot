import os
import sys
import sqlite3
import requests
from bs4 import BeautifulSoup

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

class FinancialAnalystAgent:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def analyze_finance(self, code):
        """
        네이버 금융에서 종목의 재무제표를 크롤링하여 재무 안전성 점수를 평가합니다.
        반환값: (safety_score, reason_summary)
        """
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        try:
            res = requests.get(url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")
            
            # cop_analysis 영역 (기업분석) 찾기
            analysis_div = soup.select_one("div.section.cop_analysis")
            if not analysis_div:
                return 80, "재무 테이블 파싱 실패 (우량주 추정 디폴트 점수 부여)"
            
            table = analysis_div.select_one("table")
            if not table:
                return 80, "재무 테이블 검색 실패"
                
            # 테이블 헤더 파싱 (연도/분기 파악)
            headers = [th.text.strip().replace("\n", "").replace("\t", "") for th in table.select("thead tr th")]
            
            # 행 데이터 파싱
            rows = table.select("tbody tr")
            finance_data = {}
            
            for row in rows:
                th_title = row.select_one("th")
                if not th_title:
                    continue
                title = th_title.text.strip()
                tds = [td.text.strip().replace(",", "") for td in row.select("td")]
                finance_data[title] = tds
                
            # 디버깅용 출력
            # print(f"[FAA Debug] 파싱된 항목: {list(finance_data.keys())}")
            
            # 주요 평가지표 추출
            # 1. 영업이익 (최근 3년 연속 적자 여부 검사)
            operating_profit = finance_data.get("영업이익", [])
            # 2. 부채비율 (최근 연도 비율 검사)
            debt_ratio = finance_data.get("부채비율", [])
            # 3. 유보율 (최근 연도 비율 검사)
            reserve_ratio = finance_data.get("유보율", [])
            
            score = 100
            deductions = []
            
            # --- 1. 영업이익 검사 ---
            if len(operating_profit) >= 3:
                try:
                    # 최근 3개년 연간 영업이익 확인 (영업이익 행의 앞쪽 3~4개 열이 연간 지표)
                    op_years = []
                    for val in operating_profit[:3]:
                        val_clean = val.replace("\xa0", "").strip()
                        if val_clean and val_clean != "-":
                            op_years.append(float(val_clean))
                    
                    # 3년 연속 적자(영업이익 < 0) 검사
                    if len(op_years) >= 3 and all(x < 0 for x in op_years):
                        score -= 40
                        deductions.append("3년 연속 영업이익 적자")
                    elif len(op_years) >= 1 and op_years[-1] < 0:
                        score -= 10
                        deductions.append("최근 연도 영업이익 적자")
                except ValueError:
                    pass
            
            # --- 2. 부채비율 검사 ---
            if debt_ratio:
                try:
                    # 가장 최근 연도/분기 부채비율 확인
                    recent_debt = None
                    for val in reversed(debt_ratio):
                        val_clean = val.replace("\xa0", "").strip()
                        if val_clean and val_clean != "-":
                            recent_debt = float(val_clean)
                            break
                    
                    if recent_debt is not None:
                        if recent_debt > 250:
                            score -= 30
                            deductions.append(f"고부채비율({recent_debt:.1f}%)")
                        elif recent_debt > 150:
                            score -= 15
                            deductions.append(f"부채비율 다소 높음({recent_debt:.1f}%)")
                except ValueError:
                    pass
                    
            # --- 3. 유보율 검사 ---
            if reserve_ratio:
                try:
                    recent_reserve = None
                    for val in reversed(reserve_ratio):
                        val_clean = val.replace("\xa0", "").strip()
                        if val_clean and val_clean != "-":
                            recent_reserve = float(val_clean)
                            break
                            
                    if recent_reserve is not None:
                        if recent_reserve < 200:
                            score -= 20
                            deductions.append(f"저유보율({recent_reserve:.1f}%)")
                        elif recent_reserve < 500:
                            score -= 10
                            deductions.append(f"유보율 다소 낮음({recent_reserve:.1f}%)")
                except ValueError:
                    pass
            
            # 최저 점수 방어
            score = max(10, score)
            
            if not deductions:
                reason = "우수한 재무 안전성 (영업이익 흑자 및 안정적 재무 상태)"
            else:
                reason = f"감점요인: {', '.join(deductions)}"
                
            return score, reason
            
        except Exception as e:
            return 75, f"재무 분석 오류: {e} (기본 안전 점수 우회)"

if __name__ == "__main__":
    agent = FinancialAnalystAgent()
    # 삼성전자(005930) 테스트
    score, reason = agent.analyze_finance("005930")
    print(f"[FAA Test] 삼성전자 재무안전 점수: {score} | 사유: {reason}")
    # 재무부실주 임의 테스트 (예: 251970 - 예시)
