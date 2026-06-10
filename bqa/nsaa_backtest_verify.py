# -*- coding: utf-8 -*-
"""
NSAA 실제 Gemini AI 연동 백테스트 재검증
=========================================
5일 모멘텀 대신 실제 Gemini AI 뉴스 감성 분석(NSAA)으로 방향 결정 후
5분봉 ISF 백테스트를 재실행하여 성과를 비교합니다.

파이프라인:
  1. 네이버 모바일 API → 60일치 뉴스 헤드라인 수집 (날짜별 정렬)
  2. 각 거래일 전날까지의 최근 5개 뉴스 → Gemini Flash 감성 분석
  3. NSAA 점수 DB 캐시 (중복 API 호출 방지)
  4. 5분봉 백테스트 재실행 (NSAA 방향 필터 적용)
  5. 모멘텀 vs NSAA 결과 비교
"""
import sys
import io
import json
import sqlite3
import os
import time
import requests
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
DB_PATH = os.path.join(workspace_root, "unified_data.db")
CFG_PATH = os.path.join(workspace_root, "config", "config.json")


# ────────────────────────────────────────────────────────────────
# 설정 로드
# ────────────────────────────────────────────────────────────────
def load_gemini_key():
    try:
        with open(CFG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        key = cfg.get('api_settings', {}).get('gemini_api_key', '')
        if key and 'YOUR_GEMINI' not in key:
            return key
    except Exception:
        pass
    return ''


GEMINI_KEY = load_gemini_key()
GEMINI_MODELS = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.0-flash-lite']


# ────────────────────────────────────────────────────────────────
# 1. 네이버 모바일 뉴스 수집 (날짜별 정렬)
# ────────────────────────────────────────────────────────────────
def fetch_all_news(code, max_pages=30):
    """
    네이버 모바일 주식 뉴스 API로 최대 max_pages × 20 = 600건 수집
    반환: {날짜str(YYYYMMDD): [헤드라인1, 헤드라인2, ...]}
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'
    }
    news_by_date = defaultdict(list)
    seen = set()

    for page in range(1, max_pages + 1):
        url = f'https://m.stock.naver.com/api/news/stock/{code}?pageSize=20&page={page}'
        try:
            r = requests.get(url, headers=headers, timeout=8)
            data = r.json()
            if isinstance(data, list) and data:
                items = data[0].get('items', [])
            elif isinstance(data, dict):
                items = data.get('items', [])
            else:
                items = []

            if not items:
                break

            for item in items:
                title = item.get('title', '').strip()
                dt_str = item.get('datetime', '')   # 예: '202605291649'
                if not title or title in seen or len(dt_str) < 8:
                    continue
                seen.add(title)
                date_key = dt_str[:8]   # YYYYMMDD
                news_by_date[date_key].append(title)

        except Exception:
            break

    print(f'  [{code}] 뉴스 수집: {sum(len(v) for v in news_by_date.values())}건 '
          f'({len(news_by_date)}개 날짜)')
    return dict(news_by_date)


# ────────────────────────────────────────────────────────────────
# 2. Gemini AI 감성 분석 (캐시 포함)
# ────────────────────────────────────────────────────────────────
def _init_nsaa_cache(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS nsaa_cache
                    (code TEXT, date TEXT, score INTEGER, reason TEXT,
                     PRIMARY KEY (code, date))''')
    conn.commit()


def get_cached_nsaa(conn, code, date_str):
    c = conn.execute('SELECT score, reason FROM nsaa_cache WHERE code=? AND date=?',
                     (code, date_str))
    row = c.fetchone()
    return (row[0], row[1]) if row else None


def save_nsaa_cache(conn, code, date_str, score, reason):
    conn.execute('INSERT OR REPLACE INTO nsaa_cache (code,date,score,reason) VALUES(?,?,?,?)',
                 (code, date_str, score, reason))
    conn.commit()


def gemini_sentiment(headlines, code, date_str):
    """Gemini Flash로 뉴스 감성 점수 산출 (0~100)"""
    if not GEMINI_KEY:
        return 50, '키 없음'

    prompt = (
        "당신은 주식 뉴스 감성 분석 전문가입니다.\n"
        "아래 기사 헤드라인들을 읽고 해당 종목의 투자 심리를 평가하세요.\n\n"
        "헤드라인:\n" + '\n'.join(f'- {h}' for h in headlines[:8]) + '\n\n'
        "반드시 아래 형식의 단일 JSON 객체로만 응답하세요:\n"
        '{"score": <0~100 정수. 50=중립, 80이상=강호재, 20이하=강악재>, '
        '"reason": "<핵심 이유 한 문장 (한국어)>"}'
    )
    payload = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'responseMimeType': 'application/json'}
    }

    for model in GEMINI_MODELS:
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}'
        try:
            r = requests.post(url, json=payload,
                              headers={'Content-Type': 'application/json'}, timeout=15)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                parsed = json.loads(text.strip())
                if isinstance(parsed, list):
                    parsed = parsed[0]
                score = max(0, min(100, int(parsed.get('score', 50))))
                reason = parsed.get('reason', '')
                return score, reason
            elif r.status_code == 429:
                time.sleep(4)
                continue
        except Exception as e:
            time.sleep(2)
            continue

    # 폴백: 키워드 기반
    pos = sum(1 for h in headlines for kw in ['상승','수주','호재','신고가','증가','확대','흑자','수혜'] if kw in h)
    neg = sum(1 for h in headlines for kw in ['하락','악재','감소','적자','우려','취소','피소','감소'] if kw in h)
    if pos + neg == 0:
        return 50, '키워드 중립'
    score = int(20 + (pos / (pos + neg)) * 70)
    return score, f'키워드(+{pos}/-{neg})'


# ────────────────────────────────────────────────────────────────
# 3. 거래일별 NSAA 점수 산출
# ────────────────────────────────────────────────────────────────
def build_nsaa_scores(code, name, trading_dates, news_by_date, long_min):
    """
    각 거래일의 '전날까지 누적 뉴스'로 NSAA 점수를 산출합니다.
    DB 캐시를 활용하여 중복 Gemini 호출을 방지합니다.
    반환: {YYYYMMDD: {'score': int, 'direction': str, 'headlines': list}}
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    _init_nsaa_cache(conn)

    # 모든 날짜 정렬
    all_dates = sorted(news_by_date.keys())
    result = {}
    api_calls = 0

    for trade_date in trading_dates:
        date_str = trade_date.strftime('%Y%m%d')
        prev_date_str = (trade_date - timedelta(days=1)).strftime('%Y%m%d')

        # 전날까지의 뉴스 수집 (최근 3일)
        relevant = []
        for d in sorted(all_dates, reverse=True):
            if d <= prev_date_str:
                relevant.extend(news_by_date[d])
            if len(relevant) >= 8:
                break
        relevant = relevant[:8]

        if not relevant:
            result[date_str] = {'score': 50, 'direction': 'NEUTRAL',
                                 'headlines': [], 'source': 'no_news'}
            continue

        # 캐시 확인
        cached = get_cached_nsaa(conn, code, date_str)
        if cached:
            score, reason = cached
            source = 'cache'
        else:
            # Gemini 호출 (rate limit: 4초 간격)
            score, reason = gemini_sentiment(relevant, code, date_str)
            save_nsaa_cache(conn, code, date_str, score, reason)
            api_calls += 1
            time.sleep(4)   # 15 RPM 한도 준수
            source = 'gemini'

        direction = 'LONG' if score >= long_min else 'NEUTRAL'
        result[date_str] = {
            'score': score, 'direction': direction,
            'headlines': relevant[:3], 'reason': reason, 'source': source
        }

    conn.close()
    print(f'  [{name}] NSAA 산출 완료: {len(result)}일 '
          f'(Gemini {api_calls}회 호출, 캐시 {len(result)-api_calls}회)')
    long_days = sum(1 for v in result.values() if v['direction'] == 'LONG')
    print(f'    LONG 방향 일수: {long_days}/{len(result)}일 ({long_days/len(result)*100:.0f}%)')
    return result


# ────────────────────────────────────────────────────────────────
# 4. NSAA 방향 필터 적용 5분봉 백테스트
# ────────────────────────────────────────────────────────────────
def backtest_with_nsaa(df_5min, name, K, SL_PCT, TP_PCT,
                       nsaa_scores, CONTRACT_SIZE=10, CAPITAL=31_000_000):
    """nsaa_scores 딕셔너리를 방향 필터로 적용한 5분봉 백테스트"""
    import datetime as _dt

    MARGIN_RATE = 0.15
    cap = CAPITAL
    trades_log = []

    df_5min['date_only'] = df_5min.index.date
    daily_groups = [(d, g) for d, g in df_5min.groupby('date_only')]

    for idx, (trade_date, day_df) in enumerate(daily_groups):
        day_df = day_df.sort_index()
        date_str = trade_date.strftime('%Y%m%d')

        # NSAA 방향 확인
        nsaa_info = nsaa_scores.get(date_str, {})
        direction = nsaa_info.get('direction', 'NEUTRAL')
        if direction != 'LONG':
            continue  # 오늘은 거래 없음

        if idx == 0:
            continue
        prev_date, prev_df = daily_groups[idx - 1]
        prev_range = float(prev_df['high'].max() - prev_df['low'].min())
        if prev_range <= 0:
            continue

        # 09:05 시초가
        morning = day_df[day_df.index.time >= _dt.time(9, 5)]
        if morning.empty:
            continue
        day_open = float(morning.iloc[0]['open'])
        target_long = day_open + prev_range * K

        in_pos = False
        entry_price = sl_p = tp_p = 0.0

        for dt, bar in day_df.iterrows():
            if bar.name.time() >= _dt.time(15, 15) and in_pos:
                pnl = (bar['open'] - entry_price) * CONTRACT_SIZE
                cap += pnl
                trades_log.append({'date': trade_date, 'type': 'FC',
                                    'pnl': pnl, 'nsaa': nsaa_info.get('score', 50)})
                in_pos = False
                break

            if not in_pos:
                if bar.name.time() < _dt.time(9, 5):
                    continue
                if bar['high'] >= target_long:
                    entry_price = target_long
                    sl_p = entry_price * (1 - SL_PCT / 100)
                    tp_p = entry_price * (1 + TP_PCT / 100)
                    in_pos = True
            else:
                if bar['low'] <= sl_p:
                    pnl = (sl_p - entry_price) * CONTRACT_SIZE
                    cap += pnl
                    trades_log.append({'date': trade_date, 'type': 'SL',
                                        'pnl': pnl, 'nsaa': nsaa_info.get('score', 50)})
                    in_pos = False
                elif bar['high'] >= tp_p:
                    pnl = (tp_p - entry_price) * CONTRACT_SIZE
                    cap += pnl
                    trades_log.append({'date': trade_date, 'type': 'TP',
                                        'pnl': pnl, 'nsaa': nsaa_info.get('score', 50)})
                    in_pos = False

    if not trades_log:
        return None

    df_t = pd.DataFrame(trades_log)
    wins = (df_t['pnl'] > 0).sum()
    total_pnl = df_t['pnl'].sum()
    total_ret = total_pnl / CAPITAL * 100

    arr = np.array([CAPITAL] + [CAPITAL + df_t['pnl'].iloc[:i+1].sum()
                                for i in range(len(df_t))])
    peak = np.maximum.accumulate(arr)
    mdd = float(np.min((arr - peak) / peak) * 100)

    type_c = df_t['type'].value_counts().to_dict()
    avg_nsaa = df_t['nsaa'].mean()

    return {
        'name': name,
        'trades': len(df_t), 'wins': int(wins),
        'win_rate': round(wins / len(df_t) * 100, 1),
        'total_pnl': int(total_pnl), 'total_ret': round(total_ret, 2),
        'mdd': round(mdd, 2), 'final_capital': int(cap),
        'sl': type_c.get('SL', 0), 'tp': type_c.get('TP', 0),
        'fc': type_c.get('FC', 0),
        'avg_nsaa': round(avg_nsaa, 1),
        'avg_pnl': round(total_pnl / len(df_t))
    }


# ────────────────────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=== NSAA Gemini AI 연동 백테스트 재검증 ===\n')

    if not GEMINI_KEY:
        print('Gemini API 키 없음! config.json gemini_api_key 확인 필요')
        sys.exit(1)
    print(f'Gemini API 키 로드 완료 ({GEMINI_KEY[:8]}...)\n')

    # 5분봉 데이터 로드 (isf_5min_backtest.py에서 저장됨)
    print('[5분봉 데이터 로드]')
    conn = sqlite3.connect(DB_PATH)
    df_ss = pd.read_sql(
        "SELECT * FROM isf_5min_ohlcv WHERE code='005930' ORDER BY date", conn)
    df_sk = pd.read_sql(
        "SELECT * FROM isf_5min_ohlcv WHERE code='000660' ORDER BY date", conn)
    conn.close()

    if df_ss.empty or df_sk.empty:
        print('5분봉 데이터 없음 — 먼저 isf_5min_backtest.py 실행 필요')
        import subprocess
        subprocess.run([r'venv32\Scripts\python.exe', r'bqa\isf_5min_backtest.py'],
                       cwd=workspace_root)
        conn = sqlite3.connect(DB_PATH)
        df_ss = pd.read_sql("SELECT * FROM isf_5min_ohlcv WHERE code='005930' ORDER BY date", conn)
        df_sk = pd.read_sql("SELECT * FROM isf_5min_ohlcv WHERE code='000660' ORDER BY date", conn)
        conn.close()

    for df in [df_ss, df_sk]:
        df['datetime'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S')
        df.set_index('datetime', inplace=True)

    # 거래일 목록 추출
    trading_dates_ss = sorted(set(df_ss.index.date))
    trading_dates_sk = sorted(set(df_sk.index.date))
    print(f'  삼성전자: {len(trading_dates_ss)}거래일 / SK하이닉스: {len(trading_dates_sk)}거래일\n')

    # 뉴스 수집
    print('[뉴스 수집] 네이버 모바일 API...')
    news_ss = fetch_all_news('005930', max_pages=40)
    news_sk = fetch_all_news('000660', max_pages=40)
    print()

    # NSAA 점수 산출 (Gemini AI)
    print('[NSAA 점수 산출] Gemini AI 감성 분석 중...')
    print('  (4초 간격 API 호출, 캐시 재활용 — 약 2~5분 소요)\n')
    nsaa_ss = build_nsaa_scores('005930', '삼성전자', trading_dates_ss,
                                 news_ss, long_min=72)
    nsaa_sk = build_nsaa_scores('000660', 'SK하이닉스', trading_dates_sk,
                                 news_sk, long_min=80)

    # NSAA 점수 샘플 출력
    print('\n[NSAA 점수 샘플 — 최근 5일]')
    for code, nm, nsaa_dict in [('005930','삼성전자',nsaa_ss), ('000660','SK하이닉스',nsaa_sk)]:
        print(f'\n  {nm}:')
        for d in sorted(nsaa_dict.keys())[-5:]:
            v = nsaa_dict[d]
            print(f'    {d}: {v["score"]}점 → {v["direction"]} | {v.get("reason","")[:40]}')

    # 백테스트 재실행
    print('\n[NSAA 기반 5분봉 백테스트 재실행]')
    r_ss_nsaa = backtest_with_nsaa(df_ss, '삼성전자 (NSAA)', K=0.15,
                                    SL_PCT=1.5, TP_PCT=4.0, nsaa_scores=nsaa_ss)
    r_sk_nsaa = backtest_with_nsaa(df_sk, 'SK하이닉스 (NSAA)', K=0.15,
                                    SL_PCT=1.5, TP_PCT=4.0, nsaa_scores=nsaa_sk)

    # 결과 비교
    print('\n' + '='*65)
    print('결과 비교: 5일 모멘텀 방향 vs Gemini AI NSAA 방향')
    print('='*65)
    print(f"{'항목':>20} {'삼성(모멘텀)':>14} {'삼성(NSAA)':>14} {'SK(모멘텀)':>14} {'SK(NSAA)':>14}")
    print('-'*65)

    # 모멘텀 결과 (isf_5min_backtest.py 결과 참고값)
    mom_ss = {'거래수': 36, '승률': 61.1, '수익률': 6.00, 'MDD': -0.80}
    mom_sk = {'거래수': 87, '승률': 83.9, '수익률': 139.47, 'MDD': -1.91}

    for label, v_mom_ss, v_mom_sk, key in [
        ('거래수(회)', mom_ss['거래수'], mom_sk['거래수'],
         lambda r: r['trades'] if r else 'N/A'),
        ('승률(%)', mom_ss['승률'], mom_sk['승률'],
         lambda r: r['win_rate'] if r else 'N/A'),
        ('수익률(%)', mom_ss['수익률'], mom_sk['수익률'],
         lambda r: r['total_ret'] if r else 'N/A'),
        ('MDD(%)', mom_ss['MDD'], mom_sk['MDD'],
         lambda r: r['mdd'] if r else 'N/A'),
    ]:
        ss_v = key(r_ss_nsaa) if callable(key) else key
        sk_v = key(r_sk_nsaa) if callable(key) else key
        print(f'{label:>20} {v_mom_ss:>14} {ss_v:>14} {v_mom_sk:>14} {sk_v:>14}')

    if r_ss_nsaa:
        print(f'\n[삼성전자 NSAA 상세]')
        print(f'  거래수: {r_ss_nsaa["trades"]}회  승률: {r_ss_nsaa["win_rate"]}%  '
              f'손절:{r_ss_nsaa["sl"]} 익절:{r_ss_nsaa["tp"]} 시간:{r_ss_nsaa["fc"]}')
        print(f'  총손익: {r_ss_nsaa["total_pnl"]:+,}원  수익률: {r_ss_nsaa["total_ret"]:+.2f}%  '
              f'MDD: {r_ss_nsaa["mdd"]:.2f}%')
        print(f'  평균 NSAA 점수: {r_ss_nsaa["avg_nsaa"]}점')

    if r_sk_nsaa:
        print(f'\n[SK하이닉스 NSAA 상세]')
        print(f'  거래수: {r_sk_nsaa["trades"]}회  승률: {r_sk_nsaa["win_rate"]}%  '
              f'손절:{r_sk_nsaa["sl"]} 익절:{r_sk_nsaa["tp"]} 시간:{r_sk_nsaa["fc"]}')
        print(f'  총손익: {r_sk_nsaa["total_pnl"]:+,}원  수익률: {r_sk_nsaa["total_ret"]:+.2f}%  '
              f'MDD: {r_sk_nsaa["mdd"]:.2f}%')
        print(f'  평균 NSAA 점수: {r_sk_nsaa["avg_nsaa"]}점')

    # NSAA 점수 분포 분석
    print('\n[NSAA 점수 분포 분석]')
    for nm, nsaa_dict, long_min in [('삼성전자', nsaa_ss, 72), ('SK하이닉스', nsaa_sk, 80)]:
        scores = [v['score'] for v in nsaa_dict.values()]
        long_cnt = sum(1 for s in scores if s >= long_min)
        print(f'  {nm}: 평균={np.mean(scores):.1f}점  최소={min(scores)}  최대={max(scores)}  '
              f'LONG({long_min}점+)={long_cnt}/{len(scores)}일')
