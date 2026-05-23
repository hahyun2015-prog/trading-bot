"""
AMATS 주식 일봉 Historical 데이터 수집 + 스윙 백테스터
========================================================
- Kiwoom 불필요: 네이버 금융 일봉 크롤링
- 수집 대상: KOSPI 대형주 + top_volume_theme 편입 종목
- 백테스트: 스윙 전략 (60일 신고가 돌파 + 5일선 청산)
- 결과: unified_data.db daily_ohlcv 테이블에 저장

사용: python bqa/collect_stock_data.py
"""

import os
import sys
import sqlite3
import requests
import json
import time
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
DB_PATH = os.path.join(workspace_root, "unified_data.db")

# 기본 수집 대상 (유동성 높은 KOSPI 대형주)
DEFAULT_TARGETS = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"),
    ("005380", "현대차"),
    ("000270", "기아"),
    ("051910", "LG화학"),
    ("006400", "삼성SDI"),
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("247540", "에코프로비엠"),
    ("086520", "에코프로"),
    ("003550", "LG"),
    ("066570", "LG전자"),
    ("034730", "SK"),
]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            code TEXT,
            date TEXT,
            open INTEGER,
            high INTEGER,
            low  INTEGER,
            close INTEGER,
            volume INTEGER,
            UNIQUE(code, date)
        )
    """)
    conn.commit()
    return conn


def fetch_daily_naver(code, pages=10):
    """네이버 금융 일봉 데이터 수집 (최대 pages × 10개 = 100거래일)"""
    rows = []
    for page in range(1, pages + 1):
        url = (f"https://finance.naver.com/item/sise_day.naver"
               f"?code={code}&page={page}")
        try:
            res = requests.get(url, headers=HEADERS, timeout=5)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(res.content, "html.parser")
            trs = soup.select("table.type2 tr")
            found = False
            for tr in trs:
                tds = tr.select("td")
                if len(tds) < 7:
                    continue
                date_str = tds[0].text.strip().replace(".", "")
                if not date_str or not date_str.isdigit():
                    continue
                try:
                    close  = int(tds[1].text.strip().replace(",", ""))
                    open_  = int(tds[3].text.strip().replace(",", ""))
                    high   = int(tds[4].text.strip().replace(",", ""))
                    low    = int(tds[5].text.strip().replace(",", ""))
                    volume = int(tds[6].text.strip().replace(",", ""))
                    rows.append((code, date_str, open_, high, low, close, volume))
                    found = True
                except (ValueError, IndexError):
                    continue
            if not found:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"  [크롤링 오류] {code} page{page}: {e}")
            break
    return rows


def collect_theme_targets(conn):
    """unified_data.db top_volume_theme에서 오늘 테마 종목 추가 수집"""
    extra = []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT code, name FROM top_volume_theme ORDER BY date DESC LIMIT 20"
        )
        extra = cursor.fetchall()
    except Exception:
        pass
    return extra


def run_swing_backtest(conn, code, name):
    """
    스윙 전략 백테스트 (일봉 기반):
      - 진입: 종가가 60일 최고가 갱신 (신고가 돌파)
      - 손절: -7% (일봉 기준 스윙 손절)
      - 익절: 5일 이동평균선 종가 하향 이탈 시 청산
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT date, open, high, low, close, volume FROM daily_ohlcv "
        "WHERE code=? ORDER BY date ASC",
        (code,)
    )
    rows = cursor.fetchall()
    if len(rows) < 65:
        return None

    dates  = [r[0] for r in rows]
    closes = [r[4] for r in rows]
    opens  = [r[1] for r in rows]

    INIT    = 10_000_000
    STOP    = -0.07
    capital = INIT
    pos     = 0
    entry   = 0
    trades  = 0
    wins    = 0
    equity  = [INIT]

    for i in range(60, len(rows)):
        c    = closes[i]
        high60 = max(closes[i - 60:i])
        ma5  = sum(closes[i - 4:i + 1]) / 5 if i >= 4 else c

        if pos == 1:
            pnl = (c - entry) / entry
            if pnl <= STOP:
                capital += capital * pnl
                equity.append(capital)
                trades += 1
                pos = 0
            elif c < ma5:
                capital += capital * pnl
                equity.append(capital)
                trades += 1
                if pnl > 0:
                    wins += 1
                pos = 0
        elif pos == 0:
            if c > high60:
                pos   = 1
                entry = c

    # MDD
    peak = equity[0]
    mdd = 0.0
    for eq in equity:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > mdd:
            mdd = dd

    wr     = (wins / trades * 100) if trades > 0 else 0
    profit = (capital - INIT) / INIT * 100
    days   = max(1, len(rows))
    cagr   = ((capital / INIT) ** (252 / days) - 1) * 100 if capital > 0 else -100

    return {
        'code': code, 'name': name,
        'trades': trades, 'win_rate': round(wr, 2),
        'profit': round(profit, 2), 'cagr': round(cagr, 2),
        'mdd': round(mdd, 2),
    }


def main():
    print("=" * 60)
    print("  AMATS 주식 일봉 데이터 수집 + 스윙 백테스트")
    print("=" * 60)

    conn = init_db()
    theme_targets = collect_theme_targets(conn)

    all_targets = list(DEFAULT_TARGETS)
    for code, name in theme_targets:
        if not any(c == code for c, _ in all_targets):
            all_targets.append((code, name))

    print(f"\n수집 대상: {len(all_targets)}개 종목 (대형주 {len(DEFAULT_TARGETS)} + 테마 {len(theme_targets)})")
    print(f"수집 기간: 최근 100거래일 (네이버 금융 10페이지)")

    cursor = conn.cursor()
    collected = 0
    for i, (code, name) in enumerate(all_targets):
        print(f"  [{i+1:2}/{len(all_targets)}] {name}({code}) 수집 중...", end="", flush=True)
        rows = fetch_daily_naver(code, pages=10)
        if rows:
            cursor.executemany(
                "INSERT OR REPLACE INTO daily_ohlcv "
                "(code, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()
            print(f" {len(rows)}일치 저장")
            collected += 1
        else:
            print(" 데이터 없음")

    print(f"\n수집 완료: {collected}/{len(all_targets)}개 종목")

    # ── 스윙 전략 백테스트 ──────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  스윙 전략 백테스트 (60일 신고가 돌파 + 5MA 청산)")
    print("─" * 60)
    print(f"{'종목명':<12} | {'매매':>5} | {'승률%':>6} | {'수익%':>7} | {'CAGR%':>7} | {'MDD%':>6}")
    print("-" * 60)

    backtest_results = []
    for code, name in all_targets:
        r = run_swing_backtest(conn, code, name)
        if r and r['trades'] >= 3:
            backtest_results.append(r)
            print(f"{r['name']:<12} | {r['trades']:>5} | {r['win_rate']:>6.1f} | "
                  f"{r['profit']:>7.2f} | {r['cagr']:>7.2f} | {r['mdd']:>6.2f}")

    if backtest_results:
        avg_wr   = sum(r['win_rate'] for r in backtest_results) / len(backtest_results)
        avg_cagr = sum(r['cagr'] for r in backtest_results) / len(backtest_results)
        avg_mdd  = sum(r['mdd'] for r in backtest_results) / len(backtest_results)
        print(f"\n  평균: 승률 {avg_wr:.1f}% | CAGR {avg_cagr:.2f}% | MDD {avg_mdd:.2f}%")
        print(f"  분석 종목 수: {len(backtest_results)}개")

    conn.close()
    print(f"\n데이터 저장 위치: {DB_PATH} (daily_ohlcv 테이블)")


if __name__ == "__main__":
    main()
