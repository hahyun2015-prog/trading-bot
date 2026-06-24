import os
import sys

# 윈도우 환경에서 이모지 출력 시 CP949 인코딩 에러 방지
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import sqlite3
import requests
import re
from bs4 import BeautifulSoup
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(current_dir)
sys.path.append(workspace_root)

class LeaderDetector:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        self.db_path = os.path.join(workspace_root, "unified_data.db")
        self.exclude_keywords = [
            "KODEX", "TIGER", "KBSTAR", "KINDEX", "KOSEF", "HANARO", "ARIRANG", "TREX", "SOL", "ACE", "RISE",
            "선물", "인버스", "레버리지", "ETN", "우선주", "우", "스팩", "SPAC", "합병"
        ]

    def is_excluded(self, name):
        return any(kw in name for kw in self.exclude_keywords) or name.endswith("우") or name.endswith("우B")

    def get_tick_size(self, price):
        if price < 2000:
            return 1
        elif price < 5000:
            return 5
        elif price < 10000:
            return 10
        elif price < 50000:
            return 50
        elif price < 100000:
            return 100
        elif price < 500000:
            return 500
        else:
            return 1000

    def calculate_limit_up(self, prev_close):
        raw_limit_up = prev_close * 1.30
        temp = int(raw_limit_up)
        tick = self.get_tick_size(temp)
        limit_up = (temp // tick) * tick
        return limit_up

    def fetch_daily_ohlcv(self, code, pages=13):
        """네이버 금융 일별 시세에서 최근 N페이지(약 N*10일) 일봉 데이터 조회"""
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
        ohlcv_list = []
        try:
            for page in range(1, pages + 1):
                page_url = f"{url}&page={page}"
                res = requests.get(page_url, headers=self.headers, timeout=5)
                soup = BeautifulSoup(res.content, "html.parser")
                rows = soup.select("table.type2 tr")
                for row in rows:
                    tds = row.select("td")
                    if len(tds) < 7:
                        continue
                    date_str = tds[0].text.strip()
                    if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_str):
                        continue
                    
                    close = int(tds[1].text.strip().replace(",", ""))
                    open_p = int(tds[3].text.strip().replace(",", ""))
                    high = int(tds[4].text.strip().replace(",", ""))
                    low = int(tds[5].text.strip().replace(",", ""))
                    vol = int(tds[6].text.strip().replace(",", ""))
                    
                    ohlcv_list.append({
                        'date': date_str,
                        'open': open_p,
                        'high': high,
                        'low': low,
                        'close': close,
                        'volume': vol
                    })
            # 날짜 오름차순 정렬
            ohlcv_list.reverse()
            return ohlcv_list
        except Exception as e:
            print(f"[LeaderDetector] {code} 일봉 데이터 조회 실패: {e}")
            return []

    def get_top_rising_stocks(self, count=30):
        """네이버 금융에서 당일 등락률 상위 종목 조회 (KOSPI & KOSDAQ 통합)"""
        rising_stocks = []
        try:
            res_kospi = requests.get("https://finance.naver.com/sise/sise_rise.naver?sosok=0", headers=self.headers, timeout=5)
            soup_kospi = BeautifulSoup(res_kospi.content, "html.parser")
            
            res_kosdaq = requests.get("https://finance.naver.com/sise/sise_rise.naver?sosok=1", headers=self.headers, timeout=5)
            soup_kosdaq = BeautifulSoup(res_kosdaq.content, "html.parser")
            
            for soup in [soup_kospi, soup_kosdaq]:
                rows = soup.select("table.type_2 tr")
                for row in rows:
                    tds = row.select("td")
                    if len(tds) < 12:
                        continue
                    a = tds[1].select_one("a")
                    if not a:
                        continue
                    name = a.text.strip()
                    if self.is_excluded(name):
                        continue
                        
                    code = a["href"].split("code=")[1]
                    
                    change_tag = tds[4].select_one("span")
                    if not change_tag:
                        continue
                    change_str = change_tag.text.strip().replace("%", "").replace("+", "").replace("-", "").strip()
                    try:
                        change_val = float(change_str)
                        if "-" in change_tag.text:
                            change_val = -change_val
                    except ValueError:
                        continue
                        
                    vol_str = tds[5].text.strip().replace(",", "")
                    try:
                        volume = int(vol_str)
                    except ValueError:
                        volume = 0
                        
                    rising_stocks.append({
                        "code": code,
                        "name": name,
                        "change": change_val,
                        "volume": volume
                    })
            
            rising_stocks = sorted(rising_stocks, key=lambda x: x["change"], reverse=True)
            return rising_stocks[:count]
        except Exception as e:
            print(f"[LeaderDetector] 상승 종목 획득 에러: {e}")
            return []

    def get_top_themes(self, count=15):
        """당일 네이버 테마 랭킹에서 거래대금/등락률 강세 상위 N개 테마 조회"""
        themes = []
        try:
            url = "https://finance.naver.com/sise/theme.naver"
            res = requests.get(url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")
            
            rows = soup.select("table.type_1 tr")
            for row in rows:
                a = row.select_one("td.col_type1 a")
                if not a:
                    continue
                theme_name = a.text.strip()
                theme_url = "https://finance.naver.com" + a["href"]
                
                change_tag = row.select_one("td.col_type2 span")
                theme_change = 0.0
                if change_tag:
                    try:
                        theme_change = float(change_tag.text.strip().replace("%", "").replace("+", ""))
                    except ValueError:
                        pass
                
                themes.append({
                    "name": theme_name,
                    "url": theme_url,
                    "change": theme_change
                })
            themes = sorted(themes, key=lambda x: x["change"], reverse=True)
            return themes[:count]
        except Exception as e:
            print(f"[LeaderDetector] 테마 랭킹 조회 실패: {e}")
            return []

    def get_theme_stocks(self, theme_url):
        """특정 테마에 속한 모든 종목 코드 조회"""
        stocks = []
        try:
            res = requests.get(theme_url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")
            rows = soup.select("table.type_5 tbody tr")
            for row in rows:
                a = row.select_one("td.name a")
                if not a:
                    continue
                sname = a.text.strip()
                if self.is_excluded(sname):
                    continue
                scode = a["href"].split("code=")[1]
                stocks.append({
                    "code": scode,
                    "name": sname
                })
        except Exception as e:
            print(f"[LeaderDetector] 테마 상세 조회 실패 ({theme_url}): {e}")
        return stocks

    def detect_leader(self):
        """주도 테마와 1등 대장주 검출 및 관련 가격 정보 계산"""
        print("[LeaderDetector] 주도 테마 및 대장주 검출 프로세스 시작...")
        
        rising_stocks = self.get_top_rising_stocks(30)
        if not rising_stocks:
            print("[LeaderDetector] 상승 종목이 존재하지 않습니다.")
            return None
            
        rising_map = {s["code"]: s for s in rising_stocks}
        themes = self.get_top_themes(15)
        if not themes:
            print("[LeaderDetector] 활성 테마가 존재하지 않습니다.")
            return None
            
        theme_scores = []
        for theme in themes:
            stocks = self.get_theme_stocks(theme["url"])
            if not stocks:
                continue
                
            matching_stocks = []
            score = 0.0
            for s in stocks:
                if s["code"] in rising_map:
                    rising_info = rising_map[s["code"]]
                    matching_stocks.append(rising_info)
                    score += rising_info["change"]
                    
            if matching_stocks:
                matching_stocks = sorted(matching_stocks, key=lambda x: x["change"], reverse=True)
                theme_scores.append({
                    "theme_name": theme["name"],
                    "score": score,
                    "stocks": matching_stocks,
                    "leader_candidate": matching_stocks[0]
                })
                
        if not theme_scores:
            print("[LeaderDetector] 상승 상위 종목에 매칭되는 테마가 없습니다.")
            return None
            
        theme_scores = sorted(theme_scores, key=lambda x: x["score"], reverse=True)
        best_theme = theme_scores[0]
        leader_stock = best_theme["leader_candidate"]
        
        # 추가 가격 정보 연산 (6개월 신고가 및 상한가)
        code = leader_stock["code"]
        ohlcv = self.fetch_daily_ohlcv(code, pages=13)
        if len(ohlcv) < 10:
            print(f"[LeaderDetector] {leader_stock['name']}({code}) 일봉 데이터 부족으로 가격 정보 계산 불가.")
            return None
            
        # 장중일 때는 마지막 데이터가 오늘 데이터이므로 제외하고 이전 데이터 기준 6개월 고가와 전일 종가 구비
        # 장 시작 직후(09:30)에는 Naver 일별 시세 첫 번째 행이 오늘 시초가/현재가 데이터임
        today_bar = ohlcv[-1]
        prev_bars = ohlcv[:-1]
        
        prev_close = prev_bars[-1]["close"]
        six_month_high = max(b["high"] for b in prev_bars[-120:]) # 120영업일 (약 6개월)
        limit_up = self.calculate_limit_up(prev_close)
        
        print(f"[LeaderDetector] 주도 테마: {best_theme['theme_name']} (점수: {best_theme['score']:.2f})")
        print(f"[LeaderDetector] 대장주: {leader_stock['name']}({code}) | 등락률: {leader_stock['change']}%")
        print(f"[LeaderDetector] 전일종가: {prev_close:,}원 | 6개월신고가선: {six_month_high:,}원 | 상한가: {limit_up:,}원")
        
        return {
            "theme_name": best_theme["theme_name"],
            "score": best_theme["score"],
            "code": code,
            "name": leader_stock["name"],
            "change": leader_stock["change"],
            "prev_close": prev_close,
            "six_month_high": six_month_high,
            "limit_up": limit_up
        }

    def save_leader_signal(self, leader_info):
        """검출된 대장주 정보를 sector_leaders 테이블에 기록"""
        if not leader_info:
            return
            
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sector_leaders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    code TEXT,
                    name TEXT,
                    theme_name TEXT,
                    score REAL,
                    prev_close REAL,
                    six_month_high REAL,
                    limit_up REAL,
                    timestamp DATETIME DEFAULT (datetime('now', 'localtime'))
                )
            """)
            
            today = datetime.now().strftime("%Y-%m-%d")
            
            # 동일 날짜 중복 기록 방지
            cursor.execute("""
                SELECT COUNT(*) FROM sector_leaders 
                WHERE date = ? AND code = ?
            """, (today, leader_info["code"]))
            
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO sector_leaders (date, code, name, theme_name, score, prev_close, six_month_high, limit_up)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (today, leader_info["code"], leader_info["name"], leader_info["theme_name"], leader_info["score"],
                      leader_info["prev_close"], leader_info["six_month_high"], leader_info["limit_up"]))
                conn.commit()
                print(f"[LeaderDetector] {leader_info['name']}({leader_info['code']}) 대장주 신호 저장 완료.")
            else:
                print(f"[LeaderDetector] {leader_info['name']}({leader_info['code']}) 오늘 이미 등록된 대장주입니다.")
            conn.close()
        except Exception as e:
            print(f"[LeaderDetector] DB 저장 실패: {e}")

if __name__ == "__main__":
    detector = LeaderDetector()
    leader = detector.detect_leader()
    if leader:
        detector.save_leader_signal(leader)
