"""
AMATS Leader-Focused Order Manager (V8-Leader)
==================================================================
Inherits from ERAOrderManager to implement BullAnt's "Zero Messy Trades" principles:
- Scrapes/Checks Market Cap >= 200 Billion KRW
- Scrapes/Checks Live Transaction Value >= 50 Billion KRW
- Queries/Checks Theme Rank <= 2 (Absolute Leaders)
Bypasses filters for manual sells.
"""

import sys
import os
import sqlite3
import requests
import re
from bs4 import BeautifulSoup
from PyQt5.QtCore import QThread, pyqtSignal

# Add parent directory to sys.path to import base class
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from era.era_order_manager import ERAOrderManager


class LeaderScreenWorker(QThread):
    """리더종목 필터링(시가총액/거래대금 네이버금융 스크래핑 + 테마순위 DB조회)을
    백그라운드 스레드에서 수행한다. 원래는 메인(Qt/Kiwoom OCX) 스레드에서 동기 실행되어
    장 시작 직후 신호가 몰릴 때 최대 10회의 blocking requests.get()이 Kiwoom OCX의 COM
    메시지펌프를 지연시켰고, 이것이 매일 09:0x경 python.exe가 MSVCR100.dll(Kiwoom OpenAPI
    번들 구버전 CRT) 내부에서 0xc0000417로 죽는 크래시의 유력한 원인으로 지목되어 분리함
    (2026-07-08).
    """
    finished_signal = pyqtSignal(list)  # [(signal_id, new_status_or_None)] — None이면 통과(PENDING 유지)

    def __init__(self, pending_rows, unified_db_path, min_market_cap_billion, min_trade_value_billion):
        super().__init__()
        self.pending_rows = pending_rows
        self.unified_db_path = unified_db_path
        self.min_market_cap_billion = min_market_cap_billion
        self.min_trade_value_billion = min_trade_value_billion

    def run(self):
        results = []
        for sig_id, code, name, strategy_type in self.pending_rows:
            try:
                print(f"\n[ERA-LEADER 검증] {name}({code}) - 필터 스크리닝 시작...")

                # 검증 A: 시가총액 2000억 이상
                mcap = self._get_market_cap_billion(code)
                if mcap < self.min_market_cap_billion:
                    print(f" => [차단] 시가총액 기준 미달 (현재 {mcap:.1f}억 / 기준 {self.min_market_cap_billion:.1f}억)")
                    results.append((sig_id, 'SKIPPED_NOT_LEADER_MCAP'))
                    continue

                # 검증 B: 거래대금 500억 이상
                t_value = self._get_live_trade_value_billion(code)
                if t_value < self.min_trade_value_billion:
                    print(f" => [차단] 거래대금 기준 미달 (현재 {t_value:.1f}억 / 기준 {self.min_trade_value_billion:.1f}억)")
                    results.append((sig_id, 'SKIPPED_NOT_LEADER_VALUE'))
                    continue

                # 검증 C: 테마 내 1~2등주 대장주 검증
                if not self._is_theme_leader_rank_1_or_2(code):
                    print(" => [차단] 테마 내 3순위 이하 마이너 주식 배제")
                    results.append((sig_id, 'SKIPPED_NOT_LEADER_RANK'))
                    continue

                print(f" => [통과] 불개미 주도주 기준 충족! (시총: {mcap:.1f}억, 거래대금: {t_value:.1f}억)")
                results.append((sig_id, None))
            except Exception as e:
                # 스크리닝 자체가 실패하면 필터 없이 통과시켜 신호가 무기한 SCREENING에 묶이지 않게 함
                print(f"[ERA-LEADER 스크리닝 오류] {name}({code}): {e}")
                results.append((sig_id, None))
        self.finished_signal.emit(results)

    def _get_market_cap_billion(self, code):
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            res = requests.get(url, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            market_sum_el = soup.find(id="_market_sum")
            if market_sum_el:
                text = market_sum_el.text.strip().replace('\n', '').replace('\t', '')
                cleaned = text.replace(',', '').replace(' ', '')
                if '조' in cleaned:
                    parts = cleaned.split('조')
                    trillions = int(parts[0]) if parts[0] else 0
                    billions_str = re.sub(r'\D', '', parts[1])
                    billions = int(billions_str) if billions_str else 0
                    total_billion = (trillions * 10000) + billions
                else:
                    total_billion = int(re.sub(r'\D', '', cleaned))
                return float(total_billion)
        except Exception as e:
            print(f"[ERA-LEADER] 시가총액 스크래핑 에러 ({code}): {e}")
        return 0.0

    def _get_live_trade_value_billion(self, code):
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            res = requests.get(url, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            for td in soup.find_all('td'):
                span = td.find('span', class_='sp_txt10') or (td.find('span') and '거래대금' in td.text)
                if span:
                    blind_span = td.find('span', class_='blind')
                    if blind_span:
                        raw_val = float(blind_span.text.strip().replace(',', ''))
                        return raw_val / 1000.0  # 백만 -> 십억 변환
        except Exception as e:
            print(f"[ERA-LEADER] 거래대금 스크래핑 에러 ({code}): {e}")
        return 0.0

    def _is_theme_leader_rank_1_or_2(self, code):
        conn = sqlite3.connect(self.unified_db_path, timeout=30)
        cursor = conn.cursor()
        try:
            # 1. 테마 찾기
            cursor.execute("""
                SELECT theme FROM theme_mapping
                WHERE leader_code = ? OR follower_code = ?
                LIMIT 1
            """, (code, code))
            row = cursor.fetchone()
            if not row:
                return True  # 매핑 없는 신규 주도주는 허용
            theme = row[0]

            # 2. 테마 내 전 종목 코드 추출
            cursor.execute("SELECT DISTINCT leader_code, follower_code FROM theme_mapping WHERE theme = ?", (theme,))
            rows = cursor.fetchall()
            theme_codes = set()
            for r in rows:
                if r[0]:
                    theme_codes.add(r[0])
                if r[1]:
                    theme_codes.add(r[1])

            if not theme_codes:
                return True

            # 3. 각 종목의 당일 등락률 계산 (일봉 기준 시가 대비 종가 변동폭)
            returns = []
            for c in theme_codes:
                cursor.execute("SELECT close, open FROM daily_ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1", (c,))
                p_row = cursor.fetchone()
                if p_row:
                    close, open_p = p_row
                    ret = (close - open_p) / open_p if open_p > 0 else 0.0
                    returns.append((c, ret))

            returns.sort(key=lambda x: x[1], reverse=True)
            top_2_codes = [x[0] for x in returns[:2]]
            return code in top_2_codes
        except Exception as e:
            print(f"[ERA-LEADER] 테마 순위 검증 중 에러 ({code}): {e}")
        finally:
            conn.close()
        return True


class LeaderOrderManager(ERAOrderManager):
    def __init__(self):
        super().__init__()
        print("[ERA-LEADER] 기동 완료 - 불개미식 주도주/대장주 필터링 모드 활성화")
        # Thresholds (Billion KRW)
        self.min_market_cap_billion = 100.0
        self.min_trade_value_billion = 20.0
        self._leader_screen_worker = None
        self.screened_signals = set()  # 이미 스크리닝을 통과하여 PENDING 상태인 신호 ID 추적

    def _poll_stock_signals(self, exit_only=False):
        """
        매수 주문 발송 직전, PENDING 상태의 신호들에 대해 불개미식 주도주 필터를 백그라운드
        스레드에서 적용한다. 조건 미달 신호는 스크리닝 완료 후 DB 상태가 변경되고, 그동안
        메인 스레드는 블로킹 없이 부모 클래스의 주문 실행 메서드를 계속 폴링한다.
        """
        if exit_only or self.stock_total_balance == 0:
            super()._poll_stock_signals(exit_only=exit_only)
            return

        # 이전 스크리닝이 아직 끝나지 않았으면 이번 사이클엔 새로 띄우지 않음 (다음 폴링에서 재시도)
        if self._leader_screen_worker is None or not self._leader_screen_worker.isRunning():
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            try:
                # 1. PENDING 상태 신호 선조회 (수동 매도는 필터 예외 — 건드리지 않고 그대로 둠)
                cursor.execute("SELECT id, code, name, strategy_type FROM signals WHERE status = 'PENDING' LIMIT 5")
                # 이미 스크리닝을 완료한 신호 ID(self.screened_signals)는 재스크리닝하지 않고 건너뜀
                pending_rows = [row for row in cursor.fetchall() if row[3] != 'MANUAL_SELL' and row[0] not in self.screened_signals]

                if pending_rows:
                    ids = [(row[0],) for row in pending_rows]
                    cursor.executemany("UPDATE signals SET status = 'SCREENING' WHERE id = ?", ids)
                    conn.commit()

                    self._leader_screen_worker = LeaderScreenWorker(
                        pending_rows, self.unified_db_path,
                        self.min_market_cap_billion, self.min_trade_value_billion
                    )
                    self._leader_screen_worker.finished_signal.connect(self._on_leader_screen_finished)
                    self._leader_screen_worker.start()
            except Exception as e:
                print(f"[ERA-LEADER DB 작업 에러] {e}")
            finally:
                conn.close()

        # 백그라운드 스크리닝과 무관하게, 이미 통과했거나(PENDING 복귀) 수동매도인 신호는
        # 매 사이클 부모 클래스가 정상 처리 — SCREENING 상태 신호는 WHERE status='PENDING'에
        # 걸리지 않으므로 자동으로 건너뛰어짐
        super()._poll_stock_signals(exit_only=exit_only)

    def _on_leader_screen_finished(self, results):
        """LeaderScreenWorker 완료 시 메인 스레드에서 DB에 결과를 반영 (Qt가 자동으로
        큐드 커넥션으로 처리해주므로 메인 스레드에서 안전하게 실행됨)."""
        conn = sqlite3.connect(self.unified_db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        try:
            for sig_id, new_status in results:
                if new_status is None:
                    # 검증 통과 시, screened_signals에 추가하여 다음 폴링 때 재스크리닝(무한 루프) 방지
                    self.screened_signals.add(sig_id)
                    cursor.execute("UPDATE signals SET status = 'PENDING' WHERE id = ?", (sig_id,))
                    print(f"[ERA-LEADER] 신호 ID {sig_id} 검증 통과 -> PENDING 복구 및 중복 스크리닝 차단 등록")
                else:
                    cursor.execute("UPDATE signals SET status = ? WHERE id = ?", (new_status, sig_id))
                    print(f"[ERA-LEADER] 신호 ID {sig_id} 검증 미달 -> {new_status} 처리")
            conn.commit()
        except Exception as e:
            print(f"[ERA-LEADER 결과 반영 에러] {e}")
        finally:
            conn.close()


if __name__ == "__main__":
    # 독립적으로 기동 가능
    import sys
    from PyQt5.QtWidgets import QApplication

    current_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        print("[ERA] 윈도우 절전 방지 활성화 완료.")
    except Exception as e:
        print(f"[ERA] 절전 방지 활성화 실패: {e}")

    import socket
    # 물리적 소켓 바인딩 락 (Port: 9991) - era_order_manager.py 직접 실행 경로와 동일한 포트를
    # 공유해야 두 실행 방식(순수 base 실행 vs 이 리더 확장판)이 서로를 인식해 중복 기동을 막는다
    # (2026-07-09 최종점검에서 발견: 이 락이 없어서 워치독이 이미 떠있는 프로세스를 죽은 것으로
    # 오판하고 era_order_manager.py를 중복 기동시켜 두 프로세스가 동시에 키움 세션을 잡으려
    # 시도할 위험이 있었음).
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', 9991))
    except socket.error:
        print("[ERA ERROR] 이미 다른 ERA 주문 엔진이 실행 중입니다 (Port 9991 Lock). 실행을 중단합니다.")
        sys.exit(0)

    import atexit
    _pid_file = os.path.join(current_dir, "era.pid")
    try:
        with open(_pid_file, "w") as _f:
            _f.write(str(os.getpid()))
        atexit.register(lambda: os.remove(_pid_file) if os.path.exists(_pid_file) else None)
        print(f"[ERA] PID {os.getpid()} 기록 완료 ({_pid_file})")
    except Exception as e:
        print(f"[ERA] PID 파일 기록 실패: {e}")

    print("==========================================================")
    print("   ERA Order Manager [LEADER] (day 60% & swing 40% Unified)")
    print("==========================================================")

    try:
        app = QApplication(sys.argv)
        manager = LeaderOrderManager()
        # Kiwoom 로그인 및 루프 실행은 부모 클래스의 로직에 의해 구동됨
        sys.exit(app.exec_())
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        print(f"\n[ERA 치명적 오류] {e}\n{err_msg}")
        try:
            with open(os.path.join(current_dir, "era_crash.log"), "w", encoding="utf-8") as f:
                f.write(err_msg)
            print(f"[ERA] 에러 로그 저장: {os.path.join(current_dir, 'era_crash.log')}")
        except Exception:
            pass
        input("[ERA] 종료하려면 Enter 키를 누르세요...")
