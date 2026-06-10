# -*- coding: utf-8 -*-
"""
NSAA Gemini AI 실증 분석
========================
뉴스 데이터 가용 기간(최근 4~5일)에 집중하여:
  1. NSAA 실제 점수 산출 (Gemini AI)
  2. 5일 모멘텀 방향과 NSAA 방향 비교
  3. NSAA가 포착한 이벤트 기반 신호 실증
  4. 앞으로 NSAA 일일 수집 자동화 설계
"""
import sys, io, sqlite3, json, time, requests, os
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_PATH = os.path.join(workspace_root, "unified_data.db")

def load_gemini_key():
    try:
        with open(os.path.join(workspace_root, "config", "config.json"), encoding='utf-8') as f:
            cfg = json.load(f)
        k = cfg.get('api_settings', {}).get('gemini_api_key', '')
        return k if k and 'YOUR_GEMINI' not in k else ''
    except: return ''

GEMINI_KEY = load_gemini_key()
GEMINI_MODELS = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.0-flash-lite']

def gemini_sentiment(headlines):
    if not GEMINI_KEY or not headlines:
        return 50, '데이터없음'
    prompt = (
        "당신은 주식 뉴스 감성 분석 전문가입니다.\n"
        "아래 기사 헤드라인들의 종합 투자 심리를 평가하세요.\n\n"
        "헤드라인:\n" + '\n'.join(f'- {h}' for h in headlines[:8]) + '\n\n'
        '반드시 단일 JSON 객체로만 응답: {"score": <0~100>, "reason": "<한문장>"}'
    )
    payload = {'contents': [{'parts': [{'text': prompt}]}],
               'generationConfig': {'responseMimeType': 'application/json'}}
    for model in GEMINI_MODELS:
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}'
        try:
            r = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=15)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                parsed = json.loads(text.strip())
                if isinstance(parsed, list): parsed = parsed[0]
                return max(0, min(100, int(parsed.get('score', 50)))), parsed.get('reason', '')
            elif r.status_code == 429:
                time.sleep(5); continue
        except: time.sleep(2); continue
    return 50, 'API실패'

# ─────────────────────────────────────────────────────────────
# 1. 뉴스 수집 (가용 범위 전체)
# ─────────────────────────────────────────────────────────────
def fetch_all_available_news(code, max_pages=100):
    headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0)'}
    news_by_date = {}
    seen = set()
    for page in range(1, max_pages + 1):
        url = f'https://m.stock.naver.com/api/news/stock/{code}?pageSize=20&page={page}'
        try:
            data = requests.get(url, headers=headers, timeout=8).json()
            items = data[0].get('items', []) if isinstance(data, list) and data else \
                    data.get('items', []) if isinstance(data, dict) else []
            if not items: break
            for item in items:
                title = item.get('title', '').strip()
                dt = item.get('datetime', '')[:8]
                if title and dt and title not in seen:
                    news_by_date.setdefault(dt, []).append(title)
                    seen.add(title)
        except: break
    return news_by_date

# ─────────────────────────────────────────────────────────────
# 2. 날짜별 NSAA 점수 산출
# ─────────────────────────────────────────────────────────────
def analyze_dates(code, name, news_by_date, long_min):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute('''CREATE TABLE IF NOT EXISTS nsaa_cache
                    (code TEXT, date TEXT, score INTEGER, reason TEXT,
                     PRIMARY KEY (code, date))''')
    conn.commit()

    results = []
    dates = sorted(news_by_date.keys())
    print(f'\n  [{name}] {len(dates)}개 날짜 NSAA 분석:')

    for date_str in dates:
        headlines = news_by_date[date_str][:8]
        # 캐시 확인
        cached = conn.execute('SELECT score, reason FROM nsaa_cache WHERE code=? AND date=? AND reason!=""',
                              (code, date_str)).fetchone()
        if cached:
            score, reason = cached
            src = '캐시'
        else:
            score, reason = gemini_sentiment(headlines)
            conn.execute('INSERT OR REPLACE INTO nsaa_cache VALUES(?,?,?,?)',
                         (code, date_str, score, reason))
            conn.commit()
            src = 'Gemini'
            time.sleep(4)

        direction = 'LONG' if score >= long_min else 'NEUTRAL'
        grade = '⭐⭐⭐' if score >= 80 else ('⭐⭐' if score >= 70 else '⭐')
        print(f'    {date_str}: {grade} {score}점 → {direction} ({src}) | {reason[:45]}')
        results.append({'date': date_str, 'score': score, 'direction': direction,
                        'headlines': headlines[:2], 'reason': reason})

    conn.close()
    return results

# ─────────────────────────────────────────────────────────────
# 3. 모멘텀 vs NSAA 방향 비교
# ─────────────────────────────────────────────────────────────
def compare_signals(code, name, nsaa_results):
    # 5분봉 데이터에서 일봉 종가 추출
    conn = sqlite3.connect(DB_PATH, timeout=30)
    df = pd.read_sql(f"SELECT date, close FROM isf_5min_ohlcv WHERE code='{code}' ORDER BY date", conn)
    conn.close()
    if df.empty:
        print(f'  {name}: 5분봉 데이터 없음')
        return

    df['datetime'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
    df.set_index('datetime', inplace=True)
    daily_close = df.resample('D').last()['close'].dropna()

    print(f'\n  [{name}] 모멘텀 vs NSAA 방향 비교:')
    print(f"  {'날짜':>10} {'NSAA점수':>8} {'NSAA방향':>10} {'5일모멘텀':>10} {'모멘텀방향':>10} {'일치':>6}")
    for item in nsaa_results:
        date_str = item['date']
        try:
            dt = datetime.strptime(date_str, '%Y%m%d')
            past = daily_close[daily_close.index.date <= dt.date()]
            if len(past) >= 6:
                ret5 = (past.iloc[-1] - past.iloc[-6]) / past.iloc[-6] * 100
                mom_dir = 'LONG' if ret5 >= 1.0 else 'NEUTRAL'
            else:
                ret5 = 0; mom_dir = 'N/A'
            match = '✅' if item['direction'] == mom_dir else '❌'
            print(f"  {date_str:>10} {item['score']:>8} {item['direction']:>10} "
                  f"{ret5:>+9.1f}% {mom_dir:>10} {match:>6}")
        except Exception as e:
            print(f'  {date_str}: 오류 {e}')

# ─────────────────────────────────────────────────────────────
# 4. 이벤트 기반 신호 실증 (고점수 날짜 분석)
# ─────────────────────────────────────────────────────────────
def analyze_event_days(code, name, nsaa_results, K=0.15, SL_PCT=1.5, TP_PCT=4.0):
    high_score_days = [r for r in nsaa_results if r['score'] >= 72]
    if not high_score_days:
        print(f'\n  [{name}] LONG 기준({72}점+) 달성일 없음')
        return

    conn = sqlite3.connect(DB_PATH, timeout=30)
    df_5m = pd.read_sql(f"SELECT date, open, high, low, close FROM isf_5min_ohlcv WHERE code='{code}' ORDER BY date", conn)
    conn.close()
    if df_5m.empty: return

    df_5m['dt'] = pd.to_datetime(df_5m['date'], format='%Y%m%d%H%M%S')
    df_5m.set_index('dt', inplace=True)
    df_5m['date_only'] = df_5m.index.date

    print(f'\n  [{name}] 고점수 날짜 실제 5분봉 추적:')
    for item in high_score_days:
        date_str = item['date']
        dt = datetime.strptime(date_str, '%Y%m%d').date()
        day_df = df_5m[df_5m['date_only'] == dt].sort_index()
        if day_df.empty: continue

        import datetime as _dt
        # 전날 데이터로 prev_range 계산
        prev_day = dt - timedelta(days=1)
        # 주말 건너뛰기
        while prev_day.weekday() >= 5:
            prev_day -= timedelta(days=1)
        prev_df = df_5m[df_5m['date_only'] == prev_day]
        if prev_df.empty: continue

        prev_range = prev_df['high'].max() - prev_df['low'].min()
        morning = day_df[day_df.index.time >= _dt.time(9, 5)]
        if morning.empty: continue
        day_open = float(morning.iloc[0]['open'])
        target = day_open + prev_range * K

        # 장중 추적
        entry = None
        result_type = '미진입'
        pnl = 0
        for ts, bar in day_df.iterrows():
            if bar.name.time() < _dt.time(9, 5): continue
            if bar.name.time() >= _dt.time(15, 15):
                if entry:
                    result_type = '시간청산'
                    pnl = (bar['open'] - entry) * 10
                break
            if not entry and bar['high'] >= target:
                entry = target
            elif entry:
                sl = entry * (1 - SL_PCT / 100)
                tp = entry * (1 + TP_PCT / 100)
                if bar['low'] <= sl:
                    result_type = '손절'
                    pnl = (sl - entry) * 10
                    break
                elif bar['high'] >= tp:
                    result_type = '익절'
                    pnl = (tp - entry) * 10
                    break

        pnl_str = f'{pnl:+,.0f}원' if pnl else '—'
        print(f'    {date_str}: NSAA={item["score"]}점 | 시초={day_open:,} 목표={target:,.0f} | '
              f'{result_type} {pnl_str}')
        print(f'      뉴스: {item["headlines"][0][:55] if item["headlines"] else "없음"}')

# ─────────────────────────────────────────────────────────────
# 5. 일일 NSAA 자동 수집 설계 출력
# ─────────────────────────────────────────────────────────────
def print_daily_collection_plan():
    print('\n' + '='*65)
    print('[ 앞으로 NSAA 일일 자동 수집 설계 ]')
    print('='*65)
    print('''
  문제: 네이버 뉴스 API는 최근 4~5일만 제공 → 과거 60일 불가

  해결 방향: 매일 08:50 RSA 분석 시 NSAA 점수를 DB에 저장
             → 30~60일 누적 후 실데이터 기반 재검증

  현재 구현 상태:
  ✅ rsa_coordinator.py → 매일 08:50 Gemini NSAA 분석 실행
  ✅ research_reports 테이블 → nsaa_score 컬럼 저장됨
  ✅ era_order_manager._check_isf_direction() → 저장된 점수 읽어서 방향 결정
  ✅ nsaa_cache 테이블 → 날짜별 캐시 영속화

  앞으로 일정:
  D+0  (오늘): ERA 재시작 → 오늘 NSAA 점수 저장 시작
  D+5  : 1주 데이터 누적 → 단기 유효성 확인 가능
  D+30 : 1달 데이터 → 통계적으로 의미 있는 재검증 가능
  D+60 : 완전한 NSAA 기반 백테스트 재실행 권장

  NSAA vs 모멘텀 핵심 차이:
  모멘텀: "지난 5일 주가가 +1% 이상 올랐으면 LONG"
  NSAA:   "오늘 뉴스가 AI공급망 확대 기대, 실적개선 등 호재면 LONG"
          → 가격에 선행하는 이벤트(계약, 인사, 공시) 포착 가능
    ''')

# ─────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=== NSAA Gemini AI 실증 분석 ===\n')
    if not GEMINI_KEY:
        print('Gemini 키 없음'); import sys; sys.exit(1)

    print('[뉴스 수집 중...]')
    news_ss = fetch_all_available_news('005930', max_pages=100)
    news_sk = fetch_all_available_news('000660', max_pages=100)
    print(f'  삼성전자: {sum(len(v) for v in news_ss.values())}건 ({len(news_ss)}개 날짜)')
    print(f'  SK하이닉스: {sum(len(v) for v in news_sk.values())}건 ({len(news_sk)}개 날짜)')

    print('\n[Gemini AI NSAA 점수 산출]')
    nsaa_ss = analyze_dates('005930', '삼성전자', news_ss, long_min=72)
    nsaa_sk = analyze_dates('000660', 'SK하이닉스', news_sk, long_min=80)

    print('\n' + '='*65)
    print('[ NSAA vs 5일 모멘텀 방향 비교 ]')
    print('='*65)
    compare_signals('005930', '삼성전자', nsaa_ss)
    compare_signals('000660', 'SK하이닉스', nsaa_sk)

    print('\n' + '='*65)
    print('[ 고점수 날짜 실제 거래 추적 ]')
    print('='*65)
    analyze_event_days('005930', '삼성전자', nsaa_ss)
    analyze_event_days('000660', 'SK하이닉스', nsaa_sk)

    print_daily_collection_plan()
