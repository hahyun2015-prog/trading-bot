# -*- coding: utf-8 -*-
"""키움 1분봉 과거 데이터 깊이 측정 (2026-08-16).

목적은 하나다 — **opt50029가 시간단위=1로 얼마나 과거까지 주는가**.
5분봉 백테스트에서 "봉이 굵을수록 나쁘다"가 단조로 나왔는데, 그 추세를 왼쪽으로
연장하면 1분봉이 더 나을 수 있다. 검증하려면 이력이 필요하고, 지금 DB에는
주간 1분봉이 없다(A05609에 673봉뿐 — 야간수집기 산물).

안전 설계 (era/night_futures_data_collector.py를 그대로 따랐다):
  · 주문 관련 dynamicCall(SendOrder/SendOrderFO)이 한 줄도 없다.
  · OnReceiveChejanData(체결 통보)를 연결하지 않는다.
  · **DB에 쓰지 않는다.** 조회 결과를 화면에만 출력한다.
  · 실시간 구독(SetRealReg)도 하지 않는다 — 과거 조회만 하고 끝낸다.
  · 포트 9993으로 중복 실행만 막는다(ERA 9991, 야간수집기 9992와 별개).

ERA와 동시 실행: 야간수집기가 이미 같은 방식으로 ERA와 공존한다. 포트 락은
파이썬 레벨 중복 방지일 뿐 키움 제약이 아니다. 다만 TR 요청이 같은 세션 쿼터를
나눠 쓰므로, 장중에는 돌리지 말 것 — 주말/장 마감 후용이다.

사용:
    venv32\\Scripts\\python.exe scratch\\probe_1min_depth.py [종목코드] [최대페이지]
    기본값: A0569000(현재 근월물) 40페이지
"""
import os
import socket
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

_qt = os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "PyQt5", "Qt5", "plugins")
if os.path.exists(_qt):
    os.environ["QT_PLUGIN_PATH"] = _qt
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_qt, "platforms")

from PyQt5.QtWidgets import QApplication          # noqa: E402
from PyQt5.QAxContainer import QAxWidget          # noqa: E402
from PyQt5.QtCore import QTimer                   # noqa: E402

CODE = sys.argv[1] if len(sys.argv) > 1 else "A0569000"
MAX_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 40
UNIT = "1"                                        # ← 이것을 재려고 만든 스크립트


class DepthProbe:
    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_tr)
        self.kiwoom.OnReceiveMsg.connect(self._on_msg)
        # OnReceiveChejanData는 연결하지 않는다 — 주문을 내지 않는 도구다.

        self.page = 0
        self.total = 0
        self.oldest = None
        self.newest = None
        self.t0 = datetime.now()

        print("=" * 78)
        print(f"[1분봉 깊이 측정] 종목 {CODE} | 시간단위={UNIT} | 최대 {MAX_PAGES}페이지")
        print("조회 전용 — 주문 기능 없음, DB 쓰기 없음, 실시간 구독 없음")
        print("=" * 78)
        self.kiwoom.dynamicCall("KOA_Functions(QString, QString)", "SetServerGBCode", "1")
        self.kiwoom.dynamicCall("CommConnect()")

    def _on_login(self, err):
        if err != 0:
            print(f"[실패] 로그인 err={err}. 키움 로그인 상태를 확인하세요.")
            QApplication.quit()
            return
        print("[OK] 실서버 로그인 성공\n")
        self._request("0")

    def _on_msg(self, screen, rqname, trcode, msg):
        if msg.strip():
            print(f"  [Kiwoom] {msg.strip()[:90]}")

    def _request(self, prev_next):
        if self.page >= MAX_PAGES:
            self._done("최대 페이지 도달")
            return
        self.page += 1
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", CODE)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", UNIT)
        QTimer.singleShot(250, lambda: self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "1분봉깊이측정", "opt50029", int(prev_next), "9993"))

    def _on_tr(self, screen, rqname, trcode, record, next_str):
        if rqname != "1분봉깊이측정":
            return
        cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
        if cnt == 0:
            print(f"  p{self.page:>3}: 0봉 — 더 이상 과거 데이터 없음")
            self._done("빈 응답")
            return

        def g(i, field):
            return self.kiwoom.dynamicCall(
                "GetCommData(QString, QString, int, QString)", trcode, rqname, i, field).strip()

        first, last = g(0, "체결시간"), g(cnt - 1, "체결시간")
        self.total += cnt
        if self.newest is None:
            self.newest = first
        self.oldest = last
        print(f"  p{self.page:>3}: {cnt:>4}봉 | {last} ~ {first} | 누적 {self.total:,}")

        if next_str.strip() == "2":
            QTimer.singleShot(300, lambda: self._request("2"))
        else:
            self._done("연속조회 종료(next != 2)")

    def _done(self, why):
        print()
        print("=" * 78)
        print(f"[결과] {why}")
        print("=" * 78)
        if not self.total:
            print("  수신 0봉 — 시간단위=1을 지원하지 않거나 해당 종목에 데이터가 없습니다.")
        else:
            def parse(s):
                for f in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
                    try:
                        return datetime.strptime(s, f)
                    except ValueError:
                        continue
                return None
            o, n = parse(self.oldest), parse(self.newest)
            print(f"  총 {self.total:,}봉 | {self.page}페이지")
            print(f"  최신 {self.newest}")
            print(f"  최고(과거) {self.oldest}")
            if o and n:
                days = (n - o).days
                print(f"  기간 {days}일 (달력) ≈ 거래일 {days * 5 // 7}일 추정")
                print()
                print("  판정:")
                if days >= 365:
                    print("    1년 이상 — 별도 테이블에 적재해 바로 백테스트 가능")
                elif days >= 120:
                    print("    수개월 — 검증에는 부족. 지금부터 수집 시작 권장")
                else:
                    print("    매우 짧음 — 소급 검증 불가. 수집을 시작해 이력을 쌓아야 함")
        print(f"\n  소요 {(datetime.now() - self.t0).total_seconds():.0f}초")
        QApplication.quit()


if __name__ == "__main__":
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", 9993))
    except OSError:
        print("[중단] 이미 다른 측정 스크립트가 실행 중입니다 (Port 9993).")
        sys.exit(1)
    app = QApplication(sys.argv)
    probe = DepthProbe()
    app.exec_()
