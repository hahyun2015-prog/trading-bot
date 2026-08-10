"""
한국투자증권(KIS) Open API로 KRX 야간선물(코스피200) 시세를 수집해
기존 era_order_manager.py / bqa 백테스터와 동일한 futures_data.db의
futures_ohlcv 테이블에 적재하는 도구.

사전 준비 (필수):
  1. 한국투자증권 실전 또는 모의투자 계좌 + KIS Developers(apiportal.koreainvestment.com)
     에서 App Key/App Secret 발급
  2. C:\\Users\\<사용자>\\KIS\\config\\kis_devlp.yaml 에 발급받은 값 채워넣기
     (my_app/my_sec 또는 paper_app/paper_sec, my_acct_future, my_prod="03")
  3. pip install requests websockets PyYAML pycryptodome (이미 설치됨)

이 파일에는 주문 관련 함수 호출이 없다 — 시세 조회/구독 전용.
DB에 저장하는 code는 KIS 마스터파일의 단축코드(예: "101W9000")를 그대로 쓰며,
Kiwoom쪽 코드(10500000 등)와 겹치지 않으므로 같은 DB에 안전하게 공존한다.
"""
import os
import sys
import time
import sqlite3
import argparse
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(current_dir, "kis_night_collector.log")


# 윈도우 CP949 콘솔 인코딩 에러 방지 + 파일 로깅 (era/night_futures_data_collector.py와 동일 패턴).
# (2026-07-17 도입: print()가 이모지/대시(—) 문자를 cp949로 못 옮기고 UnicodeEncodeError를
#  던지면 이게 while True 재연결 루프의 마지막 print문이라 그대로 밖으로 새어나가 무한 재연결
#  루프 자체가 죽어버리는 사고가 있었음 — 2026-07-15 새벽 01:00경 재현 확인, 그 뒤 남은
#  야간시간대(01:00~05:00) 데이터가 통째로 누락됨)
class _SafeStream:
    # KIS 라이브러리가 수신 틱마다 INFO 로그를 찍어 로그가 무제한으로 커진다.
    # 2026-08-10 실측 240MB — 32비트 파이썬으로는 통째로 읽지도 못해 진단이 어려웠다.
    # ERA(notifier.SafeStreamWrapper)와 같은 20MB×3 회전을 넣는다. 틱마다 stat()을
    # 부르면 비용이 크므로 일정 횟수마다만 확인한다.
    MAX_BYTES = 20 * 1024 * 1024
    BACKUPS = 3
    CHECK_EVERY = 500

    def __init__(self, original, log_path):
        self.original = original
        self.log_path = log_path
        self._writes = 0

    def _rotate_if_needed(self):
        try:
            if os.path.getsize(self.log_path) < self.MAX_BYTES:
                return
        except OSError:
            return
        try:
            oldest = f"{self.log_path}.{self.BACKUPS}"
            if os.path.exists(oldest):
                os.remove(oldest)
            for i in range(self.BACKUPS - 1, 0, -1):
                src, dst = f"{self.log_path}.{i}", f"{self.log_path}.{i + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            os.replace(self.log_path, f"{self.log_path}.1")
        except Exception:
            pass  # 회전 실패가 수집을 막아선 안 된다

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
            self._writes += 1
            if self._writes % self.CHECK_EVERY == 0:
                self._rotate_if_needed()
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kis_auth as ka
from kis_index_future_code import find_kospi200_front_month

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FUTURES_DB_PATH = os.path.join(WORKSPACE_ROOT, "futures_data.db")

# 2026-08-08 11:16 ~ 08-10 22:40, 수집기가 이틀 반 죽어 있었는데 아무도 몰랐다.
# 프로세스는 살아 있어 워치독이 못 잡았고, 로그는 AttributeError만 반복했다.
# 그 사이 ERA는 08-08 06:00자 야간 종가를 "야간 마지막가"로 계속 사용했다.
# → 실패를 밖으로 알리고(_alert), 데이터가 실제로 쌓이는지 감시한다(_note_write).
sys.path.insert(0, WORKSPACE_ROOT)
try:
    # notifier는 임포트 시점에 sys.stdout/stderr를 자기 래퍼로 교체한다. 이 수집기는
    # 위에서 이미 _SafeStream(파일 동시 기록)으로 감싸 두었으므로, 덮어쓰이면 로그가
    # 파일에 안 남는다. 임포트 후 원래 스트림으로 되돌린다.
    _saved_out, _saved_err = sys.stdout, sys.stderr
    import notifier as _notifier
    sys.stdout, sys.stderr = _saved_out, _saved_err
except Exception as _e:      # 알림 실패가 수집 자체를 막아선 안 된다
    _notifier = None
    print(f"[KIS 수집기] 알림 모듈 로드 실패 (수집은 계속): {_e}")

_alerted = set()


def _alert(key, message, once=True):
    """텔레그램으로 알린다. once=True면 같은 key는 프로세스 생애 1회만 보낸다."""
    if once:
        if key in _alerted:
            return
        _alerted.add(key)
    print(f"[KIS 수집기] 알림: {message}")
    if _notifier:
        try:
            _notifier.send_message(f"📡 <b>[야간선물 수집기]</b>\n{message}")
        except Exception as e:
            print(f"[KIS 수집기] 알림 전송 실패: {e}")


_last_write_at = [0.0]        # 마지막으로 DB에 봉이 들어간 시각
STALE_ALERT_SEC = 600         # 세션 중 10분간 신규 봉이 없으면 이상으로 본다


def _in_night_session(now=None):
    """KRX 야간선물 세션(18:00~익일 05:00) 안인가. 경계 근처 오탐을 줄이려 여유를 둔다."""
    h = (now or datetime.now()).hour
    return h >= 19 or h < 4


def _note_write(n):
    if n:
        _last_write_at[0] = time.time()


def _check_freshness():
    """세션 중인데 오랫동안 봉이 안 쌓이면 알린다. 구독은 붙었는데 데이터가 안 오는
    (틱은 오는데 파싱이 깨졌거나, 구독이 조용히 끊긴) 상황을 잡기 위한 것."""
    if not _in_night_session() or not _last_write_at[0]:
        return
    gap = time.time() - _last_write_at[0]
    if gap > STALE_ALERT_SEC:
        _alert("stale",
               f"세션 중인데 {gap / 60:.0f}분째 신규 봉이 없습니다.\n"
               f"구독이 조용히 끊겼을 수 있습니다 — kis/kis_night_collector.log 확인 필요.")


def write_rows(rows):
    """rows: [(code, date_yyyymmddhhmmss, open, high, low, close, volume), ...]
    여러 종목이 섞여 있어도 한 번에 저장할 수 있도록 code를 각 행에 포함시켜 받는다."""
    if not rows:
        return 0
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
    return len(rows)


def backfill_history(code, days_back=30, use_mock=True):
    """inquire_time_fuopchartprice를 시각을 뒤로 옮겨가며 반복 호출해 과거 분봉을 채운다."""
    from inquire_time_fuopchartprice import inquire_time_fuopchartprice

    ka.auth(svr="vps" if use_mock else "prod")

    total_written = 0
    cur_dt = datetime.now()
    cutoff = cur_dt - timedelta(days=days_back)
    consecutive_empty = 0

    while cur_dt > cutoff and consecutive_empty < 3:
        date_str = cur_dt.strftime("%Y%m%d")
        hour_str = cur_dt.strftime("%H%M%S")
        print(f"[KIS 수집기] {code} 분봉 조회 중... (기준 {date_str} {hour_str})")

        try:
            _out1, out2 = inquire_time_fuopchartprice(
                fid_cond_mrkt_div_code="F",
                fid_input_iscd=code,
                fid_hour_cls_code="60",   # 1분봉
                fid_pw_data_incu_yn="Y",  # 과거데이터 포함
                fid_fake_tick_incu_yn="N",
                fid_input_date_1=date_str,
                fid_input_hour_1=hour_str,
            )
        except Exception as e:
            print(f"[KIS 수집기] 조회 오류: {e}")
            break

        if out2 is None or out2.empty:
            consecutive_empty += 1
            print("    수신 0건")
            cur_dt -= timedelta(hours=1)
            continue

        consecutive_empty = 0
        rows = []
        night_count = 0
        for _, r in out2.iterrows():
            try:
                d = f"{str(r['stck_bsop_date']).strip()}{str(r['stck_cntg_hour']).strip()}"
                o, h, l, c = float(r['futs_oprc']), float(r['futs_hgpr']), float(r['futs_lwpr']), float(r['futs_prpr'])
                v = int(float(r.get('cntg_vol', 0) or 0))
            except (KeyError, ValueError):
                continue
            rows.append((code, d, o, h, l, c, v))
            hhmm = d[8:12]
            if hhmm < "0845" or hhmm > "1545":
                night_count += 1

        n = write_rows(rows)
        total_written += n
        print(f"    [DB 저장] {n}건 (그중 야간시간대 {night_count}건) | 누적 {total_written}건")

        # 다음 조회는 이번에 받은 가장 이른 시각 이전으로
        earliest = min(rows, key=lambda x: x[0])
        earliest_dt = datetime.strptime(earliest[0], "%Y%m%d%H%M%S")
        if earliest_dt >= cur_dt:
            break  # 진행이 안 되면 무한루프 방지로 중단
        cur_dt = earliest_dt - timedelta(seconds=1)

        time.sleep(0.3)  # API 호출 과부하 방지

    print(f"[KIS 수집기] 과거 데이터 수집 완료. 총 {total_written}건 저장.")


def collect_realtime(codes, use_mock=True):
    """krx_ngt_futures_ccnl(KRX야간선물 실시간종목체결)을 구독해 1분봉으로 집계, 30초마다 DB 반영.
    codes: 종목코드 하나 또는 리스트. 여러 개를 넘기면 같은 웹소켓 연결(H0MFCNT0)로 동시 구독하고,
    수신 메시지의 futs_shrn_iscd(종목코드) 컬럼으로 어느 종목의 틱인지 구분해 각자 DB에 저장한다."""
    from krx_ngt_futures_ccnl import krx_ngt_futures_ccnl

    if isinstance(codes, str):
        codes = [codes]

    ka.auth(svr="vps" if use_mock else "prod")
    ka.auth_ws(svr="vps" if use_mock else "prod")

    ohlcv_buffer = {}  # key: (code, period_str)
    last_flush = [time.time()]

    def update_buffer(code, price):
        if price <= 0:
            return
        now = datetime.now()
        period_str = now.strftime("%Y%m%d%H%M00")
        key = (code, period_str)
        if key not in ohlcv_buffer:
            ohlcv_buffer[key] = {'o': price, 'h': price, 'l': price, 'c': price, 'v': 1}
        else:
            b = ohlcv_buffer[key]
            b['h'] = max(b['h'], price)
            b['l'] = min(b['l'], price)
            b['c'] = price
            b['v'] += 1

    def flush():
        rows = [(code, d, b['o'], b['h'], b['l'], b['c'], b['v'])
                for (code, d), b in ohlcv_buffer.items()]
        n = write_rows(rows)
        _note_write(n)
        if n:
            print(f"[KIS 수집기] 실시간 버퍼 반영: {n}건")

    def on_result(ws, tr_id, result, data_map):
        try:
            code = str(result['futs_shrn_iscd'].iloc[-1]).strip()
            price = float(result['futs_prpr'].iloc[-1])
            update_buffer(code, price)
        except (KeyError, ValueError, IndexError):
            return
        if time.time() - last_flush[0] > 30:
            flush()
            _check_freshness()
            last_flush[0] = time.time()

    kws = ka.KISWebSocket(api_url="/tryitout")
    kws.subscribe(request=krx_ngt_futures_ccnl, data=codes)
    print(f"[KIS 수집기] {codes} 실시간 구독 시작 (Ctrl+C로 종료)...")
    try:
        kws.start(on_result=on_result)
    finally:
        flush()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="KIS 야간선물 데이터 수집기")
    parser.add_argument('--mode', choices=['history', 'realtime'], required=True)
    parser.add_argument('--code', default=None,
                         help="종목코드 직접 지정(콤마로 여러 개 나열 가능). 생략 시 history는 표준선물, "
                              "realtime은 미니+표준 최근월물을 모두 자동 조회")
    parser.add_argument('--live', action='store_true', help="실전 서버 사용(기본은 모의투자)")
    parser.add_argument('--days', type=int, default=30, help="history 모드: 며칠치 조회할지 (기본 30일)")
    args = parser.parse_args()

    use_mock = not args.live
    print(f"[KIS 수집기] 서버: {'모의투자' if use_mock else '실전투자'}")

    if args.mode == 'history':
        code = args.code
        if not code:
            print("[KIS 수집기] 최근월물 코드 자동 조회 중...")
            code, name = find_kospi200_front_month(os.path.dirname(os.path.abspath(__file__)))
            print(f"[KIS 수집기] 종목: {name} / 코드: {code}")
        backfill_history(code, days_back=args.days, use_mock=use_mock)
    else:
        if args.code:
            codes = [c.strip() for c in args.code.split(",") if c.strip()]
        else:
            # 실거래 시스템(era_order_manager.py)이 실제로 쓰는 건 미니선물이지만, 표준선물도
            # 참고/비교용으로 함께 모아둔다 — 같은 웹소켓 연결로 두 종목을 동시 구독.
            print("[KIS 수집기] 최근월물 코드 자동 조회 중 (미니+표준)...")
            this_dir = os.path.dirname(os.path.abspath(__file__))
            mini_code, mini_name = find_kospi200_front_month(this_dir, product_type='B')
            std_code, std_name = find_kospi200_front_month(this_dir, product_type='1')
            codes = [mini_code, std_code]
            print(f"[KIS 수집기] 종목: {mini_name}(코드:{mini_code}), {std_name}(코드:{std_code})")

        # 실시간 모드는 야간 세션 동안 장시간 무인 실행되므로, 다음날 아침 정지
        # 스케줄 작업(era.pid/tca.pid와 동일한 관례)이 정확히 이 프로세스를 찾아 종료할 수
        # 있도록 PID 파일을 남긴다.
        import atexit
        _pid_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "night_collector.pid")
        try:
            with open(_pid_file, "w") as _f:
                _f.write(str(os.getpid()))
            atexit.register(lambda: os.remove(_pid_file) if os.path.exists(_pid_file) else None)
        except Exception as e:
            print(f"[KIS 수집기] PID 파일 기록 실패: {e}")

        # KISWebSocket 내부 재연결(기본 3회)이 모두 실패하면 예외 없이 그냥 함수가 반환되고
        # 프로세스가 조용히 종료됨 — 2026-07-14 밤 실측: 01:00경 "no close frame received or
        # sent" 이후 그대로 죽어서 남은 야간시간대(01:00~05:00) 데이터가 통째로 누락됐다.
        # collect_realtime()은 정상 동작 중에는 절대 반환되지 않아야 하므로, 반환되면(정상
        # 종료든 예외든) 무조건 재연결 시도로 간주하고 다시 시작한다. 05:15 정지 스케줄
        # 작업이 taskkill로 프로세스를 강제 종료할 때까지 무한 반복. 재시도 간격은 KIS 인증
        # API의 분당 요청 제한(EGW00133, 2026-07-13 실측)에 걸리지 않도록 90초로 둔다.
        # 재시도 간격은 실패 성격에 따라 다르다 (2026-08-10 개편).
        # 종전에는 무슨 실패든 90초 고정이었는데, AppKey가 무효화된 08-08~08-10에는
        # 재발급 전까지 절대 성공할 수 없는 요청을 이틀 반 동안 90초마다 반복했다.
        # 그 결과 KIS의 분당 발급 제한(EGW00133)까지 상시로 건드리는 상태가 됐다.
        RETRY_BASE = 90          # 정상 재연결·속도제한: 기존 간격 유지
        RETRY_MAX = 1800         # 재발급이 필요한 실패: 최대 30분까지 늦춘다
        backoff = RETRY_BASE

        while True:
            try:
                collect_realtime(codes, use_mock=use_mock)
                backoff = RETRY_BASE          # 한 번이라도 붙었으면 간격을 되돌린다
                reason = "수집 루프 정상 종료"
            except ka.KisAuthError as e:
                print(f"[KIS 수집기] 인증 실패: {e}")
                if e.is_invalid_key:
                    # 사람이 키를 재발급하기 전까지는 몇 번을 두드려도 결과가 같다.
                    backoff = min(max(backoff * 2, RETRY_BASE), RETRY_MAX)
                    _alert("invalid_key",
                           f"AppKey가 거부되고 있습니다 ({e.code}).\n"
                           f"재발급 후 <code>kis/update_kis_keys.py</code>로 갱신해야 복구됩니다.\n"
                           f"그때까지 수집이 중단됩니다.")
                elif e.is_rate_limited:
                    backoff = RETRY_BASE       # 시간이 지나면 저절로 풀린다
                else:
                    backoff = min(max(backoff * 2, RETRY_BASE), RETRY_MAX)
                    _alert(f"auth_{e.code}", f"인증 실패: {e}")
                reason = f"인증 실패({e.code or 'unknown'})"
            except Exception as e:
                print(f"[KIS 수집기] 실시간 수집 중 예외 발생: {type(e).__name__}: {e}")
                backoff = min(max(backoff * 2, RETRY_BASE), RETRY_MAX)
                reason = f"예외 {type(e).__name__}"

            print(f"[KIS 수집기] {reason} — {backoff}초 후 재연결 시도...")
            time.sleep(backoff)
