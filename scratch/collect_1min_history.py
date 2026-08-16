# -*- coding: utf-8 -*-
"""키움 1분봉 과거 데이터 적재 (2026-08-16).

scratch/probe_1min_depth.py 측정 결과:
    10500000(연결선물) 1분봉 = 152,240봉 / 2025-02-07 ~ 2026-08-14 / 약 380거래일
    현행 5분봉 백테스트 기간(388거래일)과 거의 같아 소급 검증이 가능하다.

**별도 테이블 `futures_ohlcv_1m`에만 쓴다.**
기존 `futures_ohlcv`는 5분봉 전용이고 백테스트·라이브가 전부 그것을 읽는다.
같은 테이블에 1분봉을 섞으면 기존 결과가 통째로 깨지므로 절대 섞지 않는다.
스키마는 `futures_ohlcv`와 동일하게 맞춰 `load_futures_data(code, table=...)`로
그대로 읽을 수 있게 한다.

안전 설계 (probe_1min_depth.py와 동일):
  · 주문 관련 dynamicCall이 한 줄도 없다.
  · OnReceiveChejanData를 연결하지 않는다.
  · 실시간 구독(SetRealReg)을 하지 않는다 — 과거 조회 후 종료한다.
  · 쓰기 대상은 `futures_ohlcv_1m` 하나뿐. 다른 테이블은 읽지도 쓰지도 않는다.
  · 포트 9994로 중복 실행만 막는다.

장중에는 돌리지 말 것 — TR 쿼터를 ERA와 나눠 쓴다. 주말/장 마감 후용이다.

사용:
    venv32\\Scripts\\python.exe scratch\\collect_1min_history.py [종목코드] [최대페이지]
    기본값: 10500000 400페이지
"""
import os
import socket
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DB = os.path.join(ROOT, "futures_data.db")
TABLE = "futures_ohlcv_1m"          # ★ 여기 외에는 쓰지 않는다

_qt = os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "PyQt5", "Qt5", "plugins")
if os.path.exists(_qt):
    os.environ["QT_PLUGIN_PATH"] = _qt
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_qt, "platforms")

from PyQt5.QtWidgets import QApplication          # noqa: E402
from PyQt5.QAxContainer import QAxWidget          # noqa: E402
from PyQt5.QtCore import QTimer                   # noqa: E402

CODE = sys.argv[1] if len(sys.argv) > 1 else "10500000"
MAX_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 400


def ensure_table():
    """futures_ohlcv와 같은 스키마로 1분봉 전용 테이블을 만든다."""
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='futures_ohlcv'")
    row = cur.fetchone()
    if row and row[0]:
        ddl = row[0].replace("futures_ohlcv", TABLE, 1)
        cur.execute(f"CREATE TABLE IF NOT EXISTS {TABLE}"
                    f"{ddl.split('futures_ohlcv_1m', 1)[1] if TABLE in ddl else ''}")
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    n = cur.fetchone()[0]
    conn.close()
    return n


class Collector:
    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_tr)
        # OnReceiveChejanData는 연결하지 않는다 — 주문을 내지 않는 도구다.

        self.page = 0
        self.rows = []
        self.total = 0
        self.oldest = None
        self.t0 = datetime.now()

        print("=" * 78)
        print(f"[1분봉 적재] 종목 {CODE} | 대상 테이블 {TABLE} | 최대 {MAX_PAGES}페이지")
        print("조회 전용 — 주문 없음, 실시간 구독 없음. 기존 futures_ohlcv는 건드리지 않음")
        print("=" * 78)
        self.kiwoom.dynamicCall("KOA_Functions(QString, QString)", "SetServerGBCode", "1")
        self.kiwoom.dynamicCall("CommConnect()")

    def _on_login(self, err):
        if err != 0:
            print(f"[실패] 로그인 err={err}")
            QApplication.quit()
            return
        print("[OK] 실서버 로그인 성공\n")
        self._request("0")

    def _request(self, prev_next):
        if self.page >= MAX_PAGES:
            self._finish("최대 페이지 도달")
            return
        self.page += 1
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", CODE)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", "1")
        QTimer.singleShot(250, lambda: self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "1분봉적재", "opt50029", int(prev_next), "9994"))

    def _on_tr(self, screen, rqname, trcode, record, next_str):
        if rqname != "1분봉적재":
            return
        cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
        if cnt == 0:
            self._finish("빈 응답 — 더 이상 과거 데이터 없음")
            return

        def g(i, f):
            return self.kiwoom.dynamicCall(
                "GetCommData(QString, QString, int, QString)", trcode, rqname, i, f).strip()

        got = 0
        for i in range(cnt):
            d = g(i, "체결시간")
            if not d:
                continue
            try:
                o, h, l, c = (abs(float(g(i, k))) for k in ("시가", "고가", "저가", "현재가"))
                v = float(g(i, "거래량") or 0)
            except ValueError:
                continue
            self.rows.append((CODE, d, o, h, l, c, v))
            got += 1
        self.total += got
        self.oldest = g(cnt - 1, "체결시간")
        if self.page % 20 == 0 or self.page <= 3:
            print(f"  p{self.page:>3}: {got:>4}봉 | ~{self.oldest} | 누적 {self.total:,}")

        if next_str.strip() == "2":
            QTimer.singleShot(280, lambda: self._request("2"))
        else:
            self._finish("연속조회 종료")

    def _finish(self, why):
        print(f"\n[수신 종료] {why} — {self.total:,}봉 / {self.page}페이지")
        if not self.rows:
            QApplication.quit()
            return
        conn = sqlite3.connect(DB, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executemany(
            f"INSERT OR REPLACE INTO {TABLE} (code, date, open, high, low, close, volume) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?)", self.rows)
        conn.commit()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT substr(date,1,8)) "
                    f"FROM {TABLE} WHERE code=?", (CODE,))
        n, mn, mx, days = cur.fetchone()
        conn.close()
        print()
        print("=" * 78)
        print(f"[적재 완료] {TABLE}")
        print("=" * 78)
        print(f"  {CODE}: {n:,}봉 | {mn} ~ {mx} | 거래일 {days}일")
        print(f"  소요 {(datetime.now() - self.t0).total_seconds():.0f}초")
        print(f"\n  읽기: load_futures_data('{CODE}', table='{TABLE}')")
        QApplication.quit()


if __name__ == "__main__":
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", 9994))
    except OSError:
        print("[중단] 이미 실행 중입니다 (Port 9994).")
        sys.exit(1)
    before = ensure_table()
    print(f"[준비] {TABLE} 기존 {before:,}행")
    app = QApplication(sys.argv)
    c = Collector()
    app.exec_()
