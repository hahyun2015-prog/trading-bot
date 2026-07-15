import os
import sys

# 윈도우 환경에서 이모지 출력 시 CP949 인코딩 에러 방지
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import json
import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
from bs4 import BeautifulSoup
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(current_dir)
sys.path.append(workspace_root)

from nsaa_sentiment import NewsSentimentAnalystAgent

class ClosingPriceScanner:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        self.db_path = os.path.join(workspace_root, "unified_data.db")
        self.nsaa = NewsSentimentAnalystAgent()
        # 네이버 금융 요청 시 일시적 타임아웃/5xx 오류를 가볍게 자동 재시도 (총 2회, 지수 백오프)
        # — RSA 워커 기동 직후 타임아웃으로 분석이 디폴트 점수로 우회되던 문제 보완 (2026-07-01)
        self.session = requests.Session()
        _retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=_retry))
        self.session.mount("http://", HTTPAdapter(max_retries=_retry))

    def fetch_daily_ohlcv(self, code, pages=6):
        """네이버 금융 일별 시세에서 최근 N페이지(약 N*10일) 일봉 데이터 조회"""
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
        ohlcv_list = []
        try:
            for page in range(1, pages + 1):
                page_url = f"{url}&page={page}"
                res = self.session.get(page_url, headers=self.headers, timeout=5)
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
            print(f"[Scanner] {code} 일봉 데이터 조회 실패: {e}")
            return []

    def fetch_investor_flow(self, code, days=3):
        """네이버 금융 일별 기관/외국인 순매매 데이터에서 최근 N영업일 합산 수급 조회"""
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        try:
            res = self.session.get(url, headers=self.headers, timeout=5)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, "html.parser")
            tables = soup.select("table.type2")
            if len(tables) < 2:
                return None
            rows = tables[-1].select("tr")
            inst_sum, frgn_sum, counted = 0, 0, 0
            for row in rows:
                tds = row.select("td")
                if len(tds) < 9:
                    continue
                date_str = tds[0].get_text(strip=True)
                if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_str):
                    continue
                try:
                    inst = int(tds[5].get_text(strip=True).replace(",", "").replace("+", ""))
                    frgn = int(tds[6].get_text(strip=True).replace(",", "").replace("+", ""))
                except ValueError:
                    continue
                inst_sum += inst
                frgn_sum += frgn
                counted += 1
                if counted >= days:
                    break
            if counted == 0:
                return None
            return {'institutional': inst_sum, 'foreign': frgn_sum, 'days': counted}
        except Exception as e:
            print(f"[Scanner] {code} 수급 데이터 조회 실패: {e}")
            return None

    def check_market_regime(self):
        """코스닥(KOSDAQ) 지수가 5일 이동평균선 위에 있는지 확인 (시황 필터)"""
        # 네이버 금융에서 코스닥 지수 수집
        url = "https://finance.naver.com/sise/sise_index.naver?code=KOSD"
        try:
            res = self.session.get(url, headers=self.headers, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")

            # 현재 지수
            now_val_tag = soup.select_one("#now_value")
            if not now_val_tag:
                return True # 파싱 실패 시 안전 통과
            val_text = now_val_tag.text.strip().replace(",", "")
            if not val_text:
                return True # 빈값인 경우 안전 통과
            current_index = float(val_text)
            
            # 일별 지수 (최근 5일간 종가 파악)
            # iframe sise_index_day.naver 조회
            iframe_url = "https://finance.naver.com/sise/sise_index_day.naver?code=KOSD&page=1"
            res_day = self.session.get(iframe_url, headers=self.headers, timeout=5)
            soup_day = BeautifulSoup(res_day.content, "html.parser")
            
            closes = []
            rows = soup_day.select("td.date")
            for r in rows:
                p_tr = r.parent
                tds = p_tr.select("td")
                if len(tds) >= 2:
                    val_text_day = tds[1].text.strip().replace(",", "")
                    if val_text_day:
                        closes.append(float(val_text_day))
                    
            if len(closes) >= 5:
                ma_5 = sum(closes[:5]) / 5
                is_above = current_index >= ma_5
                print(f"[시황 필터] 코스닥 현재: {current_index:.2f} / 5MA: {ma_5:.2f} ➡️ {'🟢 매수허용' if is_above else '🔴 매수보류'}")
                return is_above
        except Exception as e:
            print(f"[시황 필터] 코스닥 지수 수집 실패: {e}")
        return True

    def scan_closing_price_stocks(self):
        """거래대금 상위 50 종목 중 신접갈거조재 필터를 만족하는 종목 스캔"""
        print("[Scanner] 종가베팅 종목 스캔을 시작합니다...")
        
        # 0. 시황 필터 체크
        if not self.check_market_regime():
            print("[Scanner] 시황 필터 미통과로 인해 종가베팅을 보류합니다.")
            return []

        # 1. 오늘 top_volume_theme 종목 로드
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT code, name FROM top_volume_theme WHERE date = ?", (today,))
        candidates = cursor.fetchall()
        conn.close()

        if not candidates:
            print("[Scanner] 오늘 등록된 거래대금 상위 종목이 없습니다.")
            return []

        passed_stocks = []
        for code, name in candidates:
            # 일봉 60일치 확보
            df = self.fetch_daily_ohlcv(code, pages=6)
            if len(df) < 25:
                continue

            # 전일자까지의 가격 데이터와 오늘(마지막 인덱스) 데이터 분리
            today_bar = df[-1]
            prev_bars = df[:-1]

            close = today_bar['close']
            open_p = today_bar['open']
            high = today_bar['high']
            low = today_bar['low']
            vol = today_bar['volume']

            # 양봉이 아닐 시 스킵 (갈 - 깔끔한 양봉)
            if close <= open_p:
                continue

            # 1. 신 (신고가 영역) - 최근 60일(prev_bars[-60:]) 최고가 대비 2% 이내
            recent_high = max(b['high'] for b in prev_bars[-60:])
            is_new_high = close >= recent_high * 0.98

            # 2. 접 (좁은 이격 및 윗꼬리)
            # 윗꼬리가 전체 몸통의 35% 미만일 것 (기존 10%에서 완화)
            body = close - open_p
            upper_shadow = high - close
            is_clean_candle = body > 0 and (upper_shadow / body) < 0.35

            # 5일 이동평균선(5MA)과의 이격 체크 (오늘 종가 포함 5일 평균)
            closes_5 = [b['close'] for b in df[-5:]]
            ma_5 = sum(closes_5) / 5
            is_near_ma5 = 1.00 <= (close / ma_5) <= 1.06

            # 3. 거 (거래량 폭발)
            # 최근 20일 평균 거래량 대비 당일 거래량이 2.0배 이상 (13개월/93종목 백테스트로
            # 검증: 2.5배 기준 PF1.81/MDD7.16%/20건 -> 2.0배로 완화 시 PF2.44/MDD6.91%/30건으로
            # 거래수와 품질이 함께 개선됨 — 2.5배가 우연히 좋은 셋업까지 같이 걸러내고 있었음)
            avg_vol_20 = sum(b['volume'] for b in prev_bars[-20:]) / 20
            is_volume_spike = vol >= avg_vol_20 * 2.0

            # 4. 조 (조정 기간 검증)
            # 돌파 직전 20일간 최고가와 최저가 진폭이 수축 상태였는지 체크 (기존 60일 35%에서 20일 30%로 조정)
            prev_high_20 = max(b['high'] for b in prev_bars[-20:])
            prev_low_20 = min(b['low'] for b in prev_bars[-20:])
            if prev_low_20 > 0:
                is_consolidated = (prev_high_20 - prev_low_20) / prev_low_20 <= 0.30
            else:
                is_consolidated = False

            # [Phase 2] VWAP 20일선 필터 — 세력 매집 원가 위에서 거래되는지 확인
            # VWAP = 누적(거래량*종가) / 누적(거래량)으로 근사 계산
            vwap_data = df[-20:]  # 최근 20일
            if len(vwap_data) >= 10:
                sum_vol_price = sum(d['close'] * d['volume'] for d in vwap_data)
                sum_vol = sum(d['volume'] for d in vwap_data)
                vwap_20 = sum_vol_price / sum_vol if sum_vol > 0 else close
                is_above_vwap = close >= vwap_20
            else:
                is_above_vwap = True  # 데이터 부족 시 안전 통과
                vwap_20 = close

            if is_new_high and is_clean_candle and is_near_ma5 and is_volume_spike and is_consolidated and is_above_vwap:
                # [Phase 3] 기관/외국인 수급 역추적 필터 — 국내 개인투자자는 코스닥에서 기관·외국인과
                # 반대 방향으로 매매하며 손실을 보는 경향이 실증연구로 확인됨(거래대금 상위 종목은
                # 개인 군중 추격 신호일 수 있음). 최근 3영업일 기관+외국인 합산 순매매가 양(+)인,
                # 즉 "스마트머니"가 동반 매수 중인 종목만 통과시켜 군중매매 단독 베팅을 걸러냄.
                flow = self.fetch_investor_flow(code, days=3)
                if flow is None:
                    is_smart_money_buying = True  # 데이터 조회 실패 시 안전 통과 (필터 차단으로 인한 기회 손실 방지)
                else:
                    net_flow = flow['institutional'] + flow['foreign']
                    is_smart_money_buying = net_flow > 0
                    print(f"   => [수급 필터] {name}({code}) 최근{flow['days']}일 기관+외국인 순매매: {net_flow:+,}주 "
                          f"({'🟢 동반매수' if is_smart_money_buying else '🔴 미동반'})")

                if not is_smart_money_buying:
                    print(f"   => 🔴 [탈락] {name}({code}) 기관/외국인 수급 미동반 — 개인 군중 단독 베팅 가능성")
                    continue

                print(f"[후보 선정] {name}({code}) 조건 통과! 재료 검증을 위해 NSAA 호출...")

                # 5. 재 (재료 - 뉴스 감성 점수 70점 이상으로 완화)
                nsaa_score, nsaa_reason = self.nsaa.analyze_sentiment(code, name)
                if nsaa_score >= 70:  # 70점 이상 시 통과
                    print(f"   => 🟢 [최종 승인] {name}({code}) | 뉴스 점수: {nsaa_score}점 | {nsaa_reason}")
                    passed_stocks.append({
                        'code': code,
                        'name': name,
                        'price': close,
                        'nsaa_score': nsaa_score,
                        'reason': nsaa_reason
                    })
                else:
                    print(f"   => 🔴 [탈락] {name}({code}) 뉴스 점수 미달 ({nsaa_score}점)")

        return passed_stocks

    def save_signals(self, passed_stocks):
        """검출된 종가베팅 대상 종목들을 signals 테이블에 PENDING 상태로 적재"""
        if not passed_stocks:
            return
            
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            cursor = conn.cursor()
            # signals 테이블 생성 (미존재 시)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT,
                    name TEXT,
                    strategy_type TEXT,
                    price REAL,
                    open_price REAL,
                    status TEXT DEFAULT 'PENDING',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            for item in passed_stocks:
                # 당일 중복 저장 방지
                cursor.execute("""
                    SELECT COUNT(*) FROM signals 
                    WHERE code = ? AND strategy_type = 'DAY_CLOSE' 
                      AND date(timestamp) = date('now', 'localtime')
                """, (item['code'],))
                if cursor.fetchone()[0] == 0:
                    cursor.execute("""
                        INSERT INTO signals (code, name, strategy_type, price, open_price, status)
                        VALUES (?, ?, 'DAY_CLOSE', ?, ?, 'PENDING')
                    """, (item['code'], item['name'], item['price'], item['price']))
            conn.commit()
            conn.close()
            print(f"[Scanner] 종가베팅 신호 {len(passed_stocks)}개 적재 완료.")
        except Exception as e:
            print(f"[Scanner] 신호 저장 에러: {e}")

if __name__ == "__main__":
    scanner = ClosingPriceScanner()
    stocks = scanner.scan_closing_price_stocks()
    scanner.save_signals(stocks)
