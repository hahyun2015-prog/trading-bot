import os
import sys
import sqlite3
import json
import subprocess
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

current_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(current_dir, "era_order_manager.log")

# 윈도우 CP949 콘솔 인코딩 에러(이모지 출력 크래시) 원천 방지 래퍼 클래스 + 파일 실시간 백업 로깅
class SafeStreamWrapper:
    def __init__(self, original_stream, log_file_path=None):
        self.original_stream = original_stream
        self.log_file_path = log_file_path
        
    def write(self, data):
        if not data:
            return
        # 1. 원래 스트림(콘솔) 출력 처리
        try:
            encoding = getattr(self.original_stream, 'encoding', 'cp949') or 'cp949'
            data.encode(encoding)
            self.original_stream.write(data)
        except UnicodeEncodeError:
            cleaned_data = ""
            for char in data:
                try:
                    char.encode(encoding)
                    cleaned_data += char
                except UnicodeEncodeError:
                    pass  # 인코딩이 불가능한 이모지만 안전하게 발라냄
            self.original_stream.write(cleaned_data)
            
        # 2. 파일 실시간 백업 로깅
        if self.log_file_path:
            try:
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    f.write(data)
            except Exception:
                pass
            
    def flush(self):
        self.original_stream.flush()

sys.stdout = SafeStreamWrapper(sys.stdout, log_file)
sys.stderr = SafeStreamWrapper(sys.stderr, log_file)

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
from PyQt5.QtCore import QTimer, QThread, pyqtSignal

# 중앙 notifier 모듈 임포트
sys.path.append(os.path.abspath(os.path.join(current_dir, "..")))
try:
    import notifier
except ImportError:
    notifier = None

class AMATSDynamicAllocator:
    def __init__(self, workspace_root, db_path):
        self.workspace_root = workspace_root
        self.db_path = db_path
        self.config_local_path = os.path.join(workspace_root, "config", "config_local.json")
        self.config_path = os.path.join(workspace_root, "config", "config.json")
        self.min_allocation = 0.20 # 한 전략 최소 20% 안전 마진
        self.default_day = 0.60
        self.default_swing = 0.40

    def detect_regime(self):
        """KODEX 200 네이버 일봉 데이터를 활용한 20/50 EMA 및 14일 ATR 기울기 시장 레짐 감지"""
        try:
            print("[AMATS 자산 배분] 네이버 금융에서 KODEX 200 일봉 데이터 크롤링 시작...")
            df = self._get_naver_kodex200_daily(7)
            import pandas as pd
            import numpy as np
            if df.empty or len(df) < 55:
                # 네이버 크롤링 실패 시 로컬 DB futures_data.db 폴백 시도
                futures_db = os.path.join(self.workspace_root, "futures_data.db")
                if os.path.exists(futures_db):
                    print("[AMATS 자산 배분] 네이버 크롤링 실패 → 로컬 futures_data.db 폴백 시도...")
                    conn = sqlite3.connect(futures_db, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL;")
                    db_df = pd.read_sql(
                        "SELECT date,open,high,low,close FROM futures_ohlcv WHERE code='10500000' ORDER BY date", conn
                    )
                    conn.close()
                    db_df['date'] = pd.to_datetime(db_df['date'], format='%Y%m%d%H%M%S', errors='coerce')
                    db_df.dropna(subset=['date'], inplace=True)
                    db_df.set_index('date', inplace=True)
                    df = db_df.resample('D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
            
            if df.empty or len(df) < 55:
                print("[AMATS 자산 배분] 레짐 데이터 확보 실패 → 기본 RANGE 설정")
                return "RANGE"

            # 지표 계산
            df['ema20'] = df['close'].ewm(span=20).mean()
            df['ema50'] = df['close'].ewm(span=50).mean()
            df['ema_slope'] = (df['ema20'] - df['ema20'].shift(5)) / df['ema20'].shift(5) * 100
            
            last_row = df.iloc[-1]
            ema20 = last_row['ema20']
            ema50 = last_row['ema50']
            slope = last_row['ema_slope']
            
            print(f"[AMATS 자산 배분] 감지 지표: EMA20={ema20:.2f} | EMA50={ema50:.2f} | 5일 기울기={slope:+.3f}%")
            
            if ema20 > ema50 and slope > 0.3:
                return "UP"
            elif ema20 < ema50 and slope < -0.3:
                return "DOWN"
            else:
                return "RANGE"
        except Exception as e:
            print(f"[AMATS 자산 배분] 레짐 감지 실패 (RANGE 판별): {e}")
            return "RANGE"

    def _get_naver_kodex200_daily(self, pages=7):
        import pandas as pd
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        rows = []
        for page in range(1, pages + 1):
            url = f'https://finance.naver.com/item/sise_day.naver?code=069500&page={page}'
            try:
                r = requests.get(url, headers=headers, timeout=5)
                soup = BeautifulSoup(r.content, 'html.parser')
                for tr in soup.select('table.type2 tr'):
                    tds = tr.select('td')
                    if len(tds) < 7:
                        continue
                    dstr = tds[0].text.strip()
                    if not dstr or '.' not in dstr:
                        continue
                    try:
                        rows.append({
                            'date': datetime.strptime(dstr, '%Y.%m.%d'),
                            'close': int(tds[1].text.strip().replace(',', '')),
                            'open': int(tds[3].text.strip().replace(',', '')),
                            'high': int(tds[4].text.strip().replace(',', '')),
                            'low': int(tds[5].text.strip().replace(',', '')),
                        })
                    except Exception:
                        pass
            except Exception:
                pass
        df = pd.DataFrame(sorted(rows, key=lambda x: x['date']))
        if df.empty:
            return df
        df.set_index('date', inplace=True)
        return df

    def calculate_rolling_performance(self):
        """최근 30일간의 단타 및 스윙 거래 이력 기반 Sharpe Ratio 계산"""
        try:
            import pandas as pd
            import numpy as np
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            # stock_trades 테이블 유무 검사 및 조회
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_trades'")
            if not cursor.fetchone():
                conn.close()
                return 6.32, 5.02 # 데이터 부족 시 백테스트 평균 기본값
                
            df_trades = pd.read_sql(
                "SELECT strategy_type, pnl FROM stock_trades WHERE timestamp >= date('now', '-30 days')", conn
            )
            conn.close()
            
            if df_trades.empty or len(df_trades) < 5:
                return 6.32, 5.02
                
            # 단타(DAY) 성과 계산
            day_pnls = df_trades[df_trades['strategy_type'] == 'DAY']['pnl'].tolist()
            score_day = self._calc_sharpe(day_pnls)
            
            # 스윙(SWING) 성과 계산
            swing_pnls = df_trades[df_trades['strategy_type'] == 'SWING']['pnl'].tolist()
            score_swing = self._calc_sharpe(swing_pnls)
            
            return max(score_day, 0.1), max(score_swing, 0.1)
        except Exception:
            return 6.32, 5.02

    def _calc_sharpe(self, pnls):
        import numpy as np
        if not pnls:
            return 0.1
        total_ret = sum(pnls)
        cap_series = [10000000]
        for p in pnls:
            cap_series.append(cap_series[-1] + p)
        arr = np.array(cap_series)
        peak = np.maximum.accumulate(arr)
        mdd = np.min((arr - peak) / peak) * 100
        mdd_val = abs(mdd) if abs(mdd) > 1.0 else 1.0
        return total_ret / mdd_val

    def get_dynamic_allocation(self, regime):
        """성과 점수와 실시간 시장 레짐을 결합해 최종 분배율 도출"""
        score_day, score_swing = self.calculate_rolling_performance()
        raw_ratio_day = score_day / (score_day + score_swing)
        
        # 1차 분배 및 최소 하한선 적용
        ratio_day = max(min(raw_ratio_day, 1.0 - self.min_allocation), self.min_allocation)
        ratio_swing = 1.0 - ratio_day
        
        # 시장 레짐에 따른 스케일링
        if regime == "UP":
            ratio_swing = max(ratio_swing * 1.5, 0.60)
            ratio_day = 1.0 - ratio_swing
        elif regime == "RANGE":
            ratio_day = 0.80
            ratio_swing = 0.20
        elif regime == "DOWN":
            ratio_day = 0.20
            ratio_swing = 0.00
            
        return round(ratio_day, 2), round(ratio_swing, 2)

    def apply_to_config(self, ratio_day, ratio_swing):
        """config_local.json 및 config.json 의 예산 비율 업데이트"""
        try:
            # 1. config_local.json 업데이트
            if os.path.exists(self.config_local_path):
                with open(self.config_local_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            else:
                cfg = {}
            if "budget_allocation" not in cfg:
                cfg["budget_allocation"] = {}
            cfg["budget_allocation"]["stock_day_ratio"] = ratio_day
            cfg["budget_allocation"]["stock_swing_ratio"] = ratio_swing
            cfg["budget_allocation"]["dynamic_allocation_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.config_local_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
            print(f"[AMATS 자산 배분] config_local.json 자동 배분 업데이트 완료 (단타={ratio_day} / 스윙={ratio_swing})")
            return True
        except Exception as e:
            print(f"[AMATS 자산 배분] 설정 파일 저장 오류: {e}")
            return False

class MorningPrepWorker(QThread):
    finished_signal = pyqtSignal(bool, str, float, float, list) # success, today_regime, r_day, r_swing, leaders
    
    def __init__(self, workspace_root, unified_db_path, trading_mode):
        super().__init__()
        self.workspace_root = workspace_root
        self.unified_db_path = unified_db_path
        self.trading_mode = trading_mode

    def run(self):
        try:
            print("[AMATS 자산 배분] 백그라운드 아침 장세 분석 시작...")
            allocator = AMATSDynamicAllocator(self.workspace_root, self.unified_db_path)
            today_regime = allocator.detect_regime()
            r_day, r_swing = allocator.get_dynamic_allocation(today_regime)
            allocator.apply_to_config(r_day, r_swing)
            print(f"[AMATS 자산 배분] 백그라운드 분석 완료: regime={today_regime}, 단타={r_day}, 스윙={r_swing}")
            
            # 테마 크롤링 수행
            leaders = []
            today_str = datetime.now().strftime("%Y-%m-%d")
            
            # 먼저 STA가 오늘 데이터를 저장했는지 백그라운드에서 검증
            sta_has_data = False
            try:
                conn = sqlite3.connect(self.unified_db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL;")
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='top_volume_theme'")
                if cursor.fetchone():
                    cursor.execute("SELECT COUNT(*) FROM top_volume_theme WHERE date = ?", (today_str,))
                    count = cursor.fetchone()[0]
                    if count > 0:
                        sta_has_data = True
                conn.close()
            except Exception:
                pass
                
            if sta_has_data:
                print("[MorningPrepWorker] STA가 이미 오늘 테마 데이터를 적재하여 크롤링을 스킵합니다.")
            else:
                # 직접 네이버 테마 크롤링 수행
                leaders = self._perform_theme_crawl()
                if leaders:
                    try:
                        conn = sqlite3.connect(self.unified_db_path, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL;")
                        cursor = conn.cursor()
                        cursor.execute("""CREATE TABLE IF NOT EXISTS top_volume_theme
                                          (date TEXT, code TEXT, name TEXT, volume TEXT, UNIQUE(date, code))""")
                        cursor.execute("DELETE FROM top_volume_theme WHERE date = ?", (today_str,))
                        for item in leaders:
                            cursor.execute("INSERT OR REPLACE INTO top_volume_theme (date,code,name,volume) VALUES(?,?,?,?)",
                                           (today_str, item["code"], item["name"], item["theme"]))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        print(f"[MorningPrepWorker] DB 적재 에러: {e}")
            
            self.finished_signal.emit(True, today_regime, r_day, r_swing, leaders)
        except Exception as e:
            print(f"[MorningPrepWorker] 실행 오류: {e}")
            self.finished_signal.emit(False, "RANGE", 0.60, 0.40, [])

    def _perform_theme_crawl(self):
        _HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        _EXCLUDE = [
            "KODEX","TIGER","KBSTAR","KINDEX","KOSEF","HANARO","ARIRANG","TREX","SOL","ACE","RISE",
            "인버스","레버리지","선물","스팩","ETN","리츠","DR","우선주"
        ]
        leaders = []
        seen_codes = set()
        try:
            res = requests.get("https://finance.naver.com/sise/theme.naver", headers=_HEADERS, timeout=5)
            soup = BeautifulSoup(res.content, "html.parser")
            themes = [
                {"name": c.text, "url": "https://finance.naver.com" + c["href"]}
                for r in soup.select("table.type_1 tr")
                for c in r.select("td.col_type1 a")
            ]
            
            for theme in themes[:10]:
                try:
                    tres = requests.get(theme["url"], headers=_HEADERS, timeout=5)
                    tres.raise_for_status()
                    tsoup = BeautifulSoup(tres.content, "html.parser")
                    rows = tsoup.select("table.type_5 tbody tr")
                    if not rows:
                        continue
                    count = 0
                    for row in rows:
                        if count >= 5:
                            break
                        a = row.select_one("td.name a")
                        if a:
                            sname = a.text.strip()
                            scode = a["href"].split("code=")[1]
                            if not any(kw in sname for kw in _EXCLUDE) and scode not in seen_codes:
                                leaders.append({"code": scode, "name": sname, "theme": theme["name"]})
                                seen_codes.add(scode)
                                count += 1
                except Exception:
                    continue
        except Exception as e:
            print(f"[MorningPrepWorker] 크롤링 에러: {e}")
        return leaders

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
        self.current_regime = "RANGE"    # [AMATS 최적화] 실시간 감지 레짐 기본값
        self.morning_worker = None
        
        # 자금 정보
        self.stock_total_balance = 0
        self.stock_initial_balance = 0
        self.stock_daily_loss = 0
        
        self.futures_available_balance = 0
        self.futures_margin_cap_ratio = 0.20  # [AMATS 최적화] KOSPI200 선물 20% 격리 캡
        self.isf_margin_cap_ratio = 0.05      # [AMATS 최적화] ISF 종목당 5% 격리 캡
        self.futures_atr_cutoff = 0.5         # [AMATS 최적화] 초저변동성 구간 진입 차단 필터 기본값
        
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
        
        # 3. 스윙 15:14 종가 5일선 이탈 감시 & ISF 15:20 강제청산 감시 (1초 주기) — 항상 기동
        self.swing_time_timer = QTimer()
        self.swing_time_timer.timeout.connect(self.check_swing_close_time)
        if self.trading_mode in ('stock', 'futures', 'both'):
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
        self._daily_reset_done_date = ""   # 09:00 일일 리셋 중복 실행 방지
        self._night_reset_done_date = ""   # 18:00/05:00 야간 리셋 중복 실행 방지

        # 월간 MDD 자동 중단 (월간 손실 25% 초과 시 Kill Switch)
        self.stock_monthly_loss = 0
        self.stock_monthly_initial = 0  # 월초 잔고 기준선

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
        
        # ── 선물 과거 5분봉 자동 동기화 상태 변수 ───────────────────────
        self.futures_sync_queue = []
        self.futures_sync_index = 0
        self.futures_sync_current_page = 0
        self.futures_sync_max_pages = 10     # 수집할 과거 데이터 페이지 수 (1페이지당 약 80~100개 캔들)
        self.futures_sync_active = False

        # 선물 손절/익절 설정 (고정 pt)
        self.futures_stop_loss_pt = 2.0   # 주간선물 손절 (update_futures_dynamic_sl_tp가 덮어씀)
        self.futures_take_profit_pt = 5.0  # 주간선물 익절
        self.futures_atr_14 = 2.0
        # 야간선물 전용 고정 손절/익절 — ATR 동적 함수에 의해 절대 변경되지 않음
        self.futures_night_stop_loss_pt = 3.0
        self.futures_night_take_profit_pt = 6.0

        # 주간 선물 (09:00 ~ 익일 08:45)
        self.futures_day_open     = 0.0
        self.futures_target_long  = float('inf')
        self.futures_target_short = float('-inf')
        self.futures_order_locked = False
        self.futures_day_entry_price = 0.0  # 주간 진입가 기록
        self.futures_day_peak = 0.0         # [대안 C] 주간 트레일링 스탑용 최고/최저가 추적

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

        # 7-2. 장중 주도주 1시간 주기 실시간 동적 편입 타이머 — stock/both만
        self.intraday_refresh_timer = QTimer()
        self.intraday_refresh_timer.timeout.connect(self._refresh_intraday_leaders)
        if self.trading_mode in ('stock', 'both'):
            self.intraday_refresh_timer.start(3600000) # 1시간 주기

        # 8. OHLCV 버퍼 → DB 30초 주기 동기화 — stock/both만
        self.ohlcv_flush_timer = QTimer()
        self.ohlcv_flush_timer.timeout.connect(self._flush_ohlcv_buffer)
        if self.trading_mode in ('stock', 'both'):
            self.ohlcv_flush_timer.start(30000)

        # ── 개별주식선물 (ISF: Individual Stock Futures) 엔진 ──────────────
        self.isf_configs = []           # config에서 로드된 ISF 종목 리스트
        self.isf_positions = {}         # {stock_code: {type, qty, price, futures_code}}
        self.isf_order_locked = {}      # {stock_code: bool} 중복주문 방지
        self.isf_day_open = {}          # {stock_code: float} 오늘 시초가
        self.isf_target_long = {}       # {stock_code: float} LONG 진입 목표가
        self.isf_target_short = {}      # {stock_code: float} SHORT 진입 목표가
        self.isf_entry_price = {}       # {stock_code: float} 진입가
        self.isf_peak_price = {}        # {stock_code: float} 진입 후 최고/최저가 추적 (트레일링용)
        self.isf_direction = {}         # {stock_code: "LONG"|"SHORT"|"NEUTRAL"}
        self.isf_code_map = {}          # {futures_code: stock_code}
        self.isf_prev_range = {}        # {stock_code: float} 전일 고저폭(원)
        self.isf_direction_date = ""    # 방향 로드 날짜 (중복 로드 방지)

        # ISF 09:00 방향 체크 타이머 (1분 주기)
        self.isf_direction_timer = QTimer()
        self.isf_direction_timer.timeout.connect(self._update_isf_direction_if_needed)
        if self.trading_mode in ('futures', 'both'):
            self.isf_direction_timer.start(60000)

    def get_swing_exit_ma_period(self):
        """현재 감지된 시장 레짐(UP/RANGE/DOWN)에 따라 스윙 청산 이평선 기간 자동 결정"""
        regime = getattr(self, 'current_regime', 'RANGE')
        if regime == "UP":
            return 10  # 강세장 -> 휩소 방지 및 이익 극대화를 위해 10일선(10MA) 지지력 추종
        else:
            return 5   # 횡보/약세장 -> 칼청산으로 단기 이익 실현 및 자산 격리를 위해 5일선(5MA) 추종

    def update_futures_dynamic_sl_tp(self):
        """BQA 역사적 데이터를 조회하여 실시간 선물 변동성(ATR) 기반 동적 익손절 라인 산출"""
        try:
            import pandas as pd
            import numpy as np
            if not os.path.exists(self.futures_db_path):
                return
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            # 최근 70개 일봉(resample) 계산을 위해 충분한 분량 조회
            df = pd.read_sql(
                "SELECT date, high, low, close FROM futures_ohlcv WHERE code='10500000' ORDER BY date DESC LIMIT 400", conn
            )
            conn.close()
            
            if df.empty or len(df) < 50:
                return
                
            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
            df.dropna(subset=['date'], inplace=True)
            df.set_index('date', inplace=True)
            daily = df.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
            
            if len(daily) < 15:
                return
                
            # TR 계산
            daily['tr'] = np.maximum(daily['high'] - daily['low'], 
                                     np.maximum(abs(daily['high'] - daily['close'].shift(1)), 
                                                abs(daily['low'] - daily['close'].shift(1))))
            atr_14 = daily['tr'].rolling(14).mean().iloc[-1]
            
            if pd.isna(atr_14) or atr_14 <= 0:
                return
                
            # 동적 SL / TP 연산 (손절 = 1.0 * ATR, 익절 = 2.0 * ATR)
            self.futures_stop_loss_pt = max(round(atr_14 * 1.0, 2), 2.0)
            self.futures_take_profit_pt = max(round(atr_14 * 2.0, 2), 4.0)
            self.futures_atr_14 = float(atr_14)
            
            print(f"[AMATS 파생 최적화] 선물 동적 ATR 적용 완료: 14일 ATR={atr_14:.2f}pt ➡️ 손절={self.futures_stop_loss_pt}pt | 익절={self.futures_take_profit_pt}pt")
        except Exception as dynamic_err:
            print(f"[AMATS 파생 최적화] 동적 익손절 계산 에러 (기본 고정값 유지): {dynamic_err}")

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
            self.gemini_api_key = config.get("api_settings", {}).get("gemini_api_key", "")
            self.apply_rsa_in_mock = config.get("features", {}).get("apply_rsa_in_mock", False)

            # 선물 손절/익절 설정 로드 (고정 pt) — config.json 기본값
            futures_settings = config.get("futures_settings", {})
            self.futures_stop_loss_pt = float(futures_settings.get("stop_loss_pt", 3.0))
            self.futures_take_profit_pt = float(futures_settings.get("take_profit_pt", 6.0))
            # 야간선물 고정값: config.json futures_settings 기준, active_strategy.json에 의해 절대 변경 안 됨
            self.futures_night_stop_loss_pt = float(futures_settings.get("stop_loss_pt", 3.0))
            self.futures_night_take_profit_pt = float(futures_settings.get("take_profit_pt", 6.0))

            # active_strategy.json의 백테스트 파라미터로 주간선물만 오버라이드 (야간선물 제외)
            active_strategy_path = os.path.join(self.workspace_root, "config", "active_strategy.json")
            if os.path.exists(active_strategy_path):
                try:
                    with open(active_strategy_path, "r", encoding="utf-8") as f:
                        active = json.load(f)
                    if "stop_loss_pt" in active:
                        self.futures_stop_loss_pt = float(active["stop_loss_pt"])
                    if "take_profit_pt" in active:
                        self.futures_take_profit_pt = float(active["take_profit_pt"])
                    if "best_k" in active:
                        self.futures_best_k = float(active["best_k"])
                    if "margin_cap" in active:
                        self.futures_margin_cap_ratio = float(active["margin_cap"])
                    if "atr_cutoff" in active:
                        self.futures_atr_cutoff = float(active["atr_cutoff"])
                    print(f"[ERA] active_strategy.json 파라미터 적용: K={self.futures_best_k} | 주간손절={self.futures_stop_loss_pt}pt | 주간익절={self.futures_take_profit_pt}pt | 야간손절={self.futures_night_stop_loss_pt}pt(고정) | 야간익절={self.futures_night_take_profit_pt}pt(고정) | 마진캡={self.futures_margin_cap_ratio:.2f} | ATR필터={self.futures_atr_cutoff:.2f}pt")
                except Exception as e:
                    print(f"[ERA] active_strategy.json 로드 실패 (config.json 값 유지): {e}")

            # target_code_day를 기반으로 선물 상품 접두사 추출 (디폴트: "101" -> 일반선물, "105" -> 미니선물)
            target_code_day = futures_settings.get("target_code_day", "10100000")
            self.futures_prefix = target_code_day[:3] if len(target_code_day) >= 3 else "101"

            # 고정 계약 수량 설정 (기본값: None -> 잔고 비례 동적 계산)
            fixed_qty_val = futures_settings.get("fixed_qty", None)
            self.futures_fixed_qty = int(fixed_qty_val) if fixed_qty_val is not None else None

            # 개별주식선물(ISF) 설정 로드
            self.isf_configs = config.get("individual_stock_futures", [])
            if self.isf_configs:
                names = [c.get("name", c.get("stock_code", "?")) for c in self.isf_configs]
                print(f"[ERA ISF] 개별주식선물 {len(self.isf_configs)}종목 설정 로드: {', '.join(names)}")

            print(f"[ERA] trading_mode = {self.trading_mode} | 상품접두사 = {self.futures_prefix} | 고정수량 = {self.futures_fixed_qty} | 손절 = {self.futures_stop_loss_pt}pt | 익절 = {self.futures_take_profit_pt}pt")
        except Exception as e:
            print(f"[ERA Config Error] {e}")
            self.environment = "mock"
            self.trading_mode = "both"
            self.ratio_day = 0.60
            self.ratio_swing = 0.40
            self.futures_prefix = "101"
            self.futures_fixed_qty = None
            self.config_stock_acc = ""
            self.config_futures_acc = ""
            self.futures_stop_loss_pt = 3.0
            self.futures_take_profit_pt = 6.0

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
            data = {}
            for code, pos in self.portfolio.items():
                data[code] = {
                    "strategy": pos["strategy"],
                    "half_sold": pos.get("half_sold", False),
                    "open_price": pos.get("open_price", pos.get("buy_price", 0))
                }
            with open(self.positions_persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[ERA] 포지션 저장 실패: {e}")

    def _check_daily_reset(self):
        try:
            self._do_daily_reset()
        except Exception as e:
            print(f"[ERA _check_daily_reset 오류] {e}")

    def _do_daily_reset(self):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        # ── 05:00 야간선물 세션 종료 리셋 ──────────────────────────────
        night_reset_key = f"{today}_0500"
        is_after_5am = (now.hour == 5 and now.minute >= 0) or (now.hour > 5)
        if is_after_5am and self._night_reset_done_date != night_reset_key:
            self._night_reset_done_date = night_reset_key
            self.futures_night_open         = 0.0
            self.futures_night_target_long  = float('inf')
            self.futures_night_target_short = float('-inf')
            self.futures_night_order_locked = False
            self.futures_night_entry_price  = 0.0
            print(f"[ERA 야간선물] {now.strftime('%H:%M')} 세션 종료 — 상태 초기화")

        # ── 주간선물 세션 시작 리셋 (08:40 이후 실행) + Kill Switch 해제 ───────────
        is_after_840 = (now.hour == 8 and now.minute >= 40) or (now.hour >= 9)
        if is_after_840 and self._daily_reset_done_date != today:
            self._daily_reset_done_date = today
            # 월초: 월간 MDD 기준 리셋
            if now.day == 1:
                self.stock_monthly_loss = 0
                self.stock_monthly_initial = self.stock_total_balance if self.stock_total_balance > 0 else self.stock_monthly_initial
                print(f"[ERA 월간MDD 리셋] 월초 — 기준잔고 {self.stock_monthly_initial:,}원")
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
            print(f"[ERA 주간선물] {now.strftime('%H:%M')} 세션 준비 — 전일 Range 갱신")

        # ── 18:00 야간선물 세션 시작 리셋 ──────────────────────────────
        night_start_key = f"{today}_1800"
        is_after_18pm = (now.hour == 18 and now.minute >= 0) or (now.hour > 18)
        if is_after_18pm and self._night_reset_done_date != night_start_key:
            self._night_reset_done_date = night_start_key
            self.futures_night_open         = 0.0
            self.futures_night_target_long  = float('inf')
            self.futures_night_target_short = float('-inf')
            self.futures_night_order_locked = False
            self.futures_night_entry_price  = 0.0
            print(f"[ERA 야간선물] {now.strftime('%H:%M')} 세션 시작 대기 — 상태 초기화")

    def _is_trading_day(self):
        """오늘이 거래일인지 확인 (주말 + KRX 휴장일)"""
        now = datetime.now()
        if now.weekday() >= 5:  # 토(5), 일(6)
            return False

        # krx_holidays.json에서 휴장일 로드 (캐시, 연도별 1회)
        year = str(now.year)
        cache_year = getattr(self, '_krx_holidays_year', '')
        if cache_year != year:
            self._krx_holidays_cache = set()
            self._krx_holidays_year = year
            self._holiday_warning_sent = False
            try:
                holidays_path = os.path.join(self.workspace_root, "config", "krx_holidays.json")
                if os.path.exists(holidays_path):
                    with open(holidays_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if year in data:
                        for h in data[year]:
                            self._krx_holidays_cache.add(h["date"])
                        print(f"[ERA] {year}년 KRX 휴장일 {len(self._krx_holidays_cache)}일 로드 완료")
                    else:
                        # 새해 휴장일 데이터 없음 → 알림
                        print(f"[ERA] ⚠️ {year}년 KRX 휴장일 데이터 없음!")
                        if notifier and not self._holiday_warning_sent:
                            notifier.send_message(
                                f"⚠️ <b>[ERA 알림]</b> {year}년 KRX 휴장일 데이터가 없습니다.\n\n"
                                f"📁 config/krx_holidays.json 에 \"{year}\" 항목을 추가해주세요.\n"
                                f"휴장일 미등록 시 휴장일에도 불필요한 알림이 발송됩니다."
                            )
                            self._holiday_warning_sent = True
            except Exception as e:
                print(f"[ERA] 휴장일 로드 실패: {e}")

        today = now.strftime("%Y-%m-%d")
        if today in self._krx_holidays_cache:
            return False
        return True

    def check_connection_status(self):
        state = self.kiwoom.dynamicCall("GetConnectState()")
        now = datetime.now()
        is_trading = self._is_trading_day()

        if state == 0:
            if not self.was_disconnected:
                print("🚨 [ERA] 키움증권 서버 통신 끊김 감지!")
                # 거래일에만 텔레그램 알림 발송
                if notifier and is_trading:
                    notifier.send_message(
                        "🚨 <b>[통신 끊김]</b> 키움증권 서버 연결이 끊어졌습니다.\n"
                        "새벽 서버 점검 중이라면 07:00 이후 자동 재연결합니다."
                    )
                elif not is_trading:
                    print("[ERA] 휴장일 — 텔레그램 알림 생략")
                self.was_disconnected = True
                self._reconnect_attempts = 0

            # 영업일의 활성 매매 시간대인 경우에만 자동 재연결 시도 (장외 시간대 무한 리셋 방지)
            else:
                is_active_hours = False
                if is_trading:
                    # 주식 모드 또는 통합 모드: 주식 장중 (08:30 ~ 15:40)
                    if self.trading_mode in ('stock', 'both'):
                        if (now.hour == 8 and now.minute >= 30) or (9 <= now.hour < 15) or (now.hour == 15 and now.minute <= 40):
                            is_active_hours = True
                    
                    # 선물 모드 또는 통합 모드: 선물 장중 (주간: 08:30~15:50, 야간: 18:00~익일 04:50)
                    if self.trading_mode in ('futures', 'both'):
                        if (now.hour == 8 and now.minute >= 30) or (9 <= now.hour < 15) or (now.hour == 15 and now.minute <= 50):
                            is_active_hours = True
                        if (now.hour >= 18) or (now.hour < 4) or (now.hour == 4 and now.minute <= 50):
                            is_active_hours = True

                if is_active_hours:
                    self._reconnect_attempts = getattr(self, '_reconnect_attempts', 0) + 1
                    print(f"[ERA] 자동 재연결 시도 #{self._reconnect_attempts}...")
                    
                    if self._reconnect_attempts >= 3:
                        print("🚨 [ERA] 3회 연속 재연결 실패. 하드웨어/소프트웨어 리셋(자동 재시작)을 실행합니다.")
                        if notifier:
                            notifier.send_message(
                                "🚨 <b>[ERA 연결 장애 지속]</b>\n"
                                "3회 연속 재연결에 실패했습니다.\n"
                                "Kiwoom OpenAPI 세션 초기화 및 ERA 엔진 자동 재구동을 진행합니다 (약 60초 소요)."
                            )
                        import subprocess
                        reconnect_script = os.path.join(current_dir, "auto_reconnect_era.bat")
                        if os.path.exists(reconnect_script):
                            # CREATE_NEW_CONSOLE을 사용해 부모 프로세스 트리와 완전히 분리된 별도의 새 독립형 콘솔 창으로 띄웁니다.
                            # 이로 인해 부모 파이썬 프로세스가 taskkill 당하더라도, 재연결 배치 프로세스는 안전하게 독자 생존하여 시퀀스를 완수합니다.
                            subprocess.Popen(
                                [reconnect_script],
                                creationflags=subprocess.CREATE_NEW_CONSOLE,
                                cwd=current_dir
                            )
                        else:
                            print(f"⚠️ [오류] 재연결 스크립트가 존재하지 않습니다: {reconnect_script}")
                    else:
                        self.kiwoom.dynamicCall("CommConnect()")
                else:
                    # 대기 상태 유지, 로그 노이즈 최소화
                    if getattr(self, '_reconnect_attempts', 0) > 0:
                        self._reconnect_attempts = 0
                    if now.minute == 0:
                        print(f"[ERA] 현재 비활성 시간대({now.strftime('%H:%M')}) 또는 휴장일입니다. 재연결을 대기합니다.")
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
            
            # 선물 최근월물 자동 검색 (이중 폴백 탑재)
            future_list = self.kiwoom.dynamicCall("GetFutureList()").strip()
            self.real_day_code = ""
            self.real_night_code = ""
            
            search_prefix = self.futures_prefix
            if self.environment != "live":
                search_prefix = "A01" if self.futures_prefix == "101" else "A05"
            
            if future_list:
                codes = [c for c in future_list.split(";") if c and c.startswith(search_prefix)]
                if codes:
                    self.real_day_code = codes[0]
            
            # 폴백 1단계: GetFutureList가 실패했거나 비어있을 시 GetFutureCodeByIndex 시도
            if not self.real_day_code:
                print(" => [ERA 폴백 1단계] GetFutureList 응답 없음. GetFutureCodeByIndex 조회 시도...")
                code_by_idx = self.kiwoom.dynamicCall("GetFutureCodeByIndex(int)", 0).strip()
                if code_by_idx and (code_by_idx.startswith(search_prefix) or (self.environment != "live" and (code_by_idx.startswith("A01") or code_by_idx.startswith("A05")))):
                    self.real_day_code = code_by_idx
                    print(f" => [ERA 폴백 1단계 성공] Index(0) 코드로 최근월물 인식: {self.real_day_code}")
            
            # 폴백 2단계: API 조회가 모두 실패할 시 날짜 기반 동적 연산 알고리즘 가동
            if not self.real_day_code:
                print(" => [ERA 폴백 2단계] 키움 API 최근월물 조회 실패. 날짜 기반 가상 알고리즘 가동...")
                now = datetime.now()
                curr_year = now.year
                curr_month = now.month
                curr_day = now.day
                
                # 키움 연도 코드 매핑 (2026=V, 2027=W, 2028=X, 2029=Y, 2030=Z ...)
                if self.environment != "live":
                    year_char = str(curr_year % 10)
                else:
                    year_codes = {2026: "V", 2027: "W", 2028: "X", 2029: "Y", 2030: "Z"}
                    year_char = year_codes.get(curr_year, "V")
                
                # 선물 만기월은 3, 6, 9, 12월. 둘째주 목요일이 만기일.
                # 안전한 근사를 위해 현재 월을 기준으로 만기월 판단 (매월 10일 전후가 만기이므로, 11일 이후이면 다음 분기로 폴오버)
                if curr_month <= 3:
                    if curr_month == 3 and curr_day > 12:  # 3월 만기일(대략 12일경) 이후
                        expiry_month_char = "6"
                    else:
                        expiry_month_char = "3"
                elif curr_month <= 6:
                    if curr_month == 6 and curr_day > 12:
                        expiry_month_char = "9"
                    else:
                        expiry_month_char = "6"
                elif curr_month <= 9:
                    if curr_month == 9 and curr_day > 12:
                        expiry_month_char = "C"
                    else:
                        expiry_month_char = "9"
                else:
                    if curr_month == 12 and curr_day > 12:
                        # 12월 만기일 이후에는 다음 연도 3월물로 점프
                        if self.environment != "live":
                            year_char = str((curr_year + 1) % 10)
                        else:
                            year_char = year_codes.get(curr_year + 1, "W")
                        expiry_month_char = "3"
                    else:
                        expiry_month_char = "C"
                
                self.real_day_code = f"{search_prefix}{year_char}{expiry_month_char}000"
                print(f" => [ERA 폴백 2단계 성공] 알고리즘 생성 최근월물 적용: {self.real_day_code}")
            
            # 최종 야간 코드 설정 (야간 지수선물은 주간 최근월물 코드에서 앞 세 자리를 105로 교체)
            # 단, 이미 미니 선물(105)인 경우에는 별도의 야간 코드가 없으므로 동일하게 설정
            if self.real_day_code:
                if self.futures_prefix == "105":
                    self.real_night_code = self.real_day_code
                else:
                    night_prefix = "A05" if self.environment != "live" else "105"
                    self.real_night_code = night_prefix + self.real_day_code[3:]
            else:
                if self.environment != "live":
                    self.real_day_code = "A0566000" if self.futures_prefix == "105" else "A0166000"
                    self.real_night_code = "A0566000"
                else:
                    self.real_day_code = self.futures_prefix + "00000"
                    self.real_night_code = "10500000"
            
            print(f" => [선물 최근월물 최종 인식] 주간({self.real_day_code}), 야간({self.real_night_code})")
            
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
                        for acc in accounts:
                            if acc.endswith('11'):
                                self.stock_account = acc
                                break
                        if not self.stock_account and accounts:
                            self.stock_account = accounts[0]
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
                            if acc != self.stock_account and not acc.endswith('11'):
                                self.futures_account = acc
                                break
                        if not self.futures_account:
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

            # 로그인 직후 신호 폴링 즉시 개시 (예수금 미조회 시 poll 내부에서 skip)
            if not self.signal_timer.isActive():
                self.signal_timer.start(2000)

            # 선물 K값 전략 초기화 (futures/both만)
            if self.trading_mode in ('futures', 'both'):
                QTimer.singleShot(3000, self._init_futures_strategy)
                # 개별주식선물 코드 탐지 및 초기화 (10초 후, 일반 선물 초기화 완료 후)
                if self.isf_configs:
                    QTimer.singleShot(10000, self._init_isf_strategy)
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
        if rqname in ("장전거래대금상위조회", "장중거래대금상위조회"):
            rows = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f"   [opt10032] 거래대금상위 수신 ({rqname}): {rows}개 종목")
            
            leaders = []
            seen_codes = set()
            _EXCLUDE = [
                "KODEX","TIGER","KBSTAR","KINDEX","KOSEF","HANARO","ARIRANG","TREX","SOL","ACE","RISE",
                "인버스","레버리지","선물","스팩","ETN","리츠","DR","우선주"
            ]
            
            for i in range(rows):
                code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "종목코드").strip()
                code = code.replace("A", "").strip()
                name = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "종목명").strip()
                
                if not code or len(code) != 6:
                    continue
                if any(kw in name for kw in _EXCLUDE):
                    continue
                if code in seen_codes:
                    continue
                    
                leaders.append({"code": code, "name": name, "theme": "거래대금상위"})
                seen_codes.add(code)
                if len(leaders) >= 20: # 최대 20개만 사용
                    break
            
            if rqname == "장전거래대금상위조회":
                self._save_fallback_leaders(leaders)
            else:
                self._apply_intraday_leaders(leaders)
                
        elif rqname == "주식예수금조회":
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "d+2추정예수금").strip()
            if d2_deposit:
                self.stock_total_balance = int(d2_deposit)
                if self.stock_initial_balance == 0:
                    self.stock_initial_balance = self.stock_total_balance
                if self.stock_monthly_initial == 0:
                    self.stock_monthly_initial = self.stock_total_balance
                    
                # 60대 40 기계적 가상 자금 파티셔닝
                self.budget_day = int(self.stock_total_balance * self.ratio_day)
                self.budget_swing = int(self.stock_total_balance * self.ratio_swing)
                
            print(f"\n=> 💰 [주식 가상 자금 파티셔닝]")
            print(f"   - 총 실예수금: {self.stock_total_balance:,}원")
            print(f"   - 단타용(60%): {self.budget_day:,}원 (최대 {self.max_day_positions}종목)")
            print(f"   - 스윙용(40%): {self.budget_swing:,}원 (최대 {self.max_swing_positions}종목)")
            
        elif rqname == "선물예수금조회":
            available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "주문가능현금").strip()
            if not available_cash or int(available_cash) == 0:
                available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "예탁금").strip()
            if available_cash:
                self.futures_available_balance = int(available_cash)
            print(f"\n=> 💸 [선물 계좌 자금]")
            print(f"   - 선물 예수금: {self.futures_available_balance:,}원")
            print(f"   - {int(self.futures_margin_cap_ratio * 100)}% 캡 적용 가용금액: {int(self.futures_available_balance * self.futures_margin_cap_ratio):,}원")
            
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
                persist_val = self.persisted_strategies.get(code, "SWING")
                if isinstance(persist_val, dict):
                    strategy_tag = persist_val.get("strategy", "SWING")
                    half_sold = persist_val.get("half_sold", False)
                    open_price = persist_val.get("open_price", buy_price)
                else:
                    strategy_tag = persist_val
                    half_sold = False
                    open_price = buy_price
                
                if code not in self.portfolio:
                    self.portfolio[code] = {
                        'name': name,
                        'strategy': strategy_tag,
                        'buy_price': buy_price,
                        'current_price': current_price,
                        'qty': qty,
                        'max_price': current_price,
                        'open_price': open_price,
                        'super_trend_mode': False,
                        'ma_10': 0, 'ma_20': 0,
                        'half_sold': half_sold
                    }
                    # 실시간 데이터 감시 등록
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    print(f"   - [{strategy_tag}] {name}({code}) | {qty}주 | 평단: {buy_price:,}원 (하프매도여부: {half_sold})")
            self.export_status()
            
        elif rqname == "스윙일봉5MA조회":
            code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "종목코드").strip()
            if code in self.portfolio and self.portfolio[code]['strategy'] == 'SWING':
                pos = self.portfolio[code]
                
                closes = []
                for i in range(10): # 항상 10영업일 종가 조회
                    c = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()
                    if c:
                        closes.append(abs(int(c)))
                        
                if len(closes) >= 10:
                    ma_5 = sum(closes[:5]) / 5
                    ma_10 = sum(closes[:10]) / 10
                    current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
                    if current_price == 0:
                        current_price = closes[0]
                        
                    print(f"   => [스윙 하프익절 검증] {pos['name']} 현재가: {current_price:,} / 5MA: {ma_5:,.1f} / 10MA: {ma_10:,.1f} (하프매도여부: {pos.get('half_sold', False)})")
                    
                    # 1. 10MA 하향 이탈 시: 전량 청산
                    if current_price < ma_10:
                        print(f"   🚨 [스윙 전량 청산] {pos['name']} 10일선 하향 이탈! 전량 매도.")
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Swing_10MA_Sell]", "0103", self.stock_account, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(f"📉 <b>[스윙 익절/청산] {pos['name']}</b>\n• 종가 10일선 이탈로 실계좌 시장가 전량 청산합니다.")
                    # 2. 5MA 하향 이탈 시 (10MA 위이고, 아직 하프매도가 안 된 상태): 50% 분할 매도
                    elif current_price < ma_5 and not pos.get('half_sold', False):
                        half_qty = max(1, pos['qty'] // 2)
                        print(f"   🚨 [스윙 하프 익절] {pos['name']} 5일선 하향 이탈! 절반({half_qty}주) 매도.")
                        pos['half_sold'] = True
                        self.persist_positions() # 상태 저장
                        
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Swing_5MA_Half]", "0103", self.stock_account, 2, code, half_qty, 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(f"📉 <b>[스윙 하프 익절] {pos['name']}</b>\n• 종가 5일선 이탈로 보유 물량의 절반({half_qty}주)을 시장가 매도합니다.")
                    else:
                        print(f"   ✅ [스윙 홀딩 확정] {pos['name']} 지지 흐름 유지.")
                        
        elif rqname == "선물과거분차트동기화":
            cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            code = self.futures_sync_queue[self.futures_sync_index]
            print(f"    [ERA 선물 동기화 수신] {code} | {cnt}개 캔들 수신")
            
            futures_rows = []
            for i in range(cnt):
                date = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "체결시간").strip()
                open_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "시가").strip()))
                high_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "고가").strip()))
                low_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "저가").strip()))
                close_p = abs(float(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()))
                vol = abs(int(self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "거래량").strip()))
                
                futures_rows.append((code, date, open_p, high_p, low_p, close_p, vol))
                
            try:
                if futures_rows:
                    conn = sqlite3.connect(self.futures_db_path, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL;")
                    cursor = conn.cursor()
                    cursor.execute("""CREATE TABLE IF NOT EXISTS futures_ohlcv
                                      (code TEXT, date TEXT, open REAL, high REAL,
                                       low REAL, close REAL, volume INTEGER, UNIQUE(code, date))""")
                    cursor.executemany(
                        "REPLACE INTO futures_ohlcv (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                        futures_rows
                    )
                    conn.commit()
                    conn.close()
                    print(f"    [DB 저장] {len(futures_rows)}개 완료")
            except Exception as e:
                print(f"[ERA 선물 과거 동기화 DB 저장 에러] {e}")
                
            self.futures_sync_current_page += 1
            
            # 다음 페이지 또는 다음 코드로 이동
            if str(next_str).strip() == "2" and self.futures_sync_current_page < self.futures_sync_max_pages:
                self._request_sync_tr("2")
            else:
                self.futures_sync_index += 1
                self.futures_sync_current_page = 0
                self._request_sync_tr()

    # ── STA 통합: 테마 크롤링 + 실시간 OHLCV ────────────────────────────

    def _check_morning_prep(self):
        """1분마다 실행 — 08:50 도달 시 테마 크롤링 백그라운드 QThread 시작"""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if not (now.weekday() < 5 and now.hour == 8 and 50 <= now.minute <= 59 and self.theme_crawl_date != today_str):
            return
            
        self._morning_theme_crawl()

    def _morning_theme_crawl(self):
        """백그라운드로 아침 장세 감지 및 테마 스캔 QThread 기동 (늦은 기동/수동 실행 지원)"""
        # 스레드가 이미 진행 중이면 중복 방지
        if getattr(self, 'morning_worker', None) is not None and self.morning_worker.isRunning():
            return
            
        print("[ERA] 아침 장세 감지 및 테마 스캔 백그라운드 QThread 기동...")
        self.morning_worker = MorningPrepWorker(self.workspace_root, self.unified_db_path, self.trading_mode)
        self.morning_worker.finished_signal.connect(self._on_morning_prep_finished)
        self.morning_worker.start()

    def _on_morning_prep_finished(self, success, today_regime, r_day, r_swing, leaders):
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        self.theme_crawl_date = today_str # 완료 마크
        
        if success:
            # 1. 최적 설정 핫로드 반영
            self.load_config()
            self.current_regime = today_regime
            
            # [AMATS 파생 최적화] 아침 장전 선물 동적 ATR SL/TP 실시간 갱신
            if self.trading_mode in ('futures', 'both'):
                self.update_futures_dynamic_sl_tp()
                
            regime_lbl = {"UP": "🚀 강세 추세장", "DOWN": "📉 약세 추세장 (현금방어)", "RANGE": "⏸️ 횡보/박스장"}.get(today_regime, today_regime)
            
            if notifier:
                notifier.send_message(
                    f"📊 <b>[AMATS AI 동적 자산 배분 완료]</b>\n\n"
                    f"👤 오늘의 시장 레짐: <b>{regime_lbl}</b>\n"
                    f"💰 자금 배분 비율: 단타 <b>{int(r_day*100)}%</b> / 스윙 <b>{int(r_swing*100)}%</b>\n"
                    f"💡 <i>(횡보/약세장 시 스윙을 자동 배제하여 오버나잇 휩소 손실을 방어합니다.)</i>"
                )
                
            # 2. 테마 대장주 알림 및 후속 작업 기동
            # 2-A. 직접 크롤링에 성공하여 leaders가 있는 경우
            if leaders:
                theme_groups = {}
                for item in leaders:
                    theme_groups.setdefault(item["theme"], []).append(item["name"])
                summary_lines = [f"• {t}: {', '.join(ns)}" for t, ns in list(theme_groups.items())[:5]]
                
                if notifier:
                    notifier.send_message(
                        f"🌅 <b>[08:50 RSA 분석 후보 확보 (크롤링)]</b>\n"
                        f"총 <b>{len(leaders)}개</b> 종목 ({len(theme_groups)}개 테마)\n"
                        + "\n".join(summary_lines) +
                        f"\n\n🔬 RSA 정밀 분석 시작 중..."
                    )
                self._trigger_rsa_premarket()
                QTimer.singleShot(1000, self._register_theme_realtime)
            else:
                # 2-B. 스레드 시작 시 이미 STA가 적재했거나 크롤 결과가 빈 경우 DB 재조회
                count = 0
                try:
                    conn = sqlite3.connect(self.unified_db_path, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL;")
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM top_volume_theme WHERE date = ?", (today_str,))
                    count = cursor.fetchone()[0]
                    conn.close()
                except Exception:
                    pass
                    
                if count > 0:
                    if notifier:
                        notifier.send_message(f"🌅 <b>[08:50 테마 준비 완료]</b>\nSTA 등록 {count}종목 활용 (스마트머니 필터 적용됨)")
                    self._trigger_rsa_premarket()
                    QTimer.singleShot(1000, self._register_theme_realtime)
                else:
                    # 크롤 결과도 없고 DB에도 없으면 폴백 요청 (키움 API 조회)
                    print("[ERA] 아침 크롤링 데이터가 없어 폴백 요청(키움 API)을 기동합니다.")
                    self._request_fallback_leaders()
        else:
            print("[ERA] 아침 장전 스캔 및 배분 백그라운드 작업 실패 → 폴백 기동")
            self._request_fallback_leaders()

    def _request_fallback_leaders(self):
        print("[ERA 폴백] 키움 API를 통해 전일 거래대금 상위 종목 조회를 요청합니다...")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시장구분", "000") # 전체
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "관리종목제외", "1")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "우선주제외", "1")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "신용구분", "0")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "거래대금구분", "1") # 전체
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "가격구분", "0")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "거래량구분", "0")
        
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "장전거래대금상위조회", "opt10032", 0, "0232"
        )

    def _save_fallback_leaders(self, leaders):
        if not leaders:
            print("[ERA 폴백] 거래대금상위 데이터가 비어 있어 주도주 셋업을 스킵합니다.")
            return
            
        today = datetime.now().strftime("%Y-%m-%d")
        try:
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
            print(f"\n[ERA 폴백] 키움 API 기반 주도주 {len(leaders)}개 DB 적재 완료 (네이버 크롤링 대체)")
            
            if notifier:
                notifier.send_message(
                    f"⚠️ <b>[장전 폴백 가동]</b>\n"
                    f"네이버 크롤링 실패로 인해 키움 OpenAPI 거래대금 상위 종목으로 매매 후보를 대체합니다.\n"
                    f"후보: <b>{len(leaders)}개</b> 종목\n"
                    f"• {', '.join(x['name'] for x in leaders[:10])} 등\n\n"
                    f"🔬 RSA 정밀 분석 시작 중..."
                )
            self._trigger_rsa_premarket()
            QTimer.singleShot(1000, self._register_theme_realtime)
        except Exception as e:
            print(f"[ERA 폴백 DB 적재 오류] {e}")

    def _refresh_intraday_leaders(self):
        if self.trading_mode not in ('stock', 'both'):
            return
            
        now = datetime.now()
        if now.weekday() >= 5: # 주말 배제
            return
        if not (9 <= now.hour <= 15):
            return
        if now.hour == 9 and now.minute < 5: # 09:05 이전 제외
            return
        if now.hour == 15 and now.minute > 0: # 15:00 이후 제외
            return
            
        print("[ERA 장중 갱신] 키움 API를 통해 당일 거래대금 상위 종목 조회를 요청합니다...")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시장구분", "000") # 전체
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "관리종목제외", "1")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "우선주제외", "1")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "신용구분", "0")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "거래대금구분", "1") # 전체
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "가격구분", "0")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "거래량구분", "0")
        
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "장중거래대금상위조회", "opt10032", 0, "0232"
        )

    def _apply_intraday_leaders(self, leaders):
        if not leaders:
            return
            
        added_names = []
        for item in leaders:
            code = item["code"]
            name = item["name"]
            if code not in self.theme_stocks:
                self.theme_stocks[code] = name
                added_names.append(name)
                # 실시간 데이터 감시 등록
                self.kiwoom.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    "THEME_RT", code, "10;11;12;15", "1"
                )
                
        if added_names:
            print(f"[ERA 장중 동적 편입] {len(added_names)}종목 추가 등록 완료: {added_names}")
            if notifier:
                notifier.send_message(
                    f"🔥 <b>[장중 주도주 동적 편입]</b>\n"
                    f"거래대금 급증 감지로 인해 새로운 주도주들을 감시 목록에 추가합니다.\n"
                    f"➕ <b>추가 종목:</b> {', '.join(added_names)}\n"
                    f"💡 <i>단타 5분 스캔 감시 실시간 연동 완료</i>"
                )

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
                now = datetime.now()
                # 늦은 기동 폴백: 08:50 이후 14:00 이전이면 즉시 크롤링
                # _morning_theme_crawl 내부에서 QTimer.singleShot(1000, _register_theme_realtime) 호출하므로
                # 크롤 완료 1초 후 이 함수가 재실행되어 구독 등록까지 처리됨
                if now.weekday() < 5 and (now.hour > 8 or (now.hour == 8 and now.minute >= 50)) and now.hour < 14:
                    print("[ERA 실시간 구독] 오늘 테마 데이터 없음 — 늦은 기동 감지, 즉시 크롤링 시도")
                    self._morning_theme_crawl()
                else:
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

    def _update_futures_ohlcv(self, code, price):
        """선물 실시간 틱 → 5분봉 OHLCV 인메모리 버퍼 갱신 (30초마다 DB 동기화)
        야간 세션 데이터를 futures_ohlcv 테이블에 축적해서 향후 야간 백테스트 가능하게 함"""
        now = datetime.now()
        period_min = (now.minute // 5) * 5
        period_str = now.strftime(f"%Y%m%d{now.hour:02d}") + f"{period_min:02d}00"
        if code not in self.ohlcv_buffer:
            self.ohlcv_buffer[code] = {}
        buf = self.ohlcv_buffer[code]
        if period_str not in buf:
            buf[period_str] = {'o': price, 'h': price, 'l': price, 'c': price, 'v': 1}
        else:
            c = buf[period_str]
            if price > c['h']:
                c['h'] = price
            if price < c['l']:
                c['l'] = price
            c['c'] = price
            c['v'] += 1

    def _flush_ohlcv_buffer(self):
        """30초마다 인메모리 OHLCV 버퍼를 DB에 일괄 동기화
        - 주식 코드 (6자리 이하): unified_data.db intraday_ohlcv
        - 선물 코드 (8자리+): futures_data.db futures_ohlcv (야간 데이터 축적용)
        """
        if not self.ohlcv_buffer:
            return

        futures_codes = {getattr(self, 'real_day_code', '10100000'),
                         getattr(self, 'real_night_code', '10500000')}

        stock_rows   = []
        futures_rows = []
        for code, periods in self.ohlcv_buffer.items():
            is_futures = (code in futures_codes or len(code) > 6)
            for period_str, c in periods.items():
                row = (code, period_str, c['o'], c['h'], c['l'], c['c'], c['v'])
                if is_futures:
                    futures_rows.append(row)
                else:
                    stock_rows.append(row)

        try:
            if stock_rows:
                conn = sqlite3.connect(self.unified_db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL;")
                cursor = conn.cursor()
                cursor.execute("""CREATE TABLE IF NOT EXISTS intraday_ohlcv
                                  (code TEXT, date TEXT, open INTEGER, high INTEGER,
                                   low INTEGER, close INTEGER, volume INTEGER, UNIQUE(code, date))""")
                cursor.executemany(
                    "REPLACE INTO intraday_ohlcv (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                    stock_rows
                )
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[ERA 주식 OHLCV 플러시 오류] {e}")

        try:
            if futures_rows:
                conn = sqlite3.connect(self.futures_db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL;")
                cursor = conn.cursor()
                cursor.execute("""CREATE TABLE IF NOT EXISTS futures_ohlcv
                                  (code TEXT, date TEXT, open REAL, high REAL,
                                   low REAL, close REAL, volume INTEGER, UNIQUE(code, date))""")
                cursor.executemany(
                    "REPLACE INTO futures_ohlcv (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                    futures_rows
                )
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[ERA 선물 OHLCV 플러시 오류] {e}")

    # ── 선물 K값 변동성 돌파 전략 ────────────────────────────────────────

    def _init_futures_strategy(self):
        """로그인 성공 후 선물 전략 초기화 (주간 + 야간)"""
        self._load_futures_k()

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
        
        # 선물 과거 5분봉 자동 DB 동기화 개시
        self._start_futures_db_sync()

    def _determine_sync_pages(self, code):
        """DB에 이미 적재된 데이터의 최신 일시를 체크하여 동기화할 페이지 수 결정 (2페이지 vs 10페이지)"""
        try:
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='futures_ohlcv'")
            if not cursor.fetchone():
                conn.close()
                return 10
                
            cursor.execute("SELECT MAX(date) FROM futures_ohlcv WHERE code = ?", (code,))
            row = cursor.fetchone()
            conn.close()
            
            if row and row[0]:
                latest_date_str = row[0] # YYYYMMDDHHMMSS
                latest_date = datetime.strptime(latest_date_str[:8], "%Y%m%d")
                delta = datetime.now() - latest_date
                if delta.days <= 2:
                    print(f"   [ERA 선물 동기화] {code}의 로컬 DB 최신 데이터는 {latest_date_str[:8]}입니다. (공백 {delta.days}일) 2페이지 동기화 진행.")
                    return 2
                else:
                    print(f"   [ERA 선물 동기화] {code}의 로컬 DB 최신 데이터는 {latest_date_str[:8]}입니다. (공백 {delta.days}일) 10페이지 전체 동기화 진행.")
                    return 10
            else:
                return 10
        except Exception as e:
            print(f"[ERA 선물 동기화] 페이지 수 결정 에러: {e}")
            return 10

    def _start_futures_db_sync(self):
        """선물 과거 5분봉 데이터베이스 자동 동기화 기동"""
        if self.futures_sync_active:
            return
            
        print("\n[ERA 선물] 과거 5분봉 자동 동기화 시퀀스를 기동합니다...")
        if notifier:
            notifier.send_message("⏳ <b>[선물 데이터 자동 동기화]</b>\n누락된 최근 5분봉 과거 데이터를 동기화 중입니다...")
            
        self.futures_sync_queue = []
        # 주간 및 야간 동기화 대상 코드 큐 적재 (실제 최근월물 코드 우선 등록하여 데이터 혼선 방지)
        codes_to_sync = []
        if getattr(self, 'real_day_code', None):
            codes_to_sync.append(self.real_day_code)
        if getattr(self, 'real_night_code', None):
            codes_to_sync.append(self.real_night_code)
        codes_to_sync.extend(["10100000", "10500000"])
        
        for code in codes_to_sync:
            if code and code not in self.futures_sync_queue:
                self.futures_sync_queue.append(code)
                
        self.futures_sync_index = 0
        self.futures_sync_current_page = 0
        self.futures_sync_active = True
        
        self._request_sync_tr()

    def _request_sync_tr(self, prev_next="0"):
        if not self.futures_sync_active:
            return
            
        if self.futures_sync_index >= len(self.futures_sync_queue):
            # 모든 코드의 동기화 완료!
            self.futures_sync_active = False
            print("[ERA 선물] 과거 5분봉 데이터베이스 자동 동기화 완료!")
            self._load_prev_range()
            self.update_futures_dynamic_sl_tp()
            self.futures_strategy_active = True
            
            print(f"\n[ERA 선물 전략 활성화] K={self.futures_best_k:.2f} | 전일Range={self.futures_prev_range:.2f}pt")
            print(f"  ▶ 주간 구독: {self.real_day_code}  |  야간 구독: {self.real_night_code}")
            
            if notifier:
                notifier.send_message(
                    f"✅ <b>[선물 데이터 동기화 완료]</b>\n"
                    f"• K값: {self.futures_best_k:.2f} | 전일 Range: {self.futures_prev_range:.2f}pt\n"
                    f"• 실시간 감시 전략이 정상 가동됩니다."
                )
            return

        code = self.futures_sync_queue[self.futures_sync_index]
        if self.futures_sync_current_page == 0:
            self.futures_sync_max_pages = self._determine_sync_pages(code)
            
        print(f" -> [ERA 선물 동기화] {code} ({self.futures_sync_current_page + 1}/{self.futures_sync_max_pages} 페이지) 요청 중...")
        
        # TR 입력값 설정
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시간단위", "5") # 5분봉
        
        # 0.2초 딜레이 후 조회 (TR 과부하 방지)
        QTimer.singleShot(200, lambda: self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "선물과거분차트동기화", "opt50029", int(prev_next), "5029"
        ))

    def _load_futures_k(self):
        """active_strategy.json 에서 최적 K값 및 손절/익절 한도 로드"""
        strategy_file = os.path.join(self.workspace_root, "config", "active_strategy.json")
        try:
            with open(strategy_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.futures_best_k = float(data.get("best_k", 0.5))
            self.futures_stop_loss_pt = float(data.get("stop_loss_pt", 2.0))
            self.futures_take_profit_pt = float(data.get("take_profit_pt", 5.0))
            print(f"[ERA BQA 연동] 최적화 파라미터 로드 완료: K={self.futures_best_k}, 손절={self.futures_stop_loss_pt}pt, 익절={self.futures_take_profit_pt}pt")
        except Exception as e:
            print(f"[ERA BQA 로드 경고] {e} — 임시 디폴트 K=0.5, 손절=2.0pt, 익절=5.0pt 폴백 적용")
            self.futures_best_k = 0.5
            self.futures_stop_loss_pt = 2.0
            self.futures_take_profit_pt = 5.0
            if notifier:
                notifier.send_message(
                    "⚠️ <b>[BQA 동기화 지연 경보]</b>\n"
                    "최적화 파라미터 파일 로드에 실패하였습니다.\n"
                    "임시 안전 규격(디폴트 K=0.5, 손절=2.0pt, 익절=5.0pt)으로 매매 감시를 무중단 유지합니다.\n"
                    "구글 드라이브 동기화 상태를 확인해 주세요!"
                )

    def _load_prev_range(self):
        """futures_data.db 에서 전일 고저폭(Range) 계산"""
        try:
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            # 날짜별 일봉 집계 → 가장 최근 완성된 전일 데이터
            # 주의: date 컬럼이 '20260519154500' 형식이므로 date() 대신 SUBSTR 사용
            # 폴백: API 코드(예: 105V6000) 매칭 실패 시 prefix 기반 generic 코드로 재조회
            for query_code in [self.real_day_code, self.futures_prefix + "00000"]:
                cursor.execute("""
                    SELECT SUBSTR(date, 1, 8) as d, MAX(high) as h, MIN(low) as l
                    FROM futures_ohlcv WHERE code = ?
                    GROUP BY SUBSTR(date, 1, 8) ORDER BY d DESC LIMIT 2
                """, (query_code,))
                rows = cursor.fetchall()
                if len(rows) >= 2:
                    break
            conn.close()
            if len(rows) >= 2:
                prev_h, prev_l = rows[1][1], rows[1][2]
                calc = prev_h - prev_l
                if calc > 0:
                    self.futures_prev_range = calc
                    print(f"[ERA 선물] 전일 Range 로드 완료: {calc:.2f}pt (조회코드: {query_code})")
        except Exception as e:
            print(f"[ERA 선물] 전일 Range 로드 실패: {e}")

    def _get_today_futures_open(self, code):
        """오늘 주간 첫 5분봉 시가를 DB에서 조회 (늦은 기동 시 실제 시초가 복원 - 08:45 개장 반영)"""
        try:
            today_str = datetime.now().strftime("%Y%m%d")
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            # real_day_code(예: 105V6000) → DB 저장 코드(10500000) 순으로 폴백
            for query_code in [code, self.futures_prefix + "00000", "10500000", "10100000"]:
                cursor.execute(
                    "SELECT open FROM futures_ohlcv WHERE code = ? AND date LIKE ? ORDER BY date ASC LIMIT 1",
                    (query_code, today_str + "%")
                )
                row = cursor.fetchone()
                if row and row[0] and row[0] > 0:
                    conn.close()
                    print(f"[주간선물] 오늘 시초가 DB 복원 성공: {row[0]:.2f}pt (code={query_code})")
                    return float(row[0])
            conn.close()
        except Exception as e:
            print(f"[주간선물] 시초가 DB 조회 실패: {e}")
        return 0.0

    # ── 개별주식선물 (ISF) 엔진 ─────────────────────────────────────────────

    def _init_isf_strategy(self):
        """개별주식선물 코드 탐지 → 실시간 구독 → 전일 Range 로드"""
        if not self.isf_configs:
            return
        detected = []
        not_found = []

        # GetFutureList()에서 개별주식선물 코드 탐지 시도
        try:
            full_list = self.kiwoom.dynamicCall("GetFutureList()").strip()
            all_codes = [c for c in full_list.split(";") if c]
        except Exception:
            all_codes = []

        for isf_cfg in self.isf_configs:
            sc = isf_cfg["stock_code"]
            fc = isf_cfg.get("futures_code", "").strip()

            if not fc:
                # GetFutureList 결과에서 종목코드 포함 코드 탐지
                for code in all_codes:
                    if sc in code:
                        fc = code
                        isf_cfg["futures_code"] = fc
                        break

            if not fc:
                # GetOptionCode로 주식선물 코드 탐지 시도
                try:
                    result = self.kiwoom.dynamicCall(
                        "GetOptionCode(QString, QString, QString, QString)",
                        ["F", "0", sc, ""]
                    ).strip()
                    if result:
                        fc = result.split(";")[0]
                        isf_cfg["futures_code"] = fc
                except Exception:
                    pass

            if fc:
                self.isf_code_map[fc] = sc
                # 실시간 구독 등록 (주식선물 FID: 10=현재가, 228=전일종가)
                self.kiwoom.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    "ISF_MON", fc, "10;228", "1"
                )
                self._load_isf_prev_range(isf_cfg)
                detected.append(f"{isf_cfg['name']}({fc})")
            else:
                not_found.append(isf_cfg['name'])

        if detected:
            print(f"[ISF] 구독 등록 완료: {', '.join(detected)}")
            if notifier:
                notifier.send_message(
                    f"✅ <b>[ISF 코드 자동 탐지 성공]</b>\n\n"
                    + "\n".join(f"• {d}" for d in detected) +
                    f"\n\n실시간 구독 등록 완료. 09:00부터 방향 감시 시작."
                )

        if not_found and notifier:
            notifier.send_message(
                f"⚠️ <b>[ISF 코드 미탐지]</b>\n"
                f"{', '.join(not_found)} 개별주식선물 코드를 찾지 못했습니다.\n\n"
                f"📌 <b>해결 방법 (텔레그램으로 직접 입력):</b>\n"
                f"1. 키움 HTS → 선물옵션 → 종목검색에서 코드 확인\n"
                f"2. 텔레그램에 입력:\n"
                f"<code>!ISF코드 005930 여기에코드입력</code>\n"
                f"<code>!ISF코드 000660 여기에코드입력</code>\n"
                f"3. <code>!시스템재시작</code> 으로 ERA 재시작"
            )

        # 09:00 방향 체크 즉시 1회 실행
        self._check_isf_direction()

    def _load_isf_prev_range(self, isf_cfg):
        """개별주식선물 전일 고저폭(원) 로드"""
        sc = isf_cfg["stock_code"]
        fc = isf_cfg.get("futures_code", "")
        if not fc:
            return
        try:
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS isf_ohlcv
                (code TEXT, date TEXT, open REAL, high REAL,
                 low REAL, close REAL, volume INTEGER, UNIQUE(code, date))
            """)
            conn.commit()
            cursor.execute("""
                SELECT SUBSTR(date,1,8) as d, MAX(high) as h, MIN(low) as l
                FROM isf_ohlcv WHERE code = ?
                GROUP BY d ORDER BY d DESC LIMIT 2
            """, (fc,))
            rows = cursor.fetchall()
            conn.close()
            if len(rows) >= 2:
                prev_range = rows[1][1] - rows[1][2]
                if prev_range > 0:
                    self.isf_prev_range[sc] = prev_range
                    print(f"[ISF] {isf_cfg['name']} 전일 Range: {prev_range:,.0f}원")
        except Exception as e:
            print(f"[ISF] {isf_cfg['name']} Range 로드 실패: {e}")

    def _check_isf_direction(self):
        """research_reports 에서 NSAA 점수 조회 → 오늘의 Long/Short/Neutral 방향 결정"""
        if not self.isf_configs:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(self.unified_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            for isf_cfg in self.isf_configs:
                sc = isf_cfg["stock_code"]
                cursor.execute(
                    "SELECT nsaa_score FROM research_reports WHERE code=? AND date(timestamp)=? ORDER BY id DESC LIMIT 1",
                    (sc, today)
                )
                row = cursor.fetchone()
                prev_dir = self.isf_direction.get(sc, "NEUTRAL")
                if row:
                    nsaa = row[0]
                    long_min  = isf_cfg.get("nsaa_long_min", 72)
                    short_max = isf_cfg.get("nsaa_short_max", 35)
                    long_only = isf_cfg.get("long_only", False)
                    if nsaa >= long_min:
                        self.isf_direction[sc] = "LONG"
                    elif not long_only and nsaa <= short_max:
                        # long_only=True 이면 SHORT 방향 무시 → NEUTRAL 처리
                        self.isf_direction[sc] = "SHORT"
                    else:
                        self.isf_direction[sc] = "NEUTRAL"
                    new_dir = self.isf_direction[sc]
                    print(f"[ISF] {isf_cfg['name']} NSAA={nsaa}점 → 방향: {new_dir}")
                    if prev_dir != new_dir and notifier:
                        icon = {"LONG": "📈", "SHORT": "📉", "NEUTRAL": "⏸️"}.get(new_dir, "")
                        notifier.send_message(
                            f"{icon} <b>[ISF 방향 결정] {isf_cfg['name']}</b>\n"
                            f"• NSAA 뉴스감성: {nsaa}점\n"
                            f"• 오늘 방향: <b>{new_dir}</b>\n"
                            + (f"• K={isf_cfg.get('best_k',0.35)} | 손절-{isf_cfg.get('stop_loss_pct',1.5)}% | 익절+{isf_cfg.get('take_profit_pct',4.0)}%"
                               if new_dir != "NEUTRAL" else "• 오늘 거래 없음 (뉴스 중립)")
                        )
                else:
                    self.isf_direction[sc] = "NEUTRAL"
            conn.close()
        except Exception as e:
            print(f"[ISF] 방향 체크 오류: {e}")

    def _update_isf_direction_if_needed(self):
        """09:00~09:05 사이에 RSA 방향 갱신 (1분 주기 타이머에서 호출)"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.hour == 9 and now.minute <= 5 and self.isf_direction_date != today:
            self.isf_direction_date = today
            self._check_isf_direction()
            # 방향 갱신과 함께 당일 상태 초기화
            for isf_cfg in self.isf_configs:
                sc = isf_cfg["stock_code"]
                self.isf_day_open[sc] = 0.0
                self.isf_target_long[sc] = float('inf')
                self.isf_target_short[sc] = float('-inf')
                self.isf_order_locked[sc] = False
                self.isf_entry_price[sc] = 0.0
                self.isf_peak_price[sc] = 0.0
                self._load_isf_prev_range(isf_cfg)

    def _process_isf_tick(self, futures_code, price):
        """개별주식선물 실시간 틱 처리 — RSA 방향 기반 K값 돌파 전략"""
        if price <= 0:
            return
        sc = self.isf_code_map.get(futures_code)
        if sc is None:
            return
        isf_cfg = next((c for c in self.isf_configs if c["stock_code"] == sc), None)
        if isf_cfg is None:
            return

        direction = self.isf_direction.get(sc, "NEUTRAL")
        if direction == "NEUTRAL":
            return   # RSA 중립이면 오늘 거래 없음

        now = datetime.now()
        is_trading_session = (9 <= now.hour < 15) or (now.hour == 15 and now.minute <= 30)
        if not is_trading_session:
            return

        # 시초가 설정 (첫 틱)
        if self.isf_day_open.get(sc, 0) == 0:
            db_open = self._get_isf_day_open(futures_code, sc)
            open_price = db_open if db_open > 0 else price
            prev_range = self.isf_prev_range.get(sc, price * 0.02)  # 전일 Range 없으면 2% 추정
            k = isf_cfg.get("best_k", 0.35)
            self.isf_day_open[sc] = open_price
            self.isf_target_long[sc] = open_price + prev_range * k
            self.isf_target_short[sc] = open_price - prev_range * k
            print(f"[ISF] {isf_cfg['name']} 시초가={open_price:,}원 | "
                  f"LONG목표={self.isf_target_long[sc]:,.0f} | SHORT목표={self.isf_target_short[sc]:,.0f}")
            if notifier:
                notifier.send_message(
                    f"🌅 <b>[ISF 목표가] {isf_cfg['name']}</b>\n"
                    f"• 방향: {direction} | K={k}\n"
                    f"• 시초가: {open_price:,}원\n"
                    + (f"• LONG ▲ {self.isf_target_long[sc]:,.0f}원" if direction == "LONG"
                       else f"• SHORT ▼ {self.isf_target_short[sc]:,.0f}원")
                )

        # 포지션 보유 중: 손절/익절 감시
        if sc in self.isf_positions:
            pos = self.isf_positions[sc]
            entry = self.isf_entry_price.get(sc, 0)
            if entry > 0:
                ts_enabled = isf_cfg.get("ts_enabled", False)
                ts_activate_pct = isf_cfg.get("ts_activate_pct", 2.0)
                ts_trail_pct = isf_cfg.get("ts_trail_pct", 0.8)

                if pos["type"] == "LONG":
                    if sc not in self.isf_peak_price or self.isf_peak_price[sc] == 0.0:
                        self.isf_peak_price[sc] = price
                    else:
                        self.isf_peak_price[sc] = max(self.isf_peak_price[sc], price)

                    pnl_pct = (price - entry) / entry * 100
                    max_pnl_pct = (self.isf_peak_price[sc] - entry) / entry * 100

                    # 1. 고정 손절 (언제나 활성화)
                    if pnl_pct <= -isf_cfg.get("stop_loss_pct", 1.5):
                        print(f"[ISF] {isf_cfg['name']} LONG 손절: {pnl_pct:+.2f}%")
                        self._execute_isf_order(isf_cfg, "LONG_EXIT", price)
                        self.isf_peak_price[sc] = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[ISF 손절] {isf_cfg['name']}</b> {pnl_pct:+.2f}% | 진입:{entry:,} → {price:,}원")
                    # 2. 트레일링 스탑 감시
                    elif ts_enabled and max_pnl_pct >= ts_activate_pct:
                        ts_threshold = self.isf_peak_price[sc] * (1 - ts_trail_pct / 100)
                        if price <= ts_threshold:
                            peak_snapshot = self.isf_peak_price[sc]
                            print(f"[ISF] {isf_cfg['name']} LONG 트레일링 스탑 작동: 현재 {pnl_pct:+.2f}% (고점 {max_pnl_pct:+.2f}%, 기준선 {ts_threshold:,.0f}원)")
                            self._execute_isf_order(isf_cfg, "LONG_EXIT", price)
                            self.isf_peak_price[sc] = 0.0
                            if notifier:
                                notifier.send_message(f"✨ <b>[ISF 트레일링 스탑] {isf_cfg['name']}</b> {pnl_pct:+.2f}% | 진입:{entry:,} → {price:,}원 (최고가:{peak_snapshot:,.0f}원)")
                    # 3. 고정 익절 (트레일링 비활성화 상태이거나 활성화 기준에 도달하지 못한 경우)
                    elif pnl_pct >= isf_cfg.get("take_profit_pct", 4.0):
                        print(f"[ISF] {isf_cfg['name']} LONG 익절: {pnl_pct:+.2f}%")
                        self._execute_isf_order(isf_cfg, "LONG_EXIT", price)
                        self.isf_peak_price[sc] = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[ISF 익절] {isf_cfg['name']}</b> {pnl_pct:+.2f}% | 진입:{entry:,} → {price:,}원")

                elif pos["type"] == "SHORT":
                    if sc not in self.isf_peak_price or self.isf_peak_price[sc] == 0.0:
                        self.isf_peak_price[sc] = price
                    else:
                        self.isf_peak_price[sc] = min(self.isf_peak_price[sc], price)

                    pnl_pct = (entry - price) / entry * 100
                    max_pnl_pct = (entry - self.isf_peak_price[sc]) / entry * 100

                    # 1. 고정 손절 (언제나 활성화)
                    if pnl_pct <= -isf_cfg.get("stop_loss_pct", 1.5):
                        print(f"[ISF] {isf_cfg['name']} SHORT 손절: {pnl_pct:+.2f}%")
                        self._execute_isf_order(isf_cfg, "SHORT_EXIT", price)
                        self.isf_peak_price[sc] = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[ISF 손절] {isf_cfg['name']}</b> {pnl_pct:+.2f}% | 진입:{entry:,} → {price:,}원")
                    # 2. 트레일링 스탑 감시
                    elif ts_enabled and max_pnl_pct >= ts_activate_pct:
                        ts_threshold = self.isf_peak_price[sc] * (1 + ts_trail_pct / 100)
                        if price >= ts_threshold:
                            peak_snapshot = self.isf_peak_price[sc]
                            print(f"[ISF] {isf_cfg['name']} SHORT 트레일링 스탑 작동: 현재 {pnl_pct:+.2f}% (고점 {max_pnl_pct:+.2f}%, 기준선 {ts_threshold:,.0f}원)")
                            self._execute_isf_order(isf_cfg, "SHORT_EXIT", price)
                            self.isf_peak_price[sc] = 0.0
                            if notifier:
                                notifier.send_message(f"✨ <b>[ISF 트레일링 스탑] {isf_cfg['name']}</b> {pnl_pct:+.2f}% | 진입:{entry:,} → {price:,}원 (최저가:{peak_snapshot:,.0f}원)")
                    # 3. 고정 익절 (트레일링 비활성화 상태이거나 활성화 기준에 도달하지 못한 경우)
                    elif pnl_pct >= isf_cfg.get("take_profit_pct", 4.0):
                        print(f"[ISF] {isf_cfg['name']} SHORT 익절: {pnl_pct:+.2f}%")
                        self._execute_isf_order(isf_cfg, "SHORT_EXIT", price)
                        self.isf_peak_price[sc] = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[ISF 익절] {isf_cfg['name']}</b> {pnl_pct:+.2f}% | 진입:{entry:,} → {price:,}원")
            return  # 포지션 보유 중 신규 진입 불가

        # 신규 진입 — RSA 방향에 맞는 목표가 돌파 시
        if self.isf_order_locked.get(sc, False) or self.system_halted:
            return
        if direction == "LONG" and price >= self.isf_target_long.get(sc, float('inf')):
            self.isf_entry_price[sc] = price
            self._execute_isf_order(isf_cfg, "LONG_ENTER", price)
        elif direction == "SHORT" and price <= self.isf_target_short.get(sc, float('-inf')):
            self.isf_entry_price[sc] = price
            self._execute_isf_order(isf_cfg, "SHORT_ENTER", price)

    def _execute_isf_order(self, isf_cfg, signal_type, price):
        """개별주식선물 주문 실행"""
        sc = isf_cfg["stock_code"]
        fc = isf_cfg.get("futures_code", "")
        if not fc:
            return
        if self.isf_order_locked.get(sc, False):
            return
        self.isf_order_locked[sc] = True

        # 방향 매핑 (lOrdKind: 1=신규매수, 2=신규매도)
        dir_map = {
            "LONG_ENTER":  (1, "LONG 진입"),
            "SHORT_ENTER": (2, "SHORT 진입"),
            "LONG_EXIT":   (2, "LONG 청산"),
            "SHORT_EXIT":  (1, "SHORT 청산"),
        }
        trade_dir, label = dir_map.get(signal_type, (None, ""))
        if trade_dir is None:
            self.isf_order_locked[sc] = False
            return

        # 수량: EXIT이면 기존 수량, ENTER이면 5% 증거금 격리 비례 수량
        if "EXIT" in signal_type and sc in self.isf_positions:
            qty = self.isf_positions[sc].get("qty", 1)
        else:
            # [AMATS 파생 최적화] 예수금 비례 5% 한도 격리 (Virtual Margin Partitioning)
            # 주식선물 거래승수=10, 위탁증거금율 대략 15% 적용
            try:
                multiplier = 10
                margin_rate = 0.15
                margin_per = price * multiplier * margin_rate
                safe_budget = self.futures_available_balance * getattr(self, 'isf_margin_cap_ratio', 0.05)
                qty = max(1, int(safe_budget // margin_per)) if margin_per > 0 else 1
            except Exception:
                qty = 1 # 계산 예외 발생 시 안전 기본값 1계약 폴백

        ord_tp = "" if self.environment == "live" else "3"
        print(f"\n[ISF 주문] {isf_cfg['name']} {label} | {price:,}원 | {qty}계약 | {fc}")

        res = self.kiwoom.dynamicCall(
            "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
            ["ISFOrder", "0300", self.futures_account, fc, trade_dir, "03", ord_tp, qty, "0", ""]
        )
        if res == 0:
            if "EXIT" in signal_type:
                self.isf_order_locked[sc] = False
                if sc in self.isf_positions:
                    del self.isf_positions[sc]
                self.isf_entry_price[sc] = 0.0
                self.isf_peak_price[sc] = 0.0
            else:
                # 15초 내 체결 미확인 시 자동 잠금 해제
                def _isf_unlock(s=sc):
                    if self.isf_order_locked.get(s) and s not in self.isf_positions:
                        print(f"[ISF] {s} 15초 체결 미확인 → 잠금 해제")
                        self.isf_order_locked[s] = False
                        self.isf_entry_price[s] = 0.0
                        self.isf_peak_price[s] = 0.0
                QTimer.singleShot(15000, _isf_unlock)
        else:
            print(f"  => ISF 주문 실패 (res={res})")
            self.isf_order_locked[sc] = False
            self.isf_entry_price[sc] = 0.0
            self.isf_peak_price[sc] = 0.0

    def _get_isf_day_open(self, futures_code, stock_code):
        """ISF 오늘 09시 시초가 DB에서 조회"""
        try:
            today_prefix = datetime.now().strftime("%Y%m%d09")
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT open FROM isf_ohlcv WHERE code=? AND date LIKE ? ORDER BY date LIMIT 1",
                (futures_code, today_prefix + "%")
            )
            row = cursor.fetchone()
            conn.close()
            if row and row[0] > 0:
                return float(row[0])
        except Exception:
            pass
        return 0.0

    def _update_isf_ohlcv(self, futures_code, price):
        """ISF 실시간 틱 → 5분봉 버퍼 갱신 (ohlcv_buffer 공유)"""
        now = datetime.now()
        period_min = (now.minute // 5) * 5
        period_str = now.strftime(f"%Y%m%d{now.hour:02d}") + f"{period_min:02d}00"
        if futures_code not in self.ohlcv_buffer:
            self.ohlcv_buffer[futures_code] = {}
        buf = self.ohlcv_buffer[futures_code]
        if period_str not in buf:
            buf[period_str] = {'o': price, 'h': price, 'l': price, 'c': price, 'v': 1}
        else:
            c = buf[period_str]
            c['h'] = max(c['h'], price)
            c['l'] = min(c['l'], price)
            c['c'] = price
            c['v'] += 1

    def _get_today_night_open(self, code, now):
        """오늘 야간 세션 첫 5분봉 시가를 DB에서 조회 (늦은 기동 시 실제 야간 시초가 복원)"""
        try:
            # 야간 시작 시각 접두어 (18시 → '202605291800...')
            today_str = now.strftime("%Y%m%d")
            yesterday_str = (now.replace(hour=0, minute=0, second=0) -
                             timedelta(days=1)).strftime("%Y%m%d")
            # 새벽(00~04)은 전날 밤 18시 이후 데이터 조회
            if now.hour < 5:
                date_prefix = yesterday_str + "18"
            else:
                date_prefix = today_str + "18"
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            for query_code in [code, self.futures_prefix + "00000", "10500000", "10100000"]:
                cursor.execute(
                    "SELECT open FROM futures_ohlcv WHERE code = ? AND date LIKE ? ORDER BY date LIMIT 1",
                    (query_code, date_prefix + "%")
                )
                row = cursor.fetchone()
                if row and row[0] and row[0] > 0:
                    conn.close()
                    print(f"[야간선물] 야간 시초가 DB 복원 성공: {row[0]:.2f}pt (code={query_code})")
                    return float(row[0])
            conn.close()
        except Exception as e:
            print(f"[야간선물] 야간 시초가 DB 조회 실패: {e}")
        return 0.0

    def _process_futures_tick(self, code, current_price):
        """실시간 선물 현재가 수신 — 주간/야간 세션 분리 처리"""
        if current_price <= 0:
            return

        # 미니선물(105)은 주간/야간 코드가 동일하므로 시간으로 세션 구분
        real_day = getattr(self, 'real_day_code', '10100000')
        real_night = getattr(self, 'real_night_code', '10500000')
        if real_day == real_night:
            h = datetime.now().hour
            is_night = (h >= 18) or (h < 5)
        else:
            is_night = (code == real_night)

        # 실시간 선물 포지션 현재가 업데이트 (TCA 계좌확인용)
        pos_key = "KOSPI200_NIGHT" if is_night else "KOSPI200"
        if pos_key in self.futures_positions:
            self.futures_positions[pos_key]['current_price'] = current_price

        if not self.futures_strategy_active:
            return

        now = datetime.now()
        if is_night:
            self._process_night_tick(code, current_price, now)
        else:
            self._process_day_tick(code, current_price, now)

    def _process_day_tick(self, code, current_price, now):
        """주간 선물 전략 (09:00 진입 → 익일 08:45 청산, 3pt 손절 / 대안 C 트레일링 스탑)"""
        pos_key = "KOSPI200"

        # 09:00 ~ 15:45 정규장 시초가 및 목표가 동적 생성
        is_day_session = (now.hour == 9 and now.minute >= 0) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 45)
        if is_day_session and self.futures_day_open == 0:
            db_open = self._get_today_futures_open(code)
            day_open = db_open if db_open > 0 else current_price
            self.futures_day_open     = day_open
            self.futures_target_long  = day_open + self.futures_prev_range * self.futures_best_k
            self.futures_target_short = day_open - self.futures_prev_range * self.futures_best_k
            src_label = "DB 시초가" if db_open > 0 else "현재가(폴백)"
            print(f"\n[주간선물] ✅ 시초가 설정: {day_open:.2f}pt ({src_label})")
            print(f"  LONG목표: {self.futures_target_long:.2f}  SHORT목표: {self.futures_target_short:.2f}")
            print(f"  손절: {self.futures_stop_loss_pt}pt | 대안 C 트레일링 스탑 적용 (3pt 이상 상승 시 가동 ➡️ 최고가 대비 -2pt 청산)")
            if notifier:
                notifier.send_message(
                    f"🌅 <b>[주간선물 목표가]</b>\n"
                    f"• 시초가: {day_open:.2f}pt ({src_label})\n"
                    f"• LONG ▲ {self.futures_target_long:.2f}pt\n"
                    f"• SHORT ▼ {self.futures_target_short:.2f}pt\n"
                    f"• <b>[대안 C 적용]</b> 3pt 가동 ➡️ 최고가 -2pt 트레일링 스탑"
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
                self.futures_day_peak = 0.0
            return

        # ── 포지션 보유 중: 손절 / 대안 C 트레일링 스탑 감시 ──
        if pos_key in self.futures_positions:
            pos = self.futures_positions[pos_key]
            entry = self.futures_day_entry_price
            if entry > 0:
                if pos['type'] == 'LONG':
                    # 최고가 추적 및 갱신
                    if current_price > self.futures_day_peak:
                        self.futures_day_peak = current_price
                    
                    pnl_pt = current_price - entry
                    max_pnl_pt = self.futures_day_peak - entry # 진입 후 도달한 최고 수익폭
                    
                    # 1. 고정 손절 감시 (트레일링 가동 전까지 계좌 보호)
                    if pnl_pt <= -self.futures_stop_loss_pt:
                        print(f"[주간선물] 🛑 LONG 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                        self.futures_day_entry_price = 0.0
                        self.futures_day_peak = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[주간선물 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f}")
                        return
                    
                    # 2. 대안 C 트레일링 스탑 감시 (최고 수익이 +3.0 pt 이상 도달했을 때부터 기동)
                    elif max_pnl_pt >= 3.0:
                        ts_price = self.futures_day_peak - 2.0
                        if current_price <= ts_price:
                            realized_pnl = current_price - entry
                            peak_snapshot = self.futures_day_peak
                            print(f"[주간선물] 🎯 LONG 트레일링 스탑 발동! 최고가:{peak_snapshot:.2f} 현재가(청산):{current_price:.2f} 익절:{realized_pnl:+.2f}pt")
                            self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                            self.futures_day_entry_price = 0.0
                            self.futures_day_peak = 0.0
                            if notifier:
                                notifier.send_message(f"🎯 <b>[주간선물 트레일링 익절]</b> {realized_pnl:+.2f}pt (최고가:{peak_snapshot:.2f} ➡️ 청산:{current_price:.2f})")
                            return

                elif pos['type'] == 'SHORT':
                    # 최저가(숏 포지션이므로 가격이 낮아질수록 최고 수익) 추적 및 갱신
                    if current_price < self.futures_day_peak or self.futures_day_peak == 0:
                        self.futures_day_peak = current_price
                    
                    pnl_pt = entry - current_price
                    max_pnl_pt = entry - self.futures_day_peak # 진입 후 도달한 최고 수익폭
                    
                    # 1. 고정 손절 감시
                    if pnl_pt <= -self.futures_stop_loss_pt:
                        print(f"[주간선물] 🛑 SHORT 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                        self.futures_day_entry_price = 0.0
                        self.futures_day_peak = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[주간선물 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f}")
                        return
                    
                    # 2. 대안 C 트레일링 스탑 감시 (최고 수익이 +3.0 pt 이상 도달했을 때부터 기동)
                    elif max_pnl_pt >= 3.0:
                        ts_price = self.futures_day_peak + 2.0
                        if current_price >= ts_price:
                            realized_pnl = entry - current_price
                            peak_snapshot = self.futures_day_peak
                            print(f"[주간선물] 🎯 SHORT 트레일링 스탑 발동! 최저가:{peak_snapshot:.2f} 현재가(청산):{current_price:.2f} 익절:{realized_pnl:+.2f}pt")
                            self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                            self.futures_day_entry_price = 0.0
                            self.futures_day_peak = 0.0
                            if notifier:
                                notifier.send_message(f"🎯 <b>[주간선물 트레일링 익절]</b> {realized_pnl:+.2f}pt (최저가:{peak_snapshot:.2f} ➡️ 청산:{current_price:.2f})")
                            return
            return  # 포지션 보유 중이면 신규 진입 불가

        # ── 신규 진입 조건 (09:00 장 초반 15분 노이즈 필터 연동) ──
        if not self.futures_order_locked and not self.system_halted:
            is_after_9 = (now.hour == 9 and now.minute >= 0) or (now.hour > 9)
            if is_after_9:
                # [AMATS 최적화] 초저변동성 구간 진입 차단 필터링 (ATR Cutoff)
                atr_val = getattr(self, 'futures_atr_14', 2.0)
                if atr_val < self.futures_atr_cutoff:
                    return

                if current_price >= self.futures_target_long:
                    self.futures_day_entry_price = current_price
                    self.futures_day_peak = current_price # 진입 즉시 초기화
                    self._execute_futures_direct("LONG_ENTER", current_price, code, pos_key)
                elif current_price <= self.futures_target_short:
                    self.futures_day_entry_price = current_price
                    self.futures_day_peak = current_price # 진입 즉시 초기화
                    self._execute_futures_direct("SHORT_ENTER", current_price, code, pos_key)

    def _process_night_tick(self, code, current_price, now):
        """야간 선물 전략 (18:00 진입 → 익일 04:45 청산, config.json futures_settings 고정 SL/TP)"""
        pos_key = "KOSPI200_NIGHT"

        # 18:00 ~ 새벽 04:45 사이 야간 세션 중도 기동 시에도 즉시 야간 시초가 및 목표가 동적 생성
        is_night_session = (now.hour >= 18) or (now.hour < 5)
        if is_night_session and self.futures_night_open == 0:
            # 늦은 기동 시 실제 야간 시초가 DB 조회 (없으면 현재가 폴백)
            db_night_open = self._get_today_night_open(code, now)
            night_open = db_night_open if db_night_open > 0 else current_price
            self.futures_night_open         = night_open
            self.futures_night_target_long  = night_open + self.futures_prev_range * self.futures_best_k
            self.futures_night_target_short = night_open - self.futures_prev_range * self.futures_best_k
            src_label = "DB 시초가" if db_night_open > 0 else "현재가(폴백)"
            print(f"\n[야간선물] ✅ 시초가 설정: {night_open:.2f}pt ({src_label})")
            print(f"  LONG목표: {self.futures_night_target_long:.2f}  SHORT목표: {self.futures_night_target_short:.2f}")
            print(f"  손절: {self.futures_night_stop_loss_pt}pt  익절: {self.futures_night_take_profit_pt}pt (고정)")
            if notifier:
                notifier.send_message(
                    f"🌙 <b>[야간선물 목표가]</b>\n"
                    f"• 시초가: {night_open:.2f}pt ({src_label})\n"
                    f"• LONG ▲ {self.futures_night_target_long:.2f}pt\n"
                    f"• SHORT ▼ {self.futures_night_target_short:.2f}pt\n"
                    f"• 손절: {self.futures_night_stop_loss_pt}pt | 익절: {self.futures_night_take_profit_pt}pt (고정)"
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
                    if pnl_pt <= -self.futures_night_stop_loss_pt:
                        print(f"[야간선물] 🛑 LONG 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[야간선물 손절]</b> {pnl_pt:+.2f}pt | 기준:{self.futures_night_stop_loss_pt}pt")
                        return
                    elif pnl_pt >= self.futures_night_take_profit_pt:
                        print(f"[야간선물] 🎯 LONG 익절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 수익:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[야간선물 익절]</b> {pnl_pt:+.2f}pt | 기준:{self.futures_night_take_profit_pt}pt")
                        return
                elif pos['type'] == 'SHORT':
                    pnl_pt = entry - current_price
                    if pnl_pt <= -self.futures_night_stop_loss_pt:
                        print(f"[야간선물] 🛑 SHORT 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🛑 <b>[야간선물 손절]</b> {pnl_pt:+.2f}pt | 기준:{self.futures_night_stop_loss_pt}pt")
                        return
                    elif pnl_pt >= self.futures_night_take_profit_pt:
                        print(f"[야간선물] 🎯 SHORT 익절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 수익:{pnl_pt:+.2f}pt")
                        self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                        self.futures_night_entry_price = 0.0
                        if notifier:
                            notifier.send_message(f"🎯 <b>[야간선물 익절]</b> {pnl_pt:+.2f}pt | 기준:{self.futures_night_take_profit_pt}pt")
                        return
            return  # 포지션 보유 중이면 신규 진입 불가

        # ── 신규 진입 조건 ──
        if not self.futures_night_order_locked and not self.system_halted:
            # [AMATS 최적화] 초저변동성 구간 진입 차단 필터링 (ATR Cutoff)
            atr_val = getattr(self, 'futures_atr_14', 2.0)
            if atr_val < self.futures_atr_cutoff:
                return

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

        # ord_kind: 1: 신규, 2: 청산
        # slby_tp: "1": 매도, "2": 매수
        direction_map = {
            "LONG_ENTER":  (1, "2", "LONG 진입 📈"),
            "SHORT_ENTER": (1, "1", "SHORT 진입 📉"),
            "LONG_EXIT":   (2, "1", "LONG 청산 📤"),
            "SHORT_EXIT":  (2, "2", "SHORT 청산 📤"),
        }
        ord_kind, slby_tp, label = direction_map.get(signal_type, (None, None, ""))
        if ord_kind is None:
            setattr(self, lock_attr, False)
            return

        # 수량 계산
        if "EXIT" in signal_type and pos_key in self.futures_positions:
            qty = self.futures_positions[pos_key].get("qty", 1)
        else:
            if getattr(self, 'futures_fixed_qty', None) is not None:
                qty = self.futures_fixed_qty
            else:
                multiplier = 50000 if getattr(self, 'futures_prefix', '101') == '105' else 250000
                margin_per = current_price * multiplier * 0.10
                
                # [AMATS 최적화] active_strategy.json의 마진캡(최적화 적용값 50%)을 반영한 자본 대비 계약 수 계산
                margin_cap = getattr(self, 'futures_margin_cap_ratio', 0.20)
                qty = max(1, int((self.futures_available_balance * margin_cap) / margin_per)) if margin_per > 0 else 1
                qty = min(qty, 15)  # 최대 계약수 한도 15계약으로 제약 (과도한 레버리지 노출 제약)

        session_label = "야간" if is_night else "주간"
        print(f"\n[{session_label}선물 주문] {label} | {current_price:.2f}pt | {qty}계약 | {order_code}")

        # sOrdTp: 시장가 주문 시 반드시 "3" 지정 (시장가 매매 시 가격은 "0"으로 전송)
        res = self.kiwoom.dynamicCall(
            "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
            ["FuturesLive", "0200", self.futures_account, order_code, ord_kind, slby_tp, "3", qty, "0", ""]
        )

        if res == 0:
            if "EXIT" in signal_type:
                setattr(self, lock_attr, False)
            else:
                # ENTER 주문 전송 후 15초 내 체결 미확인 시 잠금 자동 해제
                # (Mock 서버 무응답 또는 주문 거절 후 res=0 반환하는 경우 대비)
                def _unlock_if_no_fill():
                    if getattr(self, lock_attr) and pos_key not in self.futures_positions:
                        print(f"[{session_label}선물] ⚠️ 15초 체결 미확인 → 잠금 자동 해제 (주문 재시도 허용)")
                        setattr(self, lock_attr, False)
                        if is_night:
                            self.futures_night_entry_price = 0.0
                        else:
                            self.futures_day_entry_price = 0.0
                QTimer.singleShot(15000, _unlock_if_no_fill)
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
            "monthly_realized_loss": self.stock_monthly_loss,
            "monthly_initial_balance": self.stock_monthly_initial,
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
            "isf_positions": self.isf_positions,
            "isf_direction": self.isf_direction,
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

        # [수익률 추적] 일자별 자산 합계 기록 (daily_balance_history)
        try:
            from datetime import datetime as dt
            today_str = dt.now().strftime("%Y-%m-%d")
            
            if not hasattr(self, '_last_logged_balance_date') or self._last_logged_balance_date != today_str:
                stock_invested = 0
                for code, pos in self.portfolio.items():
                    buy_price = pos.get('buy_price', 0)
                    qty = pos.get('qty', 0)
                    current_price = pos.get('current_price', buy_price)
                    stock_invested += current_price * qty
                
                stock_total = self.stock_total_balance + stock_invested
                
                futures_pnl = 0
                for code, pos in self.futures_positions.items():
                    p_type = pos.get('type', 'LONG')
                    buy_price = pos.get('price', 0)
                    qty = pos.get('qty', 0)
                    current_price = pos.get('current_price', buy_price)
                    multiplier = 50000 if '105' in code else 250000
                    if p_type == 'LONG':
                        pnl = (current_price - buy_price) * qty * multiplier
                    else:
                        pnl = (buy_price - current_price) * qty * multiplier
                    futures_pnl += pnl
                    
                futures_total = self.futures_available_balance + futures_pnl
                combined_total = stock_total + futures_total
                
                if combined_total > 0:
                    import sqlite3
                    db_conn = sqlite3.connect(self.unified_db_path, timeout=30)
                    db_conn.execute("PRAGMA journal_mode=WAL;")
                    db_cursor = db_conn.cursor()
                    db_cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_balance_history (
                        date TEXT PRIMARY KEY,
                        stock_total REAL,
                        futures_total REAL,
                        combined_total REAL
                    )
                    """)
                    db_cursor.execute("""
                    INSERT OR REPLACE INTO daily_balance_history (date, stock_total, futures_total, combined_total)
                    VALUES (?, ?, ?, ?)
                    """, (today_str, round(stock_total, 2), round(futures_total, 2), round(combined_total, 2)))
                    db_conn.commit()
                    db_conn.close()
                    self._last_logged_balance_date = today_str
                    print(f"[ERA] daily_balance_history 기록 완료: {today_str} | Stock: {stock_total:,.0f} | Futures: {futures_total:,.0f} | Combined: {combined_total:,.0f}")
        except Exception as e:
            print(f"[ERA daily_balance_history 기록 에러] {e}")


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
        try:
            self._do_swing_close_time()
        except Exception as e:
            print(f"[ERA check_swing_close_time 오류] {e}")

    def _do_swing_close_time(self):
        now = datetime.now()
        # 1. 스윙 이평선 감시 (stock/both만)
        if self.trading_mode in ('stock', 'both'):
            # [추가] 단타(DAY) 종목 15:15 당일 무조건 시장가 일괄 청산 (오버나잇 금지)
            if now.hour == 15 and 15 <= now.minute < 30:
                for code, pos in list(self.portfolio.items()):
                    if pos.get('strategy') == 'DAY' and not pos.get('sell_ordered'):
                        print(f"\n🚨 [단타 장마감 강제 청산 발동] {pos['name']}({code}) - 오버나잇 방지 일괄 시장가 매도 주문 전송.")
                        pos['sell_ordered'] = True
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Day_Flat]", "0103", self.stock_account, 2, code, pos['qty'], 0, "03", ""]
                        )
            # [신설 - Fail-safe] 15:18 ~ 15:28 사이 미청산 단타 잔고 재차 청산 시도 (30초 주기)
            if now.hour == 15 and 18 <= now.minute < 28 and now.second % 30 == 0:
                for code, pos in list(self.portfolio.items()):
                    if pos.get('strategy') == 'DAY' and pos.get('qty', 0) > 0:
                        print(f"\n⚠️ [단타 미청산 감지] {pos['name']}({code}) - {pos['qty']}주 잔고 존재. 강제 재청산 주문 전송.")
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Day_Retry]", "0103", self.stock_account, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(
                                f"⚠️ <b>[주식 단타 미청산 비상 재청산] {pos['name']}</b>\n"
                                f"• 미청산 잔고({pos['qty']}주)가 감지되어 재청산 주문을 다시 전송합니다."
                            )

            if now.hour == 15 and now.minute >= 14 and not self.today_5ma_checked:
                self.today_5ma_checked = True
                print("\n[⏰ ERA 종가 익절 감시] 15:14+ 스윙 종목 5MA 체크를 시작합니다.")
                self.pending_5ma_checks = [c for c, p in self.portfolio.items() if p['strategy'] == 'SWING']
                self._request_next_5ma()
            elif now.hour < 9:
                self.today_5ma_checked = False
                
        # 2. [AMATS 파생 최적화] 개별주식선물(ISF) 15:20 당일 무조건 시장가 일괄 청산 (Daily Flat)
        if self.trading_mode in ('futures', 'both'):
            self.check_isf_daily_flat()

    def check_isf_daily_flat(self):
        """오후 15시 20분 도달 시 미체결/보유 중인 모든 ISF 포지션을 당일 일괄 청산(Flat)하여 오버나잇 갭 차단"""
        now = datetime.now()
        if not (now.hour == 15 and 20 <= now.minute < 30):
            return
            
        for sc, pos in list(self.isf_positions.items()):
            if pos.get("qty", 0) > 0 and not pos.get("flat_ordered", False):
                pos["flat_ordered"] = True
                isf_cfg = next((c for c in self.isf_configs if c["stock_code"] == sc), None)
                if isf_cfg:
                    exit_type = "LONG_EXIT" if pos["type"] == "LONG" else "SHORT_EXIT"
                    print(f"\n🚨 [ISF 장마감 강제 청산 발동] {pos['name']} - 오버나잇 갭 차단용 당일 청산 주문 전송.")
                    self._execute_isf_order(isf_cfg, exit_type, pos.get("current_price", 0))
                    if notifier:
                        notifier.send_message(
                            f"⚠️ <b>[ISF 장마감 강제 청산] {pos['name']}</b>\n"
                            f"• 오버나잇 갭 변동성 방지를 위해 15:20 기준 실계좌 시장가 일괄 청산(Flat)을 완료했습니다."
                        )

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
        # 예수금 조회 완료 전까지는 자금 기준이 없어 주문 불가 → skip
        if self.stock_total_balance == 0:
            return
        conn = sqlite3.connect(self.unified_db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, code, name, strategy_type, price, open_price FROM signals WHERE status = 'PENDING' LIMIT 3")
            rows = cursor.fetchall()
            
            for row in rows:
                signal_id, code, name, strategy_type, price, open_price = row
                print(f"\n[🚨 주식 신규 신호 감지] {name}({code}) | 유형: {strategy_type}")
                
                # 비정상 가격 필터 (ZeroDivisionError 원천 방지)
                if price <= 0 and strategy_type != 'MANUAL_SELL':
                    print(f" => [거절] 비정상 신호 가격: {price}")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_INVALID_PRICE' WHERE id = ?", (signal_id,))
                    continue

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
                has_table = cursor.fetchone()
                
                # 모의투자 환경: RSA 테이블 자동 생성 + 기존 저점수 포함 전체 우회 (80점 강제 삽입/갱신)
                if self.environment != "live" and not getattr(self, "apply_rsa_in_mock", False):
                    if not has_table:
                        # RSA coordinator와 동일한 11컬럼 스키마로 생성 (INSERT 호환)
                        cursor.execute("""CREATE TABLE IF NOT EXISTS research_reports (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            code TEXT, name TEXT, strategy_type TEXT,
                            faa_score INTEGER, faa_reason TEXT,
                            ira_score INTEGER, ira_reason TEXT,
                            nsaa_score INTEGER, nsaa_reason TEXT,
                            score INTEGER,
                            timestamp DATETIME DEFAULT (datetime('now', 'localtime')))""")
                        has_table = True
                    cursor.execute("SELECT score FROM research_reports WHERE code = ? ORDER BY id DESC LIMIT 1", (code,))
                    existing = cursor.fetchone()
                    if existing is None or existing[0] < 70:
                        cursor.execute(
                            "INSERT INTO research_reports (code, name, strategy_type, score, timestamp) VALUES (?, ?, 'MOCK', 80, ?)",
                            (code, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        )
                        print(f" => [모의투자 RSA 자동 통과] {name}({code}) 80점 삽입 (기존={existing[0] if existing else '없음'})")
                    has_table = True

                if has_table:
                    if getattr(self, "apply_rsa_in_mock", False):
                        cursor.execute("SELECT score FROM research_reports WHERE code = ? AND strategy_type != 'MOCK' ORDER BY id DESC LIMIT 1", (code,))
                    else:
                        cursor.execute("SELECT score FROM research_reports WHERE code = ? ORDER BY id DESC LIMIT 1", (code,))
                    
                    rep = cursor.fetchone()
                    if rep is None:
                        # apply_rsa_in_mock가 True인 경우 온디맨드로 분석 기동
                        if getattr(self, "apply_rsa_in_mock", False):
                            try:
                                from rsa.rsa_coordinator import RSACoordinator
                                coord = RSACoordinator()
                                if getattr(self, "gemini_api_key", None):
                                    coord.nsaa.api_key = self.gemini_api_key
                                print(f" => [모의투자 RSA 온디맨드 분석 기동] {name}({code})")
                                coord.evaluate_stock(code, name, strategy_type)
                                
                                # 다시 조회
                                cursor.execute("SELECT score FROM research_reports WHERE code = ? AND strategy_type != 'MOCK' ORDER BY id DESC LIMIT 1", (code,))
                                rep = cursor.fetchone()
                            except Exception as rsa_err:
                                print(f" => [모의투자 RSA 온디맨드 분석 실패] {rsa_err}")
                        
                        if rep is None:
                            print(f" => [보류] RSA 미평가 — PENDING 유지, 장전 RSA 분석 완료 후 자동 처리됨")
                            continue  # status 변경 없음 → 2초 후 재시도
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
                    # 30초 내 체결 미확인 시 pending_orders 자동 해제 (Mock 서버 무체결 대비)
                    def _clear_pending(c=code):
                        if c in self.pending_orders and c not in self.portfolio:
                            print(f"[ERA 주식] ⚠️ {c} 30초 체결 미확인 → pending 자동 해제")
                            del self.pending_orders[c]
                    QTimer.singleShot(30000, _clear_pending)
                else:
                    cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
                    del self.pending_orders[code]

            conn.commit()
        except Exception as e:
            print(f"[ERA 주식 폴링 에러] {e}")
        finally:
            conn.close()

    def _poll_futures_signals(self):
        conn = sqlite3.connect(self.futures_db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        try:
            cursor.execute("""CREATE TABLE IF NOT EXISTS signals
                              (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, signal_type TEXT,
                               price REAL, status TEXT DEFAULT 'PENDING')""")
            cursor.execute("SELECT id, code, signal_type, price FROM signals WHERE status = 'PENDING' LIMIT 1")
            row = cursor.fetchone()
            if row:
                signal_id, code, signal_type, price = row
                print(f"\n[🚨 선물 신규 신호 감지] {code} | {signal_type} | 현재가: {price}")
                
                # 비정상 가격 필터 (ZeroDivisionError 원천 방지)
                if price <= 0:
                    print(f"  => [거절] 비정상 신호 가격: {price}")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_INVALID_PRICE' WHERE id = ?", (signal_id,))
                    conn.commit()
                    conn.close()
                    return
                
                if getattr(self, 'futures_fixed_qty', None) is not None:
                    qty = self.futures_fixed_qty
                else:
                    # 선물 1계약 위탁증거금 계산
                    multiplier = 50000 if getattr(self, 'futures_prefix', '101') == '105' else 250000
                    margin_per_contract = price * multiplier * 0.10  # 승수 5만(미니) 또는 25만(일반), 위탁증거금률 10%
                    safe_budget = self.futures_available_balance * self.futures_margin_cap_ratio
                    qty = int(safe_budget // margin_per_contract)
                    
                    # 최소 1계약 보장
                    if qty == 0 and self.futures_available_balance >= (margin_per_contract * 1.2):
                        qty = 1
                        print("  => [선물 안전 마진 예외] 실잔고로 최소 1계약 진입 보장")
                        
                    # 모의투자 환경 긴급 우회: 예수금이 부족하더라도(혹은 0원이더라도) 테스트 작동성 검증을 위해 최소 1계약 강제 보장
                    if self.environment != "live" and qty <= 0:
                        qty = 1
                        print("  => [모의투자 긴급 우회] 모의 예수금 부족 상황이나 테스트 작동 검증을 위해 최소 1계약 강제 보장")
                    
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

                    ord_tp = "" if self.environment == "live" else "3"
                    print(f"  => [선물 실계좌 전송] SendOrderFO 전송 (trade_dir:{trade_dir}, 수량:{qty}, 코드:{order_code})")
                    res = self.kiwoom.dynamicCall(
                        "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
                        ["FuturesOrder", "0101", self.futures_account, order_code, trade_dir, "03", ord_tp, qty, "0", ""]
                    )
                    if res == 0:
                        cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))
                    else:
                        cursor.execute("UPDATE signals SET status = 'FAILED' WHERE id = ?", (signal_id,))
            conn.commit()
        except Exception as e:
            print(f"[ERA 선물 폴링 에러] {e}")
        finally:
            conn.close()

    def _on_receive_chejan_data(self, gubun, item_cnt, fid_list):
        try:
            self._handle_chejan_data(gubun, item_cnt, fid_list)
        except Exception as e:
            import traceback
            print(f"[ERA 체잔 콜백 오류] {e}\n{traceback.format_exc()}")

    def _handle_chejan_data(self, gubun, item_cnt, fid_list):
        if gubun == "0":
            status = self.kiwoom.dynamicCall("GetChejanData(int)", 913).strip()
            name = self.kiwoom.dynamicCall("GetChejanData(int)", 302).strip()
            code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().replace("A", "")
            
            if status == "체결":
                order_no = self.kiwoom.dynamicCall("GetChejanData(int)", 9203).strip()
                raw_price = float(self.kiwoom.dynamicCall("GetChejanData(int)", 910).strip())
                raw_qty = int(self.kiwoom.dynamicCall("GetChejanData(int)", 911).strip())
                order_gubun = self.kiwoom.dynamicCall("GetChejanData(int)", 905).strip()

                # mock 모드에서는 체결량/체결가가 누적으로 들어오는 경향이 있으므로 주문번호별로 delta 처리
                if getattr(self, "environment", "mock") == "mock":
                    if not hasattr(self, "_mock_order_fills"):
                        self._mock_order_fills = {}
                    
                    prev_qty, prev_price = self._mock_order_fills.get(order_no, (0, 0.0))
                    delta_qty = raw_qty - prev_qty
                    if delta_qty <= 0:
                        return  # 이미 처리되었거나 변동이 없는 누적 이벤트 무시
                    
                    total_cost_now = raw_price * raw_qty
                    total_cost_prev = prev_price * prev_qty
                    delta_cost = total_cost_now - total_cost_prev
                    delta_price = delta_cost / delta_qty
                    
                    exec_qty = delta_qty
                    exec_price = max(0.0, delta_price)
                    self._mock_order_fills[order_no] = (raw_qty, raw_price)
                else:
                    exec_qty = raw_qty
                    exec_price = raw_price

                # 개별주식선물(ISF) 체결 처리
                if code in self.isf_code_map:
                    sc = self.isf_code_map[code]
                    self.isf_order_locked[sc] = False  # 체결되었으므로 주문 잠금 해제
                    isf_cfg = next((c for c in self.isf_configs if c["stock_code"] == sc), None)
                    if isf_cfg:
                        if "매수" in order_gubun or "환매" in order_gubun:
                            if sc not in self.isf_positions:
                                self.isf_positions[sc] = {"type": "LONG", "qty": exec_qty, "price": exec_price, "futures_code": code}
                                self.isf_entry_price[sc] = exec_price
                                self.isf_peak_price[sc] = exec_price
                            else:
                                if self.isf_positions[sc]["type"] == "SHORT":
                                    self.isf_positions[sc]["qty"] -= exec_qty
                                    if self.isf_positions[sc]["qty"] <= 0:
                                        del self.isf_positions[sc]
                                        self.isf_peak_price[sc] = 0.0
                                else:
                                    self.isf_positions[sc]["qty"] += exec_qty
                            if notifier:
                                notifier.send_message(f"💰 <b>[ISF 매수체결] {isf_cfg['name']}</b>\n• {exec_price:,}원 | {exec_qty}계약")
                        elif "매도" in order_gubun or "전매" in order_gubun:
                            if sc not in self.isf_positions:
                                self.isf_positions[sc] = {"type": "SHORT", "qty": exec_qty, "price": exec_price, "futures_code": code}
                                self.isf_entry_price[sc] = exec_price
                                self.isf_peak_price[sc] = exec_price
                            else:
                                if self.isf_positions[sc]["type"] == "LONG":
                                    self.isf_positions[sc]["qty"] -= exec_qty
                                    if self.isf_positions[sc]["qty"] <= 0:
                                        del self.isf_positions[sc]
                                        self.isf_peak_price[sc] = 0.0
                                else:
                                    self.isf_positions[sc]["qty"] += exec_qty
                            if notifier:
                                notifier.send_message(f"📉 <b>[ISF 매도체결] {isf_cfg['name']}</b>\n• {exec_price:,}원 | {exec_qty}계약")
                        self.export_status()
                    return

                # 선물 체결 감지 (코드 길이 또는 "KOSPI" 이름 감지)
                if len(code) > 6 or "KOSPI" in name or "선물" in name:
                    _rd = getattr(self, 'real_day_code', '10100000')
                    _rn = getattr(self, 'real_night_code', '10500000')
                    if _rd == _rn:
                        _h = datetime.now().hour
                        is_night_fill = (_h >= 18) or (_h < 5)
                    else:
                        is_night_fill = (code == _rn)
                    # 체결되었으므로 주문 잠금 해제
                    if is_night_fill:
                        self.futures_night_order_locked = False
                    else:
                        self.futures_order_locked = False

                    pos_key = "KOSPI200_NIGHT" if is_night_fill else "KOSPI200"
                    session_label = "야간" if is_night_fill else "주간"
                    print(f"[{session_label}선물 실체결 확정] {name}({code}) | {exec_price} | {exec_qty}계약 | {order_gubun}")
                    if "매수" in order_gubun or "환매" in order_gubun:
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

                    elif "매도" in order_gubun or "전매" in order_gubun:
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
                            'super_trend_mode': False, 'ma_10': 0, 'ma_20': 0,
                            'entry_date': datetime.now().strftime('%Y-%m-%d'),
                            'half_sold': False
                        }
                    else:
                        # 부분체결 평균단가 재계산
                        pos = self.portfolio[code]
                        prev_qty = pos['qty']
                        if prev_qty > 0:
                            pos['buy_price'] = (pos['buy_price'] * prev_qty + exec_price * exec_qty) / (prev_qty + exec_qty)

                    self.portfolio[code]['qty'] += exec_qty
                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    self.persist_positions()
                    self.export_status()

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
                            loss_amt = abs(profit)
                            self.stock_daily_loss += loss_amt
                            self.stock_monthly_loss += loss_amt
                            icon = "✂️"
                            # 월간 MDD 25% 초과 시 Kill Switch 자동 발동
                            if self.stock_monthly_initial > 0 and not self.system_halted:
                                monthly_loss_ratio = self.stock_monthly_loss / self.stock_monthly_initial
                                if monthly_loss_ratio >= 0.25:
                                    self.system_halted = True
                                    print(f"[ERA Kill Switch] 월간 MDD {monthly_loss_ratio:.1%} 초과 — 자동 매매 중단!")
                                    if notifier:
                                        notifier.send_message(
                                            f"🚨 <b>[월간 MDD 자동 중단]</b>\n"
                                            f"월간 손실: {monthly_loss_ratio:.1%} (한도 25%)\n"
                                            f"신규 진입이 중단됩니다. 수동 검토 후 <code>!시스템시작</code>으로 재개하세요."
                                        )
                        else:
                            icon = "🚀"
                            
                        if notifier:
                            strat_name = "단타(가상)" if strat == 'DAY' else "스윙(가상)"
                            notifier.send_message(f"{icon} <b>[{strat_name} 매도 완료] {name}</b>\n• 체결가: {exec_price:,.0f}원\n• 손익률: {profit_pct:+.2f}%\n• 실현손익: {profit:+,}원\n🔄 가용 실예수금: {self.stock_total_balance:,}원")
                            
                        if pos['qty'] <= 0:
                            del self.portfolio[code]
                            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "0102", code)
                            self.persist_positions()
                        self.export_status()

    def _on_receive_real_data(self, code, real_type, real_data):
        # 개별주식선물(ISF) 실시간 틱 처리
        if code in self.isf_code_map:
            raw = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10).strip()
            if raw:
                try:
                    price = abs(float(raw))
                    self._process_isf_tick(code, price)
                    self._update_isf_ohlcv(code, price)
                except ValueError:
                    pass
            return

        # 선물 실시간 틱 처리 (futures/both만)
        if real_type == "선물시세" or real_type == "선물체결":
            if self.trading_mode not in ('futures', 'both'):
                return
            raw = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10).strip()
            if raw:
                try:
                    price = abs(float(raw))
                    self._process_futures_tick(code, price)
                    self._update_futures_ohlcv(code, price)  # 야간 포함 선물 틱 → 5분봉 DB 축적
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
                    # 장대양봉 시가 이탈 시 즉시 기계적 손절 (하드 스탑) — 진입 당일에만 적용
                    _today = datetime.now().strftime('%Y-%m-%d')
                    _entry_date = pos.get('entry_date', _today)
                    if _entry_date == _today and pos['open_price'] and current_price < pos['open_price']:
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
                self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            else:
                # 휴장일에는 텔레그램 알림 생략
                if not self._is_trading_day():
                    return
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
                kill_price = pos.get('current_price', pos.get('price', 0))
                self._execute_futures_direct(
                    "LONG_EXIT" if pos['type'] == 'LONG' else "SHORT_EXIT",
                    kill_price, order_code, pos_key
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

    import socket
    # 물리적 소켓 바인딩 락 (Port: 9991) - Singleton 보장
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
