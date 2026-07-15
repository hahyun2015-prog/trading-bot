"""
야간선물 데이터 수집 전용 임시 도구.

era_order_manager.py와 별개의 독립 프로세스로, 키움증권 실서버(LIVE)에 접속해
선물 과거/실시간 5분봉만 futures_data.db(era_order_manager.py/bqa 백테스터와
동일한 파일·테이블)에 채워 넣는다. 목적을 다하면 프로세스를 종료하면 되고,
이후 모의투자·실거래 전환 시 이 파일이 쌓아둔 데이터를 그대로 이어받아 쓴다.

안전 설계:
  - 이 파일 전체에 주문 관련 dynamicCall(SendOrder/SendOrderFO 등)이 단 한 줄도 없다.
    로그인 → 과거 데이터 조회(opt50029) → 실시간 시세 구독(SetRealReg) → DB 저장이 전부다.
  - OnReceiveChejanData(체결 통보)도 연결하지 않는다 — 애초에 주문을 내지 않으므로 불필요.
  - era_order_manager.py(포트 9991)와 별개의 포트(9992)로 중복 실행만 방지한다.
"""
import os
import sys
import sqlite3
import socket
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.abspath(os.path.join(current_dir, ".."))
FUTURES_DB_PATH = os.path.join(WORKSPACE_ROOT, "futures_data.db")
LOG_FILE = os.path.join(current_dir, "night_data_collector.log")

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

MAX_SYNC_PAGES = 10  # era_order_manager.py의 "전체 동기화" 기준과 동일


class NightFuturesDataCollector:
    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)
        self.kiwoom.OnReceiveRealData.connect(self._on_receive_real_data)
        # 주의: OnReceiveChejanData(체결 통보)는 의도적으로 연결하지 않음 — 주문을 내지 않는 도구라 불필요.

        self.target_codes = []
        self.sync_queue = []
        self.sync_index = 0
        self.sync_page = 0
        self.sync_stop_code = False  # 이번 코드에서 빈 페이지를 만나 조기 종료할지
        self.ohlcv_buffer = {}
        self.total_written = 0

        self.flush_timer = QTimer()
        self.flush_timer.timeout.connect(self._flush_buffer)
        self.flush_timer.start(30000)

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._print_status)
        self.status_timer.start(300000)

        print("=" * 70)
        print("[야간선물 데이터 수집기] 키움증권 실서버(LIVE) 접속 시도")
        print("이 도구는 주문 기능이 구현되어 있지 않습니다 (시세/과거데이터 수집 전용)")
        print("=" * 70)
        self.kiwoom.dynamicCall("KOA_Functions(QString, QString)", "SetServerGBCode", "1")
        self.kiwoom.dynamicCall("CommConnect()")

    # ── 로그인 ──────────────────────────────────────────────────
    def _on_login(self, err_code):
        if err_code != 0:
            print(f"[야간수집기] 로그인 실패 (err_code={err_code}) — 계정/네트워크 상태를 확인하세요.")
            return
        print("[야간수집기] 실서버 로그인 성공")
        self._detect_codes()
        self._start_sync()

    def _detect_codes(self):
        future_list = self.kiwoom.dynamicCall("GetFutureList()").strip()
        codes = [c for c in future_list.split(';') if c.strip()]
        print(f"[야간수집기] GetFutureList() 전체 응답({len(codes)}개): {codes}")

        # 접두사별로 묶어서 출력 — 101/105 외에 다른 계열(야간전용 등)이 섞여 있는지 한눈에 확인
        prefixes = {}
        for c in codes:
            prefixes.setdefault(c[:3], []).append(c)
        for pfx, lst in sorted(prefixes.items()):
            print(f"    접두사 '{pfx}' ({len(lst)}개): {lst}")

        # 계좌에 등록된 선물 계좌번호/상품 구분도 함께 확인 (야간선물 약정 여부 단서)
        try:
            acc_list = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCLIST").strip()
            print(f"[야간수집기] 계좌 목록: {acc_list}")
        except Exception as e:
            print(f"[야간수집기] 계좌 목록 조회 실패: {e}")

        day_code = next((c for c in codes if c.startswith("101") or c.startswith("105")), None)
        if not day_code:
            code_by_idx = self.kiwoom.dynamicCall("GetFutureCodeByIndex(int)", 0).strip()
            day_code = code_by_idx or None

        night_code = None
        if day_code:
            night_code = day_code if day_code.startswith("105") else ("105" + day_code[3:])

        # era_order_manager.py가 실제로 쓰는 제네릭 코드도 항상 같이 확보(대조군 겸용)
        self.target_codes = list(dict.fromkeys(
            [c for c in (day_code, night_code, "10500000", "10100000") if c]
        ))
        print(f"[야간수집기] 수집 대상 코드: {self.target_codes}")

    # ── 과거 데이터 동기화 ──────────────────────────────────────
    def _start_sync(self):
        self.sync_queue = list(self.target_codes)
        self.sync_index = 0
        self.sync_page = 0
        self._request_sync()

    def _request_sync(self, prev_next="0"):
        if self.sync_index >= len(self.sync_queue):
            print("[야간수집기] 과거 데이터 동기화 완료.")
            self._report_night_coverage()
            self._subscribe_realtime()
            print("[야간수집기] 실시간 시세 수집 대기 중... (Ctrl+C로 종료, 30초마다 DB 반영)")
            return

        code = self.sync_queue[self.sync_index]
        self.sync_stop_code = False
        print(f"[야간수집기] {code} 과거 5분봉 조회 중... ({self.sync_page + 1}/{MAX_SYNC_PAGES})")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", "5")
        QTimer.singleShot(200, lambda: self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "야간선물과거동기화", "opt50029", int(prev_next), "9029"
        ))

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        try:
            self._on_receive_tr_data_impl(screen_no, rqname, trcode, record_name, next_str)
        except Exception as e:
            import traceback
            print(f"[야간수집기 TR 처리 오류] {e}")
            traceback.print_exc()

    def _on_receive_tr_data_impl(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname != "야간선물과거동기화":
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
            self.sync_index += 1
            self.sync_page = 0
            self._request_sync()

    def _report_night_coverage(self):
        """이번 동기화로 08:45~15:45 바깥(=야간) 시각 데이터가 실제로 확보됐는지 즉시 검증."""
        try:
            conn = sqlite3.connect(FUTURES_DB_PATH, timeout=30)
            cur = conn.cursor()
            for code in self.target_codes:
                cur.execute(
                    "SELECT COUNT(*), MIN(date), MAX(date) FROM futures_ohlcv "
                    "WHERE code=? AND (substr(date,9,4) < '0845' OR substr(date,9,4) > '1545')",
                    (code,)
                )
                cnt, min_d, max_d = cur.fetchone()
                if cnt:
                    print(f"[야간수집기 검증] {code}: 야간(08:45~15:45 바깥) 데이터 {cnt}건 확보! ({min_d} ~ {max_d})")
                else:
                    print(f"[야간수집기 검증] {code}: 야간 시간대 데이터 없음 (과거 동기화 기준)")
            conn.close()
        except Exception as e:
            print(f"[야간수집기 검증 오류] {e}")

    # ── 실시간 구독/수집 ────────────────────────────────────────
    def _subscribe_realtime(self):
        codes_str = ";".join(self.target_codes)
        self.kiwoom.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            "NIGHT_COLLECT", codes_str, "10;11;12;15", "0"
        )
        print(f"[야간수집기] 실시간 구독 등록 완료: {self.target_codes}")

    def _on_receive_real_data(self, code, real_type, real_data):
        try:
            if real_type not in ("선물시세", "선물체결"):
                return
            raw = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10).strip()
            if not raw:
                return
            price = abs(float(raw))
            self._update_buffer(code, price)
        except Exception as e:
            print(f"[야간수집기 실시간 콜백 오류] {e}")

    def _update_buffer(self, code, price):
        if price <= 0:
            return
        now = datetime.now()
        period_min = (now.minute // 5) * 5
        period_str = now.strftime(f"%Y%m%d{now.hour:02d}") + f"{period_min:02d}00"
        buf = self.ohlcv_buffer.setdefault(code, {})
        if period_str not in buf:
            buf[period_str] = {'o': price, 'h': price, 'l': price, 'c': price, 'v': 1}
        else:
            c = buf[period_str]
            c['h'] = max(c['h'], price)
            c['l'] = min(c['l'], price)
            c['c'] = price
            c['v'] += 1

    def _flush_buffer(self):
        if not self.ohlcv_buffer:
            return
        rows = []
        for code, periods in self.ohlcv_buffer.items():
            for period_str, c in periods.items():
                rows.append((code, period_str, c['o'], c['h'], c['l'], c['c'], c['v']))
        self._write_rows(rows)

    def _write_rows(self, rows):
        if not rows:
            return
        try:
            conn = sqlite3.connect(FUTURES_DB_PATH, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS futures_ohlcv
                            (code TEXT, date TEXT, open REAL, high REAL,
                             low REAL, close REAL, volume INTEGER, UNIQUE(code, date))""")
            cur.executemany(
                "REPLACE INTO futures_ohlcv (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()
            conn.close()
            self.total_written += len(rows)
            print(f"    [DB 저장] {len(rows)}건 (누적 {self.total_written}건)")
        except Exception as e:
            print(f"[야간수집기 DB 저장 오류] {e}")

    def _on_receive_msg(self, screen_no, rqname, trcode, msg):
        print(f"[Kiwoom Msg] {msg}")

    def _print_status(self):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        buffered_bars = sum(len(p) for p in self.ohlcv_buffer.values())
        print(f"[야간수집기 생존확인] {now} | 버퍼 중인 봉: {buffered_bars} | 누적 DB 기록: {self.total_written}건")


if __name__ == "__main__":
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', 9992))  # era_order_manager.py(9991)와 다른 포트
    except socket.error:
        print("[야간수집기] 이미 실행 중입니다 (Port 9992 Lock). 중복 실행을 중단합니다.")
        sys.exit(0)

    print("#" * 70)
    print("# 경고: 이 스크립트는 키움증권 실서버(모의투자 아님)로 로그인합니다.")
    print("# 이 파일에는 주문 관련 코드가 전혀 없습니다 — 시세/과거데이터 수집 전용입니다.")
    print("# era_order_manager.py(모의투자, PID 별도)와 동시 실행해도 서로 다른 프로세스/포트입니다.")
    print("# 종료하려면 이 창에서 Ctrl+C를 누르세요.")
    print("#" * 70)

    app = QApplication(sys.argv)
    collector = NightFuturesDataCollector()
    sys.exit(app.exec_())
