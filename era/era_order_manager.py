import os
import sys
import sqlite3
import json
import subprocess
from datetime import datetime
import requests
from bs4 import BeautifulSoup

current_dir = os.path.dirname(os.path.abspath(__file__))

# PyQt5 플러그인 경로를 현재 Python 실행파일 위치에서 자동 감지 (하드코딩 경로 제거)
_exe_dir = os.path.dirname(sys.executable)
_qt_base = os.path.join(_exe_dir, "Lib", "site-packages", "PyQt5")
_qt_plugin_path = os.path.join(_qt_base, "Qt5", "plugins")
if not os.path.exists(_qt_plugin_path):
    _qt_plugin_path = os.path.join(_qt_base, "Qt", "plugins")  # 최신 PyQt5 경로
if os.path.exists(_qt_plugin_path):
    os.environ["QT_PLUGIN_PATH"] = _qt_plugin_path
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_qt_plugin_path, "platforms")

from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer

# 중앙 notifier 모듈 임포트
sys.path.append(os.path.abspath(os.path.join(current_dir, "..")))
try:
    import notifier
except ImportError:
    notifier = None

class ERAOrderManager:
    def __init__(self):
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        
        # 키움 OpenAPI 이벤트 연동
        self.kiwoom.OnEventConnect.connect(self._on_login)
        self.kiwoom.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.kiwoom.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        self.kiwoom.OnReceiveMsg.connect(self._on_receive_msg)
        self.kiwoom.OnReceiveRealData.connect(self._on_receive_real_data)
        
        # 계좌 및 포트폴리오 상태 변수
        self.stock_account = ""
        self.futures_account = ""
        self.portfolio = {}          # 주식 보유 정보 (코드 -> 정보)
        self.futures_positions = {}   # 선물 보유 정보 (코드 -> 정보)
        self.pending_orders = {}      # 주식 미체결/진입 대기
        self.pending_futures_orders = {} # 선물 미체결/진입 대기
        self.system_halted = False
        
        # 자금 정보
        self.stock_total_balance = 0
        self.stock_initial_balance = 0
        self.stock_daily_loss = 0
        
        self.futures_available_balance = 0
        self.futures_margin_cap_ratio = 0.3  # 30% 위탁증거금 캡(Cap) 적용
        
        # 자금 분배율 (기본값)
        self.ratio_day = 0.60
        self.ratio_swing = 0.40
        self.budget_day = 0
        self.budget_swing = 0
        
        self.max_day_positions = 5
        self.max_swing_positions = 3
        
        # 설정 파일 및 데이터 경로 로드
        self.workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
        self.config_path = os.path.join(self.workspace_root, "config", "config.json")
        self.positions_persist_path = os.path.join(self.workspace_root, "era", "era_positions.json")
        self.unified_db_path = os.path.join(self.workspace_root, "unified_data.db")
        self.futures_db_path = os.path.join(self.workspace_root, "futures_data.db")
        
        self.load_config()
        self.load_persisted_positions()
        
        # 서버 연결 초기화
        env_label = "실전매매" if self.environment == "live" else "모의투자"
        print(f"[ERA] 환경: {env_label} (environment={self.environment})")
        server_code = "1" if self.environment == "live" else "2"
        self.kiwoom.dynamicCall("KOA_Functions(QString, QString)", "SetServerGBCode", server_code)
        
        print("[ERA] 키움증권 서버 로그인 요청 중...")
        self.kiwoom.dynamicCall("CommConnect()")
        
        # 타이머 설정
        # 1. 시그널 감시 (2초 주기)
        self.signal_timer = QTimer()
        self.signal_timer.timeout.connect(self.poll_signals)
        
        # 2. 주식 10MA/20MA 실시간 이평 갱신 (10초 주기) — stock/both만
        self.ma_timer = QTimer()
        self.ma_timer.timeout.connect(self.update_day_ma_data)
        if self.trading_mode in ('stock', 'both'):
            self.ma_timer.start(10000)
        
        # 3. 스윙 15:14 종가 5일선 이탈 감시 (1초 주기) — stock/both만
        self.swing_time_timer = QTimer()
        self.swing_time_timer.timeout.connect(self.check_swing_close_time)
        if self.trading_mode in ('stock', 'both'):
            self.swing_time_timer.start(1000)
        
        # 4. 키움 서버 통신 끊김 검사 (60초 주기) — 항상
        self.conn_check_timer = QTimer()
        self.conn_check_timer.timeout.connect(self.check_connection_status)
        self.conn_check_timer.start(60000)
        self.was_disconnected = False
        
        # 5. 매일 시스템 상태 일일 리셋 타이머 — 항상
        self.reset_timer = QTimer()
        self.reset_timer.timeout.connect(self._check_daily_reset)
        self.reset_timer.start(60000)
        
        self.pending_5ma_checks = []
        self.today_5ma_checked = False

        # 6. 단타 신호 스캔 (5분 주기, 09:00~14:00) — stock/both만
        self.day_scan_timer = QTimer()
        self.day_scan_timer.timeout.connect(self._run_day_screening)
        if self.trading_mode in ('stock', 'both'):
            self.day_scan_timer.start(300000)  # 5분

        # 9. 긴급정지 플래그 감시 (1초 주기) — 항상
        self.kill_flag_timer = QTimer()
        self.kill_flag_timer.timeout.connect(self._check_kill_flag)
        self.kill_flag_timer.start(1000)

        # 10. 키움 세션 킵얼라이브 (5분 주기) — 자동 로그아웃 방지
        self.keepalive_timer = QTimer()
        self.keepalive_timer.timeout.connect(self._keepalive_ping)
        self.keepalive_timer.start(300000)  # 5분

        # ── 선물 실시간 K값 변동성 돌파 전략 ─────────────────────────────
        self.futures_strategy_active = False
        self.futures_best_k = 0.5
        self.futures_prev_range = 20.0

        # 선물 손절/익절 설정 (고정 pt)
        self.futures_stop_loss_pt = 2.0   # 고정 손절: 진입가 대비 2.0pt
        self.futures_take_profit_pt = 5.0  # 고정 익절: 진입가 대비 5.0pt

        # 주간 선물 (09:00 ~ 익일 08:45)
        self.futures_day_open     = 0.0
        self.futures_target_long  = float('inf')
        self.futures_target_short = float('-inf')
        self.futures_order_locked = False
        self.futures_day_entry_price = 0.0  # 주간 진입가 기록

        # 야간 선물 (18:00 ~ 익일 04:45)
        self.futures_night_open         = 0.0
        self.futures_night_target_long  = float('inf')
        self.futures_night_target_short = float('-inf')
        self.futures_night_order_locked = False
        self.futures_night_entry_price = 0.0  # 야간 진입가 기록

        # ── STA 통합: 테마 크롤링 + 실시간 OHLCV ─────────────────────────
        self.theme_stocks = {}        # {code: name} 오늘 실시간 구독 종목
        self.ohlcv_buffer = {}        # {code: {period_str: {o,h,l,c,v}}}
        self.theme_crawl_date = ""    # 크롤링 완료 날짜 (YYYY-MM-DD), 날짜 바뀌면 재실행

        # 7. 장전 테마 크롤링 체크 (1분 주기, 08:50) — stock/both만
        self.morning_timer = QTimer()
        self.morning_timer.timeout.connect(self._check_morning_prep)
        if self.trading_mode in ('stock', 'both'):
            self.morning_timer.start(60000)

        # 8. OHLCV 버퍼 → DB 30초 주기 동기화 — stock/both만
        self.ohlcv_flush_timer = QTimer()
        self.ohlcv_flush_timer.timeout.connect(self._flush_ohlcv_buffer)
        if self.trading_mode in ('stock', 'both'):
            self.ohlcv_flush_timer.start(30000)


    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            # config_local.json 로컬 오버라이드 (동기화 제외 파일)
            local_config_path = os.path.join(self.workspace_root, "config", "config_local.json")
            if os.path.exists(local_config_path):
                with open(local_config_path, "r", encoding="utf-8") as f:
                    local_overrides = json.load(f)
                # 로컬 설정으로 덮어쓰기 (중첩 딕셔너리는 1레벨만)
                for key, val in local_overrides.items():
                    if isinstance(val, dict) and isinstance(config.get(key), dict):
                        config[key].update(val)
                    else:
                        config[key] = val
                print(f"[ERA] config_local.json 로컬 오버라이드 적용: {list(local_overrides.keys())}")

            self.environment = config.get("environment", "mock")
            self.trading_mode = config.get("trading_mode", "both")  # stock / futures / both
            self.ratio_day = config.get("budget_allocation", {}).get("stock_day_ratio", 0.60)
            self.ratio_swing = config.get("budget_allocation", {}).get("stock_swing_ratio", 0.40)
            self.config_stock_acc = config.get("accounts", {}).get("stock_account", "")
            self.config_futures_acc = config.get("accounts", {}).get("futures_account", "")

            print(f"[ERA] trading_mode = {self.trading_mode}")
        except Exception as e:
            print(f"[ERA Config Error] {e}")
            self.environment = "mock"
            self.trading_mode = "both"
            self.ratio_day = 0.60
            self.ratio_swing = 0.40
            self.config_stock_acc = ""
            self.config_futures_acc = ""

    def load_persisted_positions(self):
        """가상 파티셔닝(단타 vs 스윙) 정보가 담긴 JSON 복원"""
        self.persisted_strategies = {}
        if os.path.exists(self.positions_persist_path):
            try:
                with open(self.positions_persist_path, "r", encoding="utf-8") as f:
                    self.persisted_strategies = json.load(f)
                print(f"[ERA] 가상 파티셔닝 포지션 복원 완료: {self.persisted_strategies}")
            except Exception as e:
                print(f"[ERA] 포지션 복원 실패: {e}")

    def persist_positions(self):
        """가상 파티셔닝 정보 파일 저장"""
        try:
            data = {code: pos['strategy'] for code, pos in self.portfolio.items()}
            with open(self.positions_persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[ERA] 포지션 저장 실패: {e}")

    def _check_daily_reset(self):
        now = datetime.now()

        # ── 05:00 야간선물 세션 종료 리셋 ──────────────────────────────
        if now.hour == 5 and now.minute == 0:
            self.futures_night_open         = 0.0
            self.futures_night_target_long  = float('inf')
            self.futures_night_target_short = float('-inf')
            self.futures_night_order_locked = False
            self.futures_night_entry_price  = 0.0
            print("[ERA 야간선물] 05:00 세션 종료 — 상태 초기화")

        # ── 09:00 주간선물 세션 시작 리셋 + Kill Switch 해제 ───────────
        if now.hour == 9 and now.minute == 0:
            if self.system_halted or self.stock_daily_loss > 0:
                self.system_halted = False
                self.stock_daily_loss = 0
                print("[ERA Kill Switch 리셋] 새 거래일 시작 - 시스템 재가동, 손익 한도 초기화")
                if notifier:
                    notifier.send_message("🔄 <b>[Kill Switch 자동 해제]</b>\n새 거래일이 시작되어 시스템이 재가동됩니다.")
            self.futures_day_open = 0.0
            self.futures_order_locked = False
            self.futures_day_entry_price = 0.0
            self._load_prev_range()
            print("[ERA 주간선물] 09:00 세션 준비 — 전일 Range 갱신")

        # ── 18:00 야간선물 세션 시작 리셋 ──────────────────────────────
        if now.hour == 18 and now.minute == 0:
            self.futures_night_open         = 0.0
            self.futures_night_target_long  = float('inf')
            self.futures_night_target_short = float('-inf')
            self.futures_night_order_locked = False
            self.futures_night_entry_price  = 0.0
            print("[ERA 야간선물] 18:00 세션 시작 대기 — 상태 초기화")

    def check_connection_status(self):
        state = self.kiwoom.dynamicCall("GetConnectState()")
        now = datetime.now()

        if state == 0:
            if not self.was_disconnected:
                print("🚨 [ERA] 키움증권 서버 통신 끊김 감지!")
                if notifier:
                    notifier.send_message(
                        "🚨 <b>[통신 끊김]</b> 키움증권 서버 연결이 끊어졌습니다.\n"
                        "새벽 서버 점검 중이라면 07:00 이후 자동 재연결합니다."
                    )
                self.was_disconnected = True
                self._reconnect_attempts = 0

            # 07:00 이후 자동 재연결 시도 (점검 종료 후)
            elif now.hour >= 7:
                self._reconnect_attempts = getattr(self, '_reconnect_attempts', 0) + 1
                print(f"[ERA] 자동 재연결 시도 #{self._reconnect_attempts}...")
                self.kiwoom.dynamicCall("CommConnect()")
        else:
            if self.was_disconnected:
                print("✅ [ERA] 키움증권 서버 통신 복구.")
                if notifier:
                    notifier.send_message(
                        "✅ <b>[통신 복구]</b> 키움증권 서버 연결이 정상화되었습니다.\n"
                        "매매 시스템이 재가동됩니다."
                    )
                self.was_disconnected = False
                self._reconnect_attempts = 0
                # 재연결 후 테마 구독 재등록 (실시간 데이터 끊겼을 수 있음)
                QTimer.singleShot(3000, self._register_theme_realtime)

    _LOGIN_ERRORS = {
        -100: "사용자 정보교환 실패 (ID/PW 확인)",
        -101: "서버 접속 실패 (인터넷·방화벽 확인)",
        -102: "버전처리 실패 (영웅문4 재실행 후 opstarter 업데이트 완료 필요)",
        -103: "개인방화벽 실패 (키움 방화벽 예외 추가 필요)",
        -104: "메모리 보호 실패",
        -105: "함수 입력값 오류",
        -106: "통신 연결 종료",
    }

    def _on_login(self, err_code):
        if err_code == 0:
            print("[ERA] 로그인 성공!")
            if notifier:
                env_label = "실전매매" if self.environment == "live" else "모의투자"
                notifier.send_message(f"✅ <b>[ERA 연결 성공]</b> 키움증권 서버 접속 완료 ({env_label})")
            
            # 선물 최근월물 자동 검색
            future_list = self.kiwoom.dynamicCall("GetFutureList()").strip()
            self.real_day_code = "10100000"
            self.real_night_code = "10500000"
            if future_list:
                codes = [c for c in future_list.split(";") if c and c.startswith("101")]
                if codes:
                    self.real_day_code = codes[0]
                    self.real_night_code = "105" + self.real_day_code[3:]
                    print(f" => [선물 최근월물 자동 인식] 주간({self.real_day_code}), 야간({self.real_night_code})")
            
            # ── 계좌 목록 조회 ──────────────────────────────────────────
            raw_accounts = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            accounts = [a.strip() for a in raw_accounts.split(';') if a.strip()]
            is_mock = (self.environment != "live")

            print(f"\n => [전체 계좌 목록] {len(accounts)}개 감지됨")
            for i, acc in enumerate(accounts):
                print(f"    [{i}] {acc}")

            # ── 주식 계좌 감지 (stock/both 모드에서만) ────────────────────
            if self.trading_mode in ('stock', 'both'):
                self.stock_account = self.config_stock_acc
                if not self.stock_account:
                    if is_mock:
                        self.stock_account = accounts[0] if accounts else ""
                    else:
                        for acc in accounts:
                            if acc.endswith('11'):
                                self.stock_account = acc
                                break
                        if not self.stock_account and accounts:
                            self.stock_account = accounts[0]
            else:
                self.stock_account = ""
                print("[ERA] 선물 전용 모드 — 주식 계좌 비활성화")

            # ── 선물 계좌 감지 (futures/both 모드에서만) ──────────────────
            if self.trading_mode in ('futures', 'both'):
                self.futures_account = self.config_futures_acc
                if not self.futures_account:
                    if is_mock:
                        for acc in accounts:
                            if acc != self.stock_account:
                                self.futures_account = acc
                                break
                    else:
                        for acc in accounts:
                            if not acc.endswith('11'):
                                self.futures_account = acc
                                break
                        if not self.futures_account and len(accounts) > 1:
                            self.futures_account = accounts[1]
            else:
                self.futures_account = ""
                print("[ERA] 주식 전용 모드 — 선물 계좌 비활성화")

            mode_tag = "모의투자" if is_mock else "실전매매"
            trading_label = {'stock': '주식 전용', 'futures': '선물 전용', 'both': '주식+선물'}[self.trading_mode]
            print(f"\n => [계좌 셋업 / {mode_tag} / {trading_label}]")
            print(f"    주식 계좌: {self.stock_account or '비활성'}")
            print(f"    선물 계좌: {self.futures_account or '비활성'}")

            # ── 계좌 감지 결과 텔레그램 알림 ────────────────────────────
            acc_list_str = "\n".join(f"  [{i}] <code>{a}</code>" for i, a in enumerate(accounts))
            if notifier:
                notifier.send_message(
                    f"🔑 <b>[계좌 감지 / {mode_tag} / {trading_label}]</b>\n\n"
                    f"<b>전체 계좌 목록:</b>\n{acc_list_str}\n\n"
                    f"{'✅' if self.stock_account else '⬜'} 주식: <code>{self.stock_account or '비활성'}</code>\n"
                    f"{'✅' if self.futures_account else '⬜'} 선물: <code>{self.futures_account or '비활성'}</code>\n\n"
                    f"💡 <i>모드: {trading_label} (config_local.json으로 변경 가능)</i>"
                )

            # ── 예수금 조회 (활성 계좌만) ──────────────────────────────
            if self.stock_account:
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account)
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식예수금조회", "opw00001", 0, "0201")

            if self.futures_account:
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.futures_account)
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선물예수금조회", "opw20010", 0, "2001")

            # ── 기존 주식 보유 종목 조회 (stock/both만) ─────────────────
            if self.stock_account:
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account)
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "계좌평가잔고내역요청", "opw00018", 0, "0202")

            # 선물 K값 전략 초기화 (futures/both만)
            if self.trading_mode in ('futures', 'both'):
                QTimer.singleShot(3000, self._init_futures_strategy)
            # 테마 대장주 실시간 구독 (stock/both만)
            if self.trading_mode in ('stock', 'both'):
                QTimer.singleShot(6000, self._register_theme_realtime)
            
        else:
            desc = self._LOGIN_ERRORS.get(err_code, "알 수 없는 오류")
            print(f"[ERA] 로그인 실패 (에러코드: {err_code}) - {desc}")
            if notifier:
                notifier.send_message(
                    f"🚨 <b>[ERA 로그인 실패]</b>\n"
                    f"• 에러코드: <code>{err_code}</code>\n"
                    f"• 원인: {desc}\n\n"
                    f"조치 후 <code>!시스템시작</code> 으로 재시도하세요."
                )

    def _on_receive_msg(self, screen_no, rqname, trcode, msg):
        print(f"[Kiwoom Msg] {msg}")

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, next_str):
        if rqname == "주식예수금조회":
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "d+2추정예수금").strip()
            if d2_deposit:
                self.stock_total_balance = int(d2_deposit)
                if self.stock_initial_balance == 0:
                    self.stock_initial_balance = self.stock_total_balance
                    
                # 60대 40 기계적 가상 자금 파티셔닝
                self.budget_day = int(self.stock_total_balance * self.ratio_day)
                self.budget_swing = int(self.stock_total_balance * self.ratio_swing)
                
            print(f"\n=> 💰 [주식 가상 자금 파티셔닝]")
            print(f"   - 총 실예수금: {self.stock_total_balance:,}원")
            print(f"   - 단타용(60%): {self.budget_day:,}원 (최대 {self.max_day_positions}종목)")
            print(f"   - 스윙용(40%): {self.budget_swing:,}원 (최대 {self.max_swing_positions}종목)")
            
            # 주식 자금 갱신 시 신호 폴링 개시
            self.signal_timer.start(2000)
            
        elif rqname == "선물예수금조회":
            available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "주문가능현금").strip()
            if not available_cash or int(available_cash) == 0:
                available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "예탁금").strip()
            if available_cash:
                self.futures_available_balance = int(available_cash)
            print(f"\n=> 💸 [선물 계좌 자금]")
            print(f"   - 선물 예수금: {self.futures_available_balance:,}원")
            print(f"   - 30% 캡 적용 가용금액: {int(self.futures_available_balance * self.futures_margin_cap_ratio):,}원")
            
        elif rqname == "계좌평가잔고내역요청":
            rows = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f"\n=> 📦 [기존 주식 포지션 실계좌 연동]")
            for i in range(rows):
                code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "종목번호").strip()
                code = code.replace("A", "")
                name = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "종목명").strip()
                qty = int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "보유수량").strip())
                buy_price = int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "매입가").strip())
                current_price = int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip())
                
                # 저장되어 있던 가상 파티셔닝 정보 복원 (기록 없으면 스윙으로 안전 처리)
                strategy_tag = self.persisted_strategies.get(code, "SWING")
                
                if code not in self.portfolio:
                    self.portfolio[code] = {
                        'name': name,
                        'strategy': strategy_tag,
                        'buy_price': buy_price,
                        'current_price': current_price,
                        'qty': qty,
                        'max_price': current_price,
                        'open_price': buy_price,
                        'super_trend_mode': False,
                        'ma_10': 0, 'ma_20': 0
                    }
                    # 실시간 데이터 감시 등록
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    print(f"   - [{strategy_tag}] {name}({code}) | {qty}주 | 평단: {buy_price:,}원")
            self.export_status()
            
        elif rqname == "스윙일봉5MA조회":
            code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "종목코드").strip()
            if code in self.portfolio and self.portfolio[code]['strategy'] == 'SWING':
                pos = self.portfolio[code]
                closes = []
                for i in range(5):
                    c = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()
                    if c:
                        closes.append(abs(int(c)))
                        
                if len(closes) == 5:
                    ma_5 = sum(closes) / 5
                    current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
                    if current_price == 0:
                        current_price = closes[0]
                        
                    print(f"   => [스윙 종가 검증] {pos['name']} 현재가: {current_price:,} / 5MA: {ma_5:,.1f}")
                    
                    if current_price < ma_5:
                        print(f"   🚨 [스윙 자동 청산] {pos['name']} 5일선 하향 이탈! 시장가 매도.")
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Swing_5MA_Sell]", "0103", self.stock_account, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(f"📉 <b>[스윙 익절/청산] {pos['name']}</b>\n• 종가 5일선 이탈로 실계좌 시장가 전량 청산합니다.")
                    else:
                        print(f"   ✅ [스윙 오버나잇 확정] {pos['name']} 5MA 지지.")

    # ── STA 통합: 테마 크롤링 + 실시간 OHLCV ────────────────────────────

    def _check_morning_prep(self):
        """1분마다 실행 — 08:50 도달 시 테마 크롤링 자동 시작"""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if not (now.weekday() < 5 and now.hour == 8 and 50 <= now.minute <= 59 and self.theme_crawl_date != today_str):
            return
        # STA ThemeTracker가 이미 오늘 데이터를 적재했으면 ERA 크롤 생략
        try:
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='top_volume_theme'")
            if cursor.fetchone():
                cursor.execute("SELECT COUNT(*) FROM top_volume_theme WHERE date = ?", (today_str,))
                count = cursor.fetchone()[0]
                conn.close()
                if count > 0:
                    self.theme_crawl_date = today_str
                    print(f"[ERA] STA가 이미 {count}종목 등록 완료 → ERA 크롤 생략, RSA 기동.")
                    if notifier:
                        notifier.send_message(f"🌅 <b>[08:50 테마 준비 완료]</b>\nSTA 등록 {count}종목 활용 (스마트머니 필터 적용됨)")
                    self._trigger_rsa_premarket()
                    return
            else:
                conn.close()
        except Exception:
            pass
        self._morning_theme_crawl()

    def _morning_theme_crawl(self):
        """네이버 금융 테마 대장주 크롤링 (Kiwoom 불필요 — 순수 HTTP)"""
        _HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        _EXCLUDE = ["KODEX","TIGER","KBSTAR","KINDEX","KOSEF","HANARO","인버스","레버리지","선물","스팩","ETN"]
        leaders = []
        try:
            res = requests.get("https://finance.naver.com/sise/theme.naver", headers=_HEADERS, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")
            themes = [
                {"name": c.text, "url": "https://finance.naver.com" + c["href"]}
                for r in soup.select("table.type_1 tr")
                for c in r.select("td.col_type1 a")
            ]
            for theme in themes[:3]:
                tres = requests.get(theme["url"], headers=_HEADERS, timeout=5)
                tsoup = BeautifulSoup(tres.content, "html.parser")
                count = 0
                for row in tsoup.select("table.type_5 tbody tr"):
                    if count >= 3:
                        break
                    a = row.select_one("td.col_type1 a")
                    if a:
                        sname = a.text.strip()
                        scode = a["href"].split("code=")[1]
                        if not any(kw in sname for kw in _EXCLUDE):
                            leaders.append({"code": scode, "name": sname, "theme": theme["name"]})
                            count += 1
            if not leaders:
                print("[ERA 테마 크롤링] 결과 없음")
                return
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            cursor.execute("""CREATE TABLE IF NOT EXISTS top_volume_theme
                              (date TEXT, code TEXT, name TEXT, volume TEXT, UNIQUE(date, code))""")
            cursor.execute("DELETE FROM top_volume_theme WHERE date = ?", (today,))
            for item in leaders:
                cursor.execute("INSERT OR REPLACE INTO top_volume_theme (date,code,name,volume) VALUES(?,?,?,?)",
                               (today, item["code"], item["name"], item["theme"]))
            conn.commit()
            conn.close()
            self.theme_crawl_date = today
            names = ", ".join(i["name"] for i in leaders)
            print(f"\n[ERA 테마 크롤링] 완료: {names}")
            if notifier:
                notifier.send_message(f"🌅 <b>[08:50 테마 대장주 포착]</b>\n{names}\n로그인 후 실시간 구독 시작")
            self._trigger_rsa_premarket()
        except Exception as e:
            print(f"[ERA 테마 크롤링 오류] {e}")

    def _trigger_rsa_premarket(self):
        """테마 종목 확정 후 RSA 사전 분석 서브프로세스 기동"""
        rsa_script = os.path.join(self.workspace_root, 'rsa', 'rsa_coordinator.py')
        if not os.path.exists(rsa_script):
            print("[ERA→RSA] rsa_coordinator.py 파일을 찾을 수 없습니다.")
            return
        try:
            subprocess.Popen(
                [sys.executable, rsa_script],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            print("[ERA→RSA] 장전 RSA 사전 분석 서브프로세스 기동 완료.")
        except Exception as e:
            print(f"[ERA→RSA] RSA 기동 실패: {e}")

    def _register_theme_realtime(self):
        """오늘 테마 대장주 실시간 데이터 구독 등록"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='top_volume_theme'")
            if not cursor.fetchone():
                conn.close()
                return
            cursor.execute("SELECT code, name FROM top_volume_theme WHERE date = ?", (today,))
            stocks = cursor.fetchall()
            conn.close()
            if not stocks:
                print("[ERA 실시간 구독] 오늘 테마 대장주 없음 (08:50 이전이거나 크롤링 미완료)")
                return
            self.theme_stocks = {code: name for code, name in stocks}
            for code in self.theme_stocks:
                # FID: 10=현재가, 11=체결량, 12=누적거래량, 15=체결시간
                self.kiwoom.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    "THEME_RT", code, "10;11;12;15", "1"
                )
            print(f"\n[ERA 실시간 구독] {len(self.theme_stocks)}종목 등록: {list(self.theme_stocks.values())}")
            if notifier:
                notifier.send_message(
                    f"📡 <b>[실시간 모니터링 시작]</b>\n"
                    f"{', '.join(self.theme_stocks.values())}\n"
                    f"단타 5분 스캔 가동 중"
                )
        except Exception as e:
            print(f"[ERA 실시간 구독 오류] {e}")

    def _update_intraday_ohlcv(self, code, price, tick_vol):
        """실시간 틱 → 3분봉 OHLCV 인메모리 버퍼 갱신 (30초마다 DB 동기화)"""
        now = datetime.now()
        period_min = (now.minute // 3) * 3
        period_str = now.strftime(f"%Y%m%d{now.hour:02d}") + f"{period_min:02d}00"
        if code not in self.ohlcv_buffer:
            self.ohlcv_buffer[code] = {}
        buf = self.ohlcv_buffer[code]
        if period_str not in buf:
            buf[period_str] = {'o': price, 'h': price, 'l': price, 'c': price, 'v': tick_vol}
        else:
            c = buf[period_str]
            if price > c['h']:
                c['h'] = price
            if price < c['l']:
                c['l'] = price
            c['c'] = price
            c['v'] += tick_vol

    def _flush_ohlcv_buffer(self):
        """30초마다 인메모리 OHLCV 버퍼를 DB에 일괄 동기화"""
        if not self.ohlcv_buffer:
            return
        try:
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            cursor.execute("""CREATE TABLE IF NOT EXISTS intraday_ohlcv
                              (code TEXT, date TEXT, open INTEGER, high INTEGER,
                               low INTEGER, close INTEGER, volume INTEGER, UNIQUE(code, date))""")
            for code, periods in self.ohlcv_buffer.items():
                for period_str, c in periods.items():
                    cursor.execute(
                        "REPLACE INTO intraday_ohlcv (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                        (code, period_str, c['o'], c['h'], c['l'], c['c'], c['v'])
                    )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ERA OHLCV 플러시 오류] {e}")

    # ── 선물 K값 변동성 돌파 전략 ────────────────────────────────────────

    def _init_futures_strategy(self):
        """로그인 성공 후 선물 전략 초기화 (주간 + 야간)"""
        self._load_futures_k()
        self._load_prev_range()

        # 주간 선물 실시간 구독
        self.kiwoom.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            "FUTURES_MON", self.real_day_code, "10;11;12;15", "0"
        )
        # 야간 선물 실시간 구독 (같은 스크린에 추가)
        self.kiwoom.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            "FUTURES_MON", self.real_night_code, "10;11;12;15", "1"
        )
        self.futures_strategy_active = True
        print(f"\n[ERA 선물 전략] K={self.futures_best_k:.2f} | 전일Range={self.futures_prev_range:.2f}pt")
        print(f"  ▶ 주간 구독: {self.real_day_code}  |  야간 구독: {self.real_night_code}")
        if notifier:
            notifier.send_message(
                f"📊 <b>[선물 전략 대기 중]</b>\n"
                f"• K값: {self.futures_best_k:.2f} | 전일 Range: {self.futures_prev_range:.2f}pt\n"
                f"• 주간 ({self.real_day_code}): 09:00 시초가 → 익일 08:45 청산\n"
                f"• 야간 ({self.real_night_code}): 18:00 시초가 → 익일 04:45 청산"
            )

    def _load_futures_k(self):
        """active_strategy.json 에서 최적 K값 로드"""
        strategy_file = os.path.join(self.workspace_root, "config", "active_strategy.json")
        try:
            with open(strategy_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.futures_best_k = float(data.get("best_k", 0.5))
        except Exception:
            self.futures_best_k = 0.5

    def _load_prev_range(self):
        """futures_data.db 에서 전일 고저폭(Range) 계산"""
        try:
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            # 날짜별 일봉 집계 → 가장 최근 완성된 전일 데이터
            # 주의: date 컬럼이 '20260519154500' 형식이므로 date() 대신 SUBSTR 사용
            cursor.execute("""
                SELECT SUBSTR(date, 1, 8) as d, MAX(high) as h, MIN(low) as l
                FROM futures_ohlcv WHERE code = ?
                GROUP BY SUBSTR(date, 1, 8) ORDER BY d DESC LIMIT 2
            """, (self.real_day_code,))
            rows = cursor.fetchall()
            conn.close()
            if len(rows) >= 2:
                prev_h, prev_l = rows[1][1], rows[1][2]
                calc = prev_h - prev_l
                if calc > 0:
                    self.futures_prev_range = calc
        except Exception as e:
            print(f"[ERA 선물] 전일 Range 로드 실패: {e}")

    def _process_futures_tick(self, code, current_price):
        """실시간 선물 현재가 수신 — 주간/야간 세션 분리 처리"""
        if not self.futures_strategy_active or current_price <= 0:
            return

        is_night = (code == getattr(self, 'real_night_code', '10500000'))
        now = datetime.now()

        if is_night:
            self._process_night_tick(code, current_price, now)
        else:
            self._process_day_tick(code, current_price, now)

    def _process_day_tick(self, code, current_price, now):
        """주간 선물 전략 (09:00 진입 → 익일 08:45 청산, 2pt 손절 / 5pt 익절)"""
        pos_key = "KOSPI200"

        # 09:00 시초가 확정 (09:00~09:04 첫 틱 수신 시)
        if now.hour == 9 and now.minute < 5 and self.futures_day_open == 0:
            self.futures_day_open     = current_price
            self.futures_target_long  = current_price + self.futures_prev_range * self.futures_best_k
            self.futures_target_short = current_price - self.futures_prev_range * self.futures_best_k
            print(f"\n[주간선물] ✅ 09:00 시초가: {current_price:.2f}pt")
            print(f"  LONG목표: {self.futures_target_long:.2f}  SHORT목표: {self.futures_target_short:.2f}")
            print(f"  손절: {self.futures_stop_loss_pt}pt  익절: {self.futures_take_profit_pt}pt")
            if notifier:
                notifier.send_message(
                    f"🌅 <b>[주간선물 목표가]</b>\n"
                    f"• 시초가: {current_price:.2f}pt\n"
                    f"• LONG ▲ {self.futures_target_long:.2f}pt\n"
                    f"• SHORT ▼ {self.futures_target_short:.2f}pt\n"
                    f"• 손절: {self.futures_stop_loss_pt}pt | 익절: {self.futures_take_profit_pt}pt"
                )

        if self.futures_day_open == 0:
            return

        # 08:45~08:55 익일 장전 강제 청산
        if now.hour == 8 and 45 <= now.minute <= 55:
            if pos_key in self.futures_positions:
                pos = self.futures_positions[pos_key]
                print(f"[주간선물] ⏰ 08:45 시간 청산 실행")
                self._execute_futures_direct("LONG_EXIT" if pos["type"] == "LONG" else "SHORT_EXIT",
                                             current_price, code, pos_key)
                self.futures_day_entry_price = 0.0
            return

        # ── 포지션 보유 중: 손절/익절 감시 ──
        if pos_key in self.futures_positions:
            pos = self.futures_positions[pos_key]
            entry = self.futures_day_entry_price
            if entry > 0:
                if pos['type'] == 'LONG':
                    pnl_pt = current_price - entry
                    if pnl_pt <= -self.futures_stop_loss_pt:
                        print(f"[주간선물] 🛑 LONG 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                        self.futures_day_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[주간선물 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f}")
                        return
                    elif pnl_pt >= self.futures_take_profit_pt:
                        print(f"[주간선물] 🎯 LONG 익절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 수익:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                        self.futures_day_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[주간선물 익절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f}")
                        return
                elif pos['type'] == 'SHORT':
                    pnl_pt = entry - current_price
                    if pnl_pt <= -self.futures_stop_loss_pt:
                        print(f"[주간선물] 🛑 SHORT 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                        self.futures_day_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[주간선물 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f}")
                        return
                    elif pnl_pt >= self.futures_take_profit_pt:
                        print(f"[주간선물] 🎯 SHORT 익절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 수익:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                        self.futures_day_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[주간선물 익절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f}")
                        return
            return  # 포지션 보유 중이면 신규 진입 불가

        # ── 신규 진입 조건 ──
        if not self.futures_order_locked and not self.system_halted:
            if current_price >= self.futures_target_long:
                self.futures_day_entry_price = current_price
                self._execute_futures_direct("LONG_ENTER", current_price, code, pos_key)
            elif current_price <= self.futures_target_short:
                self.futures_day_entry_price = current_price
                self._execute_futures_direct("SHORT_ENTER", current_price, code, pos_key)

    def _process_night_tick(self, code, current_price, now):
        """야간 선물 전략 (18:00 진입 → 익일 04:45 청산, 2pt 손절 / 5pt 익절)"""
        pos_key = "KOSPI200_NIGHT"

        # 18:00 시초가 확정 (18:00~18:04 첫 틱 수신 시)
        if now.hour == 18 and now.minute < 5 and self.futures_night_open == 0:
            self.futures_night_open         = current_price
            self.futures_night_target_long  = current_price + self.futures_prev_range * self.futures_best_k
            self.futures_night_target_short = current_price - self.futures_prev_range * self.futures_best_k
            print(f"\n[야간선물] ✅ 18:00 시초가: {current_price:.2f}pt")
            print(f"  LONG목표: {self.futures_night_target_long:.2f}  SHORT목표: {self.futures_night_target_short:.2f}")
            print(f"  손절: {self.futures_stop_loss_pt}pt  익절: {self.futures_take_profit_pt}pt")
            if notifier:
                notifier.send_message(
                    f"🌙 <b>[야간선물 목표가]</b>\n"
                    f"• 시초가: {current_price:.2f}pt\n"
                    f"• LONG ▲ {self.futures_night_target_long:.2f}pt\n"
                    f"• SHORT ▼ {self.futures_night_target_short:.2f}pt\n"
                    f"• 손절: {self.futures_stop_loss_pt}pt | 익절: {self.futures_take_profit_pt}pt"
                )

        if self.futures_night_open == 0:
            return

        # 04:45~04:55 야간장 마감 전 강제 청산
        if now.hour == 4 and 45 <= now.minute <= 55:
            if pos_key in self.futures_positions:
                pos = self.futures_positions[pos_key]
                print(f"[야간선물] ⏰ 04:45 시간 청산 실행")
                self._execute_futures_direct("LONG_EXIT" if pos["type"] == "LONG" else "SHORT_EXIT",
                                             current_price, code, pos_key)
                self.futures_night_entry_price = 0.0
            return

        # ── 포지션 보유 중: 손절/익절 감시 ──
        if pos_key in self.futures_positions:
            pos = self.futures_positions[pos_key]
            entry = self.futures_night_entry_price
            if entry > 0:
                if pos['type'] == 'LONG':
                    pnl_pt = current_price - entry
                    if pnl_pt <= -self.futures_stop_loss_pt:
                        print(f"[야간선물] 🛑 LONG 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[야간선물 손절]</b> {pnl_pt:+.2f}pt")
                        return
                    elif pnl_pt >= self.futures_take_profit_pt:
                        print(f"[야간선물] 🎯 LONG 익절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 수익:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[야간선물 익절]</b> {pnl_pt:+.2f}pt")
                        return
                elif pos['type'] == 'SHORT':
                    pnl_pt = entry - current_price
                    if pnl_pt <= -self.futures_stop_loss_pt:
                        print(f"[야간선물] 🛑 SHORT 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[야간선물 손절]</b> {pnl_pt:+.2f}pt")
                        return
                    elif pnl_pt >= self.futures_take_profit_pt:
                        print(f"[야간선물] 🎯 SHORT 익절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 수익:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[야간선물 익절]</b> {pnl_pt:+.2f}pt")
                        return
            return  # 포지션 보유 중이면 신규 진입 불가

        # ── 신규 진입 조건 ──
        if not self.futures_night_order_locked and not self.system_halted:
            if current_price >= self.futures_night_target_long:
                self.futures_night_entry_price = current_price
                self._execute_futures_direct("LONG_ENTER", current_price, code, pos_key)
            elif current_price <= self.futures_night_target_short:
                self.futures_night_entry_price = current_price
                self._execute_futures_direct("SHORT_ENTER", current_price, code, pos_key)

    def _execute_futures_direct(self, signal_type, current_price, order_code, pos_key):
        """선물 주문 직접 집행 (주간/야간 공용 — DB 신호 우회)"""
        is_night = (pos_key == "KOSPI200_NIGHT")
        lock_attr = "futures_night_order_locked" if is_night else "futures_order_locked"

        if getattr(self, lock_attr):
            return
        setattr(self, lock_attr, True)

        direction_map = {
            "LONG_ENTER":  (1, "LONG 진입 📈"),
            "SHORT_ENTER": (2, "SHORT 진입 📉"),
            "LONG_EXIT":   (2, "LONG 청산 📤"),
            "SHORT_EXIT":  (1, "SHORT 청산 📤"),
        }
        trade_dir, label = direction_map.get(signal_type, (None, ""))
        if trade_dir is None:
            setattr(self, lock_attr, False)
            return

        # 수량 계산
        if "EXIT" in signal_type and pos_key in self.futures_positions:
            qty = self.futures_positions[pos_key].get("qty", 1)
        else:
            margin_per = current_price * 250000 * 0.10
            safe_budget = self.futures_available_balance * self.futures_margin_cap_ratio
            qty = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1

        session_label = "야간" if is_night else "주간"
        print(f"\n[{session_label}선물 주문] {label} | {current_price:.2f}pt | {qty}계약 | {order_code}")

        res = self.kiwoom.dynamicCall(
            "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
            ["FuturesLive", "0200", self.futures_account, order_code, trade_dir, "03", "", qty, "0", ""]
        )

        if res == 0:
            if "EXIT" in signal_type:
                setattr(self, lock_attr, False)
            if notifier:
                icon = "🌙" if is_night else "☀️"
                notifier.send_message(
                    f"{icon} <b>[{session_label}선물 {label}]</b>\n"
                    f"• 가격: {current_price:.2f}pt | {qty}계약\n"
                    f"• K값: {self.futures_best_k:.2f}"
                )
        else:
            print(f"  => 선물 주문 실패 (res={res})")
            setattr(self, lock_attr, False)

    # ── 주식 단타 신호 스캐너 ────────────────────────────────────────────

    def _run_day_screening(self):
        """5분마다 intraday_ohlcv + top_volume_theme 기반 단타 진입 신호 생성"""
        now = datetime.now()
        if not (9 <= now.hour < 14):  # 09:00 ~ 14:00 사이에만 실행
            return
        if self.system_halted:
            return
        if not os.path.exists(self.unified_db_path):
            return

        try:
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            today = now.strftime("%Y-%m-%d")

            # 오늘 테마 추적기가 포착한 종목 목록
            cursor.execute(
                "SELECT DISTINCT code, name FROM top_volume_theme WHERE date = ?", (today,)
            )
            targets = cursor.fetchall()
            if not targets:
                conn.close()
                return

            signals_generated = 0
            for code, name in targets:
                # 이미 보유 중이거나 대기 중인 신호가 있으면 스킵
                if code in self.portfolio or code in self.pending_orders:
                    continue
                cursor.execute(
                    "SELECT COUNT(*) FROM signals WHERE code = ? AND status = 'PENDING'", (code,)
                )
                if cursor.fetchone()[0] > 0:
                    continue

                # 최근 20개 3분봉 데이터 조회 (오늘 날짜 한정 필터 적용)
                today_prefix = now.strftime("%Y%m%d")
                cursor.execute(
                    "SELECT close, volume, open FROM intraday_ohlcv "
                    "WHERE code = ? AND date LIKE ? ORDER BY date DESC LIMIT 20",
                    (code, today_prefix + "%")
                )
                candles = cursor.fetchall()
                if len(candles) < 5:  # 15분(5봉)부터 분석 가능
                    continue

                current_price  = candles[0][0]
                current_volume = candles[0][1]
                avg_volume = sum(c[1] for c in candles[1:]) / (len(candles) - 1) if len(candles) > 1 else 1
                day_open = candles[-1][2]  # 가장 오래된 봉의 시가 = 당일 시초가 근사값

                if day_open <= 0 or current_price <= 0:
                    continue

                is_breakout    = current_price >= day_open * 1.02       # 시가 대비 +2% 돌파
                is_vol_surge   = avg_volume > 0 and current_volume >= avg_volume * 1.5
                change_pct = (current_price / day_open - 1) * 100

                if is_breakout and is_vol_surge:
                    cursor.execute(
                        "INSERT INTO signals (code, name, strategy_type, price, open_price, status) "
                        "VALUES (?, ?, 'DAY', ?, ?, 'PENDING')",
                        (code, name, current_price, day_open)
                    )
                    signals_generated += 1
                    print(f"\n[단타 신호] {name}({code}) "
                          f"현재:{current_price:,} 시가대비:{change_pct:+.1f}% "
                          f"거래량:{current_volume/avg_volume:.1f}x")
                    if notifier:
                        notifier.send_message(
                            f"🔥 <b>[단타 진입 신호]</b> {name}\n"
                            f"• 현재가: {current_price:,}원 ({change_pct:+.1f}%)\n"
                            f"• 거래량: {current_volume/avg_volume:.1f}x 평균"
                        )

            conn.commit()
            conn.close()

            if signals_generated:
                print(f"[단타 스캔 완료] {signals_generated}개 신호 생성")

        except Exception as e:
            print(f"[ERA 단타 스캔 오류] {e}")

    def export_status(self):
        """TCA 에이전트와 상태를 실시간으로 공유하기 위해 JSON 저장 (모드별 파일 분리)"""
        status_data = {
            "environment": self.environment,
            "trading_mode": self.trading_mode,
            "stock_account": self.stock_account,
            "futures_account": self.futures_account,
            "total_balance": self.stock_total_balance,
            "budget_day": self.budget_day,
            "budget_swing": self.budget_swing,
            "daily_realized_loss": self.stock_daily_loss,
            "portfolio": self.portfolio,
            "futures_balance": self.futures_available_balance,
            "futures_positions": self.futures_positions,
            "futures_strategy": {
                "K": self.futures_best_k,
                "prev_range": self.futures_prev_range,
                "stop_loss_pt": self.futures_stop_loss_pt,
                "take_profit_pt": self.futures_take_profit_pt,
                "day_entry_price": self.futures_day_entry_price,
                "night_entry_price": self.futures_night_entry_price,
            },
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        # 모드별 파일명 분리 (2대PC 동기화 충돌 방지)
        filenames = {
            "stock": "system_status_stock.json",
            "futures": "system_status_futures.json",
            "both": "system_status.json",
        }
        filename = filenames.get(self.trading_mode, "system_status.json")
        try:
            status_dir = os.path.join(self.workspace_root, "tca")
            if not os.path.exists(status_dir):
                os.makedirs(status_dir)
            with open(os.path.join(status_dir, filename), "w", encoding="utf-8") as f:
                json.dump(status_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ERA status 내보내기 오류] {e}")

    def update_day_ma_data(self):
        """단타 종목들의 실시간 차트 10MA/20MA 추적 갱신"""
        self.export_status()
        day_codes = [c for c, p in self.portfolio.items() if p['strategy'] == 'DAY']
        if not day_codes:
            return
            
        try:
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            for code in day_codes:
                cursor.execute("SELECT close FROM intraday_ohlcv WHERE code = ? ORDER BY date DESC LIMIT 20", (code,))
                rows = cursor.fetchall()
                if len(rows) >= 10:
                    closes = [row[0] for row in reversed(rows)]
                    ma_10 = sum(closes[-10:]) / 10
                    prev_ma_10 = self.portfolio[code].get('ma_10', ma_10)
                    self.portfolio[code]['prev_ma_10'] = prev_ma_10
                    self.portfolio[code]['ma_10'] = ma_10
                    self.portfolio[code]['ma_10_is_up'] = ma_10 > prev_ma_10
                    
                    if len(rows) >= 20:
                        self.portfolio[code]['ma_20'] = sum(closes[-20:]) / 20
            conn.close()
        except Exception as e:
            print(f"[ERA update_day_ma_data 오류] {e}")

    def check_swing_close_time(self):
        now = datetime.now()
        if now.hour == 15 and now.minute >= 14 and not self.today_5ma_checked:
            self.today_5ma_checked = True
            print("\n[⏰ ERA 종가 익절 감시] 15:14+ 스윙 종목 5MA 체크를 시작합니다.")
            self.pending_5ma_checks = [c for c, p in self.portfolio.items() if p['strategy'] == 'SWING']
            self._request_next_5ma()
        elif now.hour < 9:
            self.today_5ma_checked = False

    def _request_next_5ma(self):
        if not self.pending_5ma_checks:
            return
        code = self.pending_5ma_checks.pop(0)
        today = datetime.now().strftime("%Y%m%d")
        
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "기준일자", today)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "스윙일봉5MA조회", "opt10081", 0, "1082")
        
        if self.pending_5ma_checks:
            QTimer.singleShot(1000, self._request_next_5ma)

    def poll_signals(self):
        """DB 시그널 테이블(주식 & 선물) 통합 감시 및 라우팅 (trading_mode 기반 분기)"""
        if self.system_halted:
            return
            
        # 1. 주식 시그널 감시 (stock/both만)
        if self.trading_mode in ('stock', 'both') and os.path.exists(self.unified_db_path):
            self._poll_stock_signals()
            
        # 2. 선물 시그널 감시 (futures/both만)
        if self.trading_mode in ('futures', 'both') and os.path.exists(self.futures_db_path):
            self._poll_futures_signals()

    def _poll_stock_signals(self):
        conn = sqlite3.connect(self.unified_db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, code, name, strategy_type, price, open_price FROM signals WHERE status = 'PENDING' LIMIT 3")
            rows = cursor.fetchall()
            
            for row in rows:
                signal_id, code, name, strategy_type, price, open_price = row
                print(f"\n[🚨 주식 신규 신호 감지] {name}({code}) | 유형: {strategy_type}")
                
                # 중복 진입 검사
                if code in self.portfolio or code in self.pending_orders:
                    print(" => [거절] 이미 포트폴리오에 있거나 매매 집행 중입니다.")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_DUPLICATE' WHERE id = ?", (signal_id,))
                    continue

                # 가상 자금 파티셔닝 제한 적용
                if strategy_type == 'DAY':
                    day_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] == 'DAY'])
                    if day_pos_count >= self.max_day_positions:
                        print(" => [거절] 단타 보유 슬롯 초과")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                        continue
                    budget_per_stock = self.budget_day // self.max_day_positions
                elif strategy_type == 'SWING':
                    swing_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] == 'SWING'])
                    if swing_pos_count >= self.max_swing_positions:
                        print(" => [거절] 스윙 보유 슬롯 초과")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                        continue
                    budget_per_stock = self.budget_swing // self.max_swing_positions
                elif strategy_type == 'MANUAL_SELL':
                    # 수동 매도 처리
                    if code in self.portfolio and not self.portfolio[code].get('sell_ordered'):
                        pos = self.portfolio[code]
                        qty = pos['qty']
                        pos['sell_ordered'] = True
                        print(f" => [수동 매도 집행] {name}({code}) 시장가 전량 청산 ({qty}주)")
                        res = self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Manual_Sell]", "0103", self.stock_account, 2, code, qty, 0, "03", ""]
                        )
                        if res == 0:
                            cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                        else:
                            cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                            pos['sell_ordered'] = False
                    else:
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_NOT_OWNED' WHERE id = ?", (signal_id,))
                    continue
                else:
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_UNKNOWN' WHERE id = ?", (signal_id,))
                    continue
                    
                safe_price = price * 1.3  # 증거금 여유 계산
                qty = int(budget_per_stock // safe_price)
                
                if qty <= 0:
                    print(" => [거절] 가상 파티셔닝 예수금 부족")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_NO_FUNDS' WHERE id = ?", (signal_id,))
                    continue
                    
                # 2차 관문 필터링: RSA 종합 평점 조회 연동 (차후 RSA 개발 완료 시 완전 활성화)
                # 만약 research_reports 테이블이 존재하면 70점 미만 필터링
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_reports'")
                if cursor.fetchone():
                    cursor.execute("SELECT score FROM research_reports WHERE code = ? ORDER BY id DESC LIMIT 1", (code,))
                    rep = cursor.fetchone()
                    if rep is None:
                        print(f" => [보류] RSA 종합 리서치 미평가 종목 — 무검증 진입 차단")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_RSA_NOT_EVALUATED' WHERE id = ?", (signal_id,))
                        continue
                    if rep[0] < 70:
                        print(f" => [거절] RSA 종합 리서치 점수 부족 ({rep[0]}점 / 기준 70점)")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_RSA_SCORE_LOW' WHERE id = ?", (signal_id,))
                        continue

                print(f" => [실계좌 라우팅 승인] 할당금액: {budget_per_stock:,}원 | 수량: {qty}주")
                self.pending_orders[code] = {'qty': qty, 'price': price, 'type': 'BUY', 'strategy': strategy_type, 'open_price': open_price}
                
                clean_code = str(code).strip().zfill(6)
                res = self.kiwoom.dynamicCall(
                    "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                    ["[ERA_Stock_Buy]", "0101", self.stock_account, 1, clean_code, qty, 0, "03", ""]
                )
                
                if res == 0:
                    cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                else:
                    cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                    del self.pending_orders[code]
                    
            conn.commit()
        except sqlite3.OperationalError as e:
            print(f"[ERA 주식 폴링 에러] {e}")
        finally:
            conn.close()

    def _poll_futures_signals(self):
        conn = sqlite3.connect(self.futures_db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, code, signal_type, price FROM signals WHERE status = 'PENDING' LIMIT 1")
            row = cursor.fetchone()
            if row:
                signal_id, code, signal_type, price = row
                print(f"\n[🚨 선물 신규 신호 감지] {code} | {signal_type} | 현재가: {price}")
                
                # 선물 1계약 위탁증거금 계산
                margin_per_contract = price * 250000 * 0.10  # 승수 25만, 위탁증거금률 10%
                safe_budget = self.futures_available_balance * self.futures_margin_cap_ratio
                qty = int(safe_budget // margin_per_contract)
                
                # 최소 1계약 보장
                if qty == 0 and self.futures_available_balance >= (margin_per_contract * 1.2):
                    qty = 1
                    print("  => [선물 안전 마진 예외] 실잔고로 최소 1계약 진입 보장")
                    
                if qty <= 0:
                    print("  => [거절] 선물 위탁증거금 부족")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_NO_FUNDS' WHERE id = ?", (signal_id,))
                else:
                    # LONG 진입/청산: 매수/매도 방향 결정
                    # Kiwoom SendOrderFO lOrdKind: 1=신규매수, 2=신규매도
                    if signal_type == "LONG_ENTER":
                        trade_dir = 1   # 신규매수 (롱 진입)
                    elif signal_type == "SHORT_ENTER":
                        trade_dir = 2   # 신규매도 (숏 진입)
                    elif signal_type == "LONG_EXIT":
                        trade_dir = 2   # 신규매도 (롱 청산)
                    elif signal_type == "SHORT_EXIT":
                        trade_dir = 1   # 신규매수 (숏 청산)
                    else:
                        cursor.execute("UPDATE signals SET status = 'ERROR_UNKNOWN' WHERE id = ?", (signal_id,))
                        conn.commit()
                        conn.close()
                        return

                    # 최근월물 실제 코드로 교환
                    order_code = code
                    if code == "10100000":
                        order_code = getattr(self, 'real_day_code', "10100000")
                    elif code == "10500000":
                        order_code = getattr(self, 'real_night_code', "10500000")

                    print(f"  => [선물 실계좌 전송] SendOrderFO 전송 (trade_dir:{trade_dir}, 수량:{qty}, 코드:{order_code})")
                    res = self.kiwoom.dynamicCall(
                        "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
                        ["FuturesOrder", "0101", self.futures_account, order_code, trade_dir, "03", "", qty, "0", ""]
                    )
                    if res == 0:
                        cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                    else:
                        cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
            conn.commit()
        except sqlite3.OperationalError as e:
            print(f"[ERA 선물 폴링 에러] {e}")
        finally:
            conn.close()

    def _on_receive_chejan_data(self, gubun, item_cnt, fid_list):
        if gubun == "0":
            status = self.kiwoom.dynamicCall("GetChejanData(int)", 913).strip()
            name = self.kiwoom.dynamicCall("GetChejanData(int)", 302).strip()
            code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().replace("A", "")
            
            if status == "체결":
                exec_price = float(self.kiwoom.dynamicCall("GetChejanData(int)", 910).strip())
                exec_qty = int(self.kiwoom.dynamicCall("GetChejanData(int)", 911).strip())
                order_gubun = self.kiwoom.dynamicCall("GetChejanData(int)", 905).strip()
                
                # 선물 체결 감지 (코드 길이 또는 "KOSPI" 이름 감지)
                if len(code) > 6 or "KOSPI" in name or "선물" in name:
                    is_night_fill = (code == getattr(self, 'real_night_code', '10500000'))
                    pos_key = "KOSPI200_NIGHT" if is_night_fill else "KOSPI200"
                    session_label = "야간" if is_night_fill else "주간"
                    print(f"[{session_label}선물 실체결 확정] {name}({code}) | {exec_price} | {exec_qty}계약 | {order_gubun}")
                    if "매수" in order_gubun:
                        if pos_key not in self.futures_positions:
                            self.futures_positions[pos_key] = {'type': 'LONG', 'qty': exec_qty, 'price': exec_price}
                            # 실체결가로 손절/익절 기준 갱신
                            if is_night_fill:
                                self.futures_night_entry_price = exec_price
                            else:
                                self.futures_day_entry_price = exec_price
                        else:
                            if self.futures_positions[pos_key]['type'] == 'SHORT':
                                self.futures_positions[pos_key]['qty'] -= exec_qty
                                if self.futures_positions[pos_key]['qty'] <= 0:
                                    del self.futures_positions[pos_key]
                            else:
                                self.futures_positions[pos_key]['qty'] += exec_qty
                        if notifier:
                            notifier.send_message(f"💰 <b>[{session_label}선물 매수 체결] 코스피200</b>\n• 체결가: {exec_price:,.2f}pt\n• 수량: {exec_qty}계약\n• 손절: -{self.futures_stop_loss_pt}pt | 익절: +{self.futures_take_profit_pt}pt")

                    elif "매도" in order_gubun:
                        if pos_key not in self.futures_positions:
                            self.futures_positions[pos_key] = {'type': 'SHORT', 'qty': exec_qty, 'price': exec_price}
                            # 실체결가로 손절/익절 기준 갱신
                            if is_night_fill:
                                self.futures_night_entry_price = exec_price
                            else:
                                self.futures_day_entry_price = exec_price
                        else:
                            if self.futures_positions[pos_key]['type'] == 'LONG':
                                self.futures_positions[pos_key]['qty'] -= exec_qty
                                if self.futures_positions[pos_key]['qty'] <= 0:
                                    del self.futures_positions[pos_key]
                            else:
                                self.futures_positions[pos_key]['qty'] += exec_qty
                        if notifier:
                            notifier.send_message(f"📉 <b>[{session_label}선물 매도 체결] 코스피200</b>\n• 체결가: {exec_price:,.2f}pt\n• 수량: {exec_qty}계약\n• 손절: -{self.futures_stop_loss_pt}pt | 익절: +{self.futures_take_profit_pt}pt")

                    self.export_status()
                    return

                # 주식 체결 처리
                print(f"[주식 실체결 확정] {name}({code}) | {exec_price:,.0f}원 | {exec_qty}주 | {order_gubun}")
                if "매수" in order_gubun:
                    if code not in self.portfolio:
                        pending = self.pending_orders.get(code, {})
                        strat = pending.get('strategy', 'SWING')
                        open_p = pending.get('open_price', exec_price)
                        
                        self.portfolio[code] = {
                            'name': name, 'strategy': strat, 'buy_price': exec_price, 'qty': 0,
                            'current_price': exec_price, 'max_price': exec_price, 'open_price': open_p,
                            'super_trend_mode': False, 'ma_10': 0, 'ma_20': 0
                        }
                        
                    self.portfolio[code]['qty'] += exec_qty
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    self.persist_positions() # 가상 파티셔닝 구조 보존
                    
                    if notifier:
                        strat_name = "단타(가상)" if self.portfolio[code]['strategy'] == 'DAY' else "스윙(가상)"
                        notifier.send_message(f"💰 <b>[{strat_name} 매수 체결] {name}</b>\n• 체결가: {exec_price:,.0f}원\n• 수량: {exec_qty}주\n• 계좌: {self.stock_account}")
                        
                elif "매도" in order_gubun:
                    if code in self.portfolio:
                        pos = self.portfolio[code]
                        strat = pos['strategy']
                        pos['qty'] -= exec_qty
                        
                        profit = (exec_price - pos['buy_price']) * exec_qty
                        profit_pct = ((exec_price - pos['buy_price']) / pos['buy_price']) * 100
                        
                        # 예수금 실시간 재조회
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account)
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식예수금조회", "opw00001", 0, "0201")
                        
                        if profit < 0:
                            self.stock_daily_loss += abs(profit)
                            icon = "✂️"
                        else:
                            icon = "🚀"
                            
                        if notifier:
                            strat_name = "단타(가상)" if strat == 'DAY' else "스윙(가상)"
                            notifier.send_message(f"{icon} <b>[{strat_name} 매도 완료] {name}</b>\n• 체결가: {exec_price:,.0f}원\n• 손익률: {profit_pct:+.2f}%\n• 실현손익: {profit:+,}원\n🔄 가용 실예수금: {self.stock_total_balance:,}원")
                            
                        if pos['qty'] <= 0:
                            del self.portfolio[code]
                            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "0102", code)
                            self.persist_positions() # 가상 파티셔닝 구조 보존

    def _on_receive_real_data(self, code, real_type, real_data):
        # 선물 실시간 틱 처리 (futures/both만)
        if real_type == "선물시세" or real_type == "선물체결":
            if self.trading_mode not in ('futures', 'both'):
                return
            raw = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10).strip()
            if raw:
                try:
                    self._process_futures_tick(code, abs(float(raw)))
                except ValueError:
                    pass
            return

        if real_type == "주식체결":
            if self.trading_mode not in ('stock', 'both'):
                return
            current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))

            # 테마 대장주 실시간 OHLCV 갱신 (포트폴리오 편입 전 모니터링)
            if code in self.theme_stocks and code not in self.portfolio:
                try:
                    tick_vol = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 11).strip() or 0))
                except (ValueError, AttributeError):
                    tick_vol = 0
                self._update_intraday_ohlcv(code, current_price, tick_vol)
                return
            
            if code in self.portfolio:
                pos = self.portfolio[code]
                pos['current_price'] = current_price
                buy_price = pos['buy_price']
                profit_ratio = (current_price - buy_price) / buy_price
                strat = pos['strategy']
                
                sell_reason = None
                
                # --- 단타 로직 (가상 격리) ---
                if strat == 'DAY':
                    ma_10 = pos.get('ma_10', 0)
                    ma_20 = pos.get('ma_20', 0)
                    ma_10_is_up = pos.get('ma_10_is_up', False)
                    super_trend_mode = pos.get('super_trend_mode', False)
                    
                    if profit_ratio <= -0.02:
                        sell_reason = "단타 고정 손절선(-2%) 도달"
                    else:
                        if super_trend_mode:
                            if current_price < ma_20 and ma_20 > 0:
                                sell_reason = "단타 20MA 하향 돌파 (Trailing Stop 종료)"
                            elif profit_ratio <= 0.015:
                                sell_reason = "+1.5% 최소 수익 보장선 이탈"
                        else:
                            if profit_ratio >= 0.03:
                                if ma_10 > 0 and ma_10_is_up and current_price >= ma_10:
                                    if not super_trend_mode:
                                        print(f"🌟 [{pos['name']}] 단타 수익 극대화 모드 진입!")
                                        pos['super_trend_mode'] = True
                                else:
                                    sell_reason = "단타 +3% 목표가 도달 (MA 하향)"
                                    
                # --- 스윙 로직 (가상 격리) ---
                elif strat == 'SWING':
                    # 장대양봉 시가 이탈 시 즉시 기계적 손절 (하드 스탑)
                    if pos['open_price'] and current_price < pos['open_price']:
                        sell_reason = f"스윙 기준봉 시가({pos['open_price']:,}원) 하향 이탈 (하드스탑)"
                        
                if sell_reason and not pos.get('sell_ordered'):
                    print(f"\n[🛡️ ERA 자동 청산 발동] {pos['name']} - {sell_reason}")
                    pos['sell_ordered'] = True
                    self.kiwoom.dynamicCall(
                        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                        ["[ERA_Auto_Sell]", "0103", self.stock_account, 2, code, pos['qty'], 0, "03", ""]
                    )

    def _keepalive_ping(self):
        """키움 세션 킵얼라이브 — 10분 자동 로그아웃 방지 (5분 주기)"""
        try:
            state = self.kiwoom.dynamicCall("GetConnectState()")
            if state == 1:
                # 가벼운 API 호출로 세션 유지
                self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            else:
                print("[ERA] ⚠️ 키움 연결 끊김 감지 (keepalive)")
                if notifier:
                    notifier.send_message("⚠️ <b>[ERA]</b> 키움 서버 연결 끊김 감지됨")
        except Exception as e:
            print(f"[ERA] keepalive 오류: {e}")

    def _check_kill_flag(self):
        """긴급정지 플래그 감시 — TCA가 생성한 emergency_kill.flag 감지 시 전량 청산 후 종료"""
        flag_path = os.path.join(self.workspace_root, "emergency_kill.flag")
        if os.path.exists(flag_path):
            print("\n🚨 [ERA] 긴급정지 플래그 감지! 전 포지션 청산 후 종료합니다.")
            if notifier:
                mode_label = {'stock': '주식', 'futures': '선물', 'both': '주식+선물'}[self.trading_mode]
                notifier.send_message(f"🚨 <b>[{mode_label} ERA 긴급정지 발동]</b>\n플래그 감지 → 전량 청산 + 종료")
            try:
                os.remove(flag_path)
            except:
                pass
            # 전량 청산 시도
            self.system_halted = True
            # 선물 포지션 청산
            for pos_key in list(self.futures_positions.keys()):
                pos = self.futures_positions[pos_key]
                order_code = self.real_night_code if 'NIGHT' in pos_key else self.real_day_code
                self._execute_futures_direct(
                    "LONG_EXIT" if pos['type'] == 'LONG' else "SHORT_EXIT",
                    0, order_code, pos_key
                )
            # 주식 포지션 청산
            for code in list(self.portfolio.keys()):
                pos = self.portfolio[code]
                if not pos.get('sell_ordered'):
                    self.kiwoom.dynamicCall(
                        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                        ["[ERA_KILL]", "0103", self.stock_account, 2, code, pos['qty'], 0, "03", ""]
                    )
            print("[ERA] 긴급정지 청산 주문 완료. 5초 후 종료합니다.")
            QTimer.singleShot(5000, lambda: sys.exit(0))

if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        print("[ERA] 윈도우 절전 방지 활성화 완료.")
    except Exception as e:
        print(f"[ERA] 절전 방지 활성화 실패: {e}")

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
    print("   ERA Order Manager (day 60% & swing 40% Unified)")
    print("==========================================================")

    try:
        app = QApplication(sys.argv)
        manager = ERAOrderManager()
        sys.exit(app.exec_())
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        print(f"\n[ERA 치명적 오류] {e}\n{err_msg}")
        # 에러 로그 파일 저장
        try:
            with open(os.path.join(current_dir, "era_crash.log"), "w", encoding="utf-8") as f:
                f.write(err_msg)
            print(f"[ERA] 에러 로그 저장: {os.path.join(current_dir, 'era_crash.log')}")
        except:
            pass
        input("[ERA] 종료하려면 Enter 키를 누르세요...")
