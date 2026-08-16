"""
1분봉 과거 데이터 백필 전용 임시 도구.

era_order_manager.py/night_futures_data_collector.py와 별개의 독립 프로세스로,
키움증권 실서버(LIVE)에 접속해 선물 과거 1분봉을 futures_data.db의 새 테이블
futures_ohlcv_1m 에 채워 넣는다(기존 5분봉 테이블 futures_ohlcv는 건드리지 않음).
목적: "5분봉 대신 1분봉으로 매매하면 반응속도가 개선되는지"를 백테스트로
검증하려면 실제 1분봉 이력이 필요한데, DB에 5분봉만 있어서 이 도구로 확보한다.

키움 서버가 1분봉을 실제로 며칠/몇 개월치나 보관하고 있는지는 코드로 알 수
없어 직접 요청해봐야 한다 — 이 스크립트는 서버가 더 줄 데이터가 없다고
할 때까지(cnt==0 또는 next_str != "2") 자동으로 멈춘다.

안전 설계 (night_futures_data_collector.py와 동일 원칙):
  - 이 파일 전체에 주문 관련 dynamicCall(SendOrder/SendOrderFO 등)이 단 한 줄도 없다.
    로그인 → 과거 1분봉 조회(opt50029, 시간단위="1") → DB 저장 → 종료가 전부다.
  - OnReceiveChejanData(체결 통보)도 연결하지 않는다 — 주문을 내지 않으므로 불필요.
  - era_order_manager.py(화면번호 5029)/night_futures_data_collector.py(화면번호 9029)와
    겹치지 않게 별도 화면번호(9039)를 쓴다. 다만 이 도구 자체는 포트 기반 중복실행
    방지가 없는 1회성 수동 실행 도구이므로, era 프로세스가 이미 돌고 있는 상태에서
    같이 실행해도 무방하지만(화면번호가 다르므로 TR 충돌 없음) 굳이 여러 번 동시에
    실행할 필요는 없다.

실행 방법 (Kiwoom OpenAPI 세션이 필요하므로 32비트 venv에서 직접 실행):
    venv32\\Scripts\\python.exe era\\backfill_futures_1min.py
완료하거나 Ctrl+C로 중단해도 그때까지 받은 데이터는 이미 DB에 반영되어 있다
(페이지마다 즉시 커밋).
"""
import os
import sys
import sqlite3

current_dir = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.abspath(os.path.join(current_dir, ".."))
FUTURES_DB_PATH = os.path.join(WORKSPACE_ROOT, "futures_data.db")
LOG_FILE = os.path.join(current_dir, "backfill_1min.log")

TARGET_CODES = ["10100000", "10500000"]  # era_order_manager.py/백테스터가 쓰는 주간/야간 연속(제네릭) 코드
TIME_UNIT = "1"          # "1" = 1분봉 (기존 5분봉 동기화 코드의 "5"만 바꾼 것)
MAX_SYNC_PAGES = 3000    # 서버가 더 줄 데이터가 없으면 이 값 전에 자연 종료됨 — 안전 상한선
REQUEST_DELAY_MS = 250   # TR 과부하/レート리밋 방지. 경고 메시지가 자주 보이면 400~500으로 올릴 것


# 윈도우 CP949 콘솔 인코딩 에러 방지 + 파일 로깅 (era_order_manager.py와 동일 패턴, 간소화)
class _SafeStream:
    def __init__(self, original, log_path):
        self.original = original
        self.log_path = log_path

    def write(self, data):
        if not data:
            return
        try:
            enc = getattr(self.original, 'encoding', 'cp949') or 'cp949'
            try:
                data.encode(enc)
                self.original.write(data)
            except UnicodeEncodeError:
                self.original.write(''.join(ch for ch in data if _fits(ch, enc)))
        except Exception:
            pass
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            pass

    def flush(self):
        self.original.flush()


def _fits(ch, enc):
    try:
        ch.encode(enc)
        return True
    except UnicodeEncodeError:
        return False


sys.stdout = _SafeStream(sys.stdout, LOG_FILE)
sys.stderr = _SafeStream(sys.stderr, LOG_FILE)

_exe_dir = os.path.dirname(sys.executable)
_qt_base = os.path.join(_exe_dir, "Lib", "site-packages", "PyQt5")
_qt_plugin_path = os.path.join(_qt_base, "Qt5", "plugins")
if not os.path.exists(_qt_plugin_path):
    _qt_plugin_path = os.path.join(_qt_base, "Qt", "plugins")
if os.path.exists(_qt_plugin_path):
    os.environ["QT_PLUGIN_PATH"] = _qt_plugin_path
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_qt_plugin_path, "platforms")

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer


class OneMinuteBackfiller:
    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)
        # 주의: OnReceiveRealData/OnReceiveChejanData는 의도적으로 연결하지 않음 —
        # 실시간 구독도, 주문도 하지 않는 순수 과거 데이터 백필 도구이므로 불필요.

        self.sync_queue = []
        self.sync_index = 0
        self.sync_page = 0
        self.sync_stop_code = False
        self.total_written = 0
        self.per_code_written = {}

        print("=" * 70)
        print("[1분봉 백필] 키움증권 실서버(LIVE) 접속 시도")
        print("이 도구는 주문 기능이 구현되어 있지 않습니다 (과거 1분봉 수집 전용)")
        print(f"대상 코드: {TARGET_CODES} | 최대 {MAX_SYNC_PAGES}페이지까지 시도")
        print("=" * 70)
        self.kiwoom.dynamicCall("KOA_Functions(QString, QString)", "SetServerGBCode", "1")
        self.kiwoom.dynamicCall("CommConnect()")

    # ── 로그인 ──────────────────────────────────────────────────
    def _on_login(self, err_code):
        if err_code != 0:
            print(f"[1분봉 백필] 로그인 실패 (err_code={err_code}) — 계정/네트워크 상태를 확인하세요.")
            QApplication.instance().quit()
            return
        print("[1분봉 백필] 실서버 로그인 성공")
        self._start_sync()

    # ── 과거 데이터 동기화 ──────────────────────────────────────
    def _start_sync(self):
        self.sync_queue = list(TARGET_CODES)
        self.sync_index = 0
        self.sync_page = 0
        self._request_sync()

    def _request_sync(self, prev_next="0"):
        if self.sync_index >= len(self.sync_queue):
            self._report_and_exit()
            return

        code = self.sync_queue[self.sync_index]
        self.sync_stop_code = False
        print(f"[1분봉 백필] {code} 과거 1분봉 조회 중... ({self.sync_page + 1}/{MAX_SYNC_PAGES}페이지)")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", TIME_UNIT)
        QTimer.singleShot(REQUEST_DELAY_MS, lambda: self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "선물1분봉백필", "opt50029", int(prev_next), "9039"
        ))

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        try:
            self._on_receive_tr_data_impl(screen_no, rqname, trcode, record_name, next_str)
        except Exception as e:
            import traceback
            print(f"[1분봉 백필 TR 처리 오류] {e}")
            traceback.print_exc()

    def _on_receive_tr_data_impl(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname != "선물1분봉백필":
            return
        cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
        code = self.sync_queue[self.sync_index]
        print(f"    [수신] {code} | {cnt}개 캔들")

        if cnt == 0:
            self.sync_stop_code = True

        rows = []
        for i in range(cnt):
            date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "체결시간").strip()
            try:
                open_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "시가").strip()))
                high_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "고가").strip()))
                low_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "저가").strip()))
                close_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()))
                vol = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "거래량").strip()))
            except ValueError:
                continue
            rows.append((code, date, open_p, high_p, low_p, close_p, vol))

        self._write_rows(rows)

        self.sync_page += 1
        if not self.sync_stop_code and str(next_str).strip() == "2" and self.sync_page < MAX_SYNC_PAGES:
            self._request_sync("2")
        else:
            reason = "서버에 더 이상 데이터 없음" if self.sync_stop_code else (
                "페이지 상한 도달" if self.sync_page >= MAX_SYNC_PAGES else "정상 종료")
            print(f"[1분봉 백필] {code} 완료 ({reason}) — 누적 {self.per_code_written.get(code, 0)}건")
            self.sync_index += 1
            self.sync_page = 0
            self._request_sync()

    def _write_rows(self, rows):
        if not rows:
            return
        try:
            conn = sqlite3.connect(FUTURES_DB_PATH, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS futures_ohlcv_1m
                            (code TEXT, date TEXT, open REAL, high REAL,
                             low REAL, close REAL, volume INTEGER, UNIQUE(code, date))""")
            cur.executemany(
                "REPLACE INTO futures_ohlcv_1m (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()
            conn.close()
            self.total_written += len(rows)
            code = rows[0][0]
            self.per_code_written[code] = self.per_code_written.get(code, 0) + len(rows)
            print(f"    [DB 저장] {len(rows)}건 (누적 {self.total_written}건)")
        except Exception as e:
            print(f"[1분봉 백필 DB 저장 오류] {e}")

    def _report_and_exit(self):
        print("\n" + "=" * 70)
        print("[1분봉 백필] 전체 완료 — 확보 현황:")
        try:
            conn = sqlite3.connect(FUTURES_DB_PATH, timeout=30)
            cur = conn.cursor()
            for code in TARGET_CODES:
                cur.execute(
                    "SELECT COUNT(*), MIN(date), MAX(date) FROM futures_ohlcv_1m WHERE code=?",
                    (code,)
                )
                cnt, min_d, max_d = cur.fetchone()
                if cnt:
                    print(f"  {code}: {cnt}건, {min_d} ~ {max_d}")
                else:
                    print(f"  {code}: 확보된 데이터 없음")
            conn.close()
        except Exception as e:
            print(f"[1분봉 백필 결과 조회 오류] {e}")
        print("=" * 70)
        QApplication.instance().quit()

    def _on_receive_msg(self, screen_no, rqname, trcode, msg):
        print(f"[Kiwoom Msg] {msg}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    collector = OneMinuteBackfiller()
    sys.exit(app.exec_())
