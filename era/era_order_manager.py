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
    MAX_LOG_BYTES = 20 * 1024 * 1024  # 로그 파일 1개당 20MB 제한 (무인 장기운영 시 디스크 무한증가 방지)
    BACKUP_COUNT = 3                  # .1~.3까지 회전 보관, 그 이상은 삭제

    def __init__(self, original_stream, log_file_path=None):
        self.original_stream = original_stream
        self.log_file_path = log_file_path

    def _rotate_if_needed(self):
        try:
            if os.path.exists(self.log_file_path) and os.path.getsize(self.log_file_path) >= self.MAX_LOG_BYTES:
                for i in range(self.BACKUP_COUNT - 1, 0, -1):
                    src = f"{self.log_file_path}.{i}"
                    dst = f"{self.log_file_path}.{i + 1}"
                    if os.path.exists(src):
                        if os.path.exists(dst):
                            os.remove(dst)
                        os.rename(src, dst)
                os.rename(self.log_file_path, f"{self.log_file_path}.1")
        except Exception:
            pass  # 회전 실패해도 로깅 자체는 계속 진행되어야 함

    def write(self, data):
        if not data:
            return
        # 1. 원래 스트림(콘솔) 출력 처리
        try:
            encoding = getattr(self.original_stream, 'encoding', 'cp949') or 'cp949'
            try:
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
        except Exception:
            pass  # 콘솔 핸들 유실(OSError 등) 전체 예외 원천 방어

        # 2. 파일 실시간 백업 로깅 (크기 초과 시 회전)
        if self.log_file_path:
            try:
                self._rotate_if_needed()
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
            # 임시파일 작성 후 os.replace로 원자적 치환 — TCA(!계약수량/!ISF코드)도 동일 파일을
            # read-modify-write하므로, 직접 write하면 겹치는 타이밍에 서로의 변경사항이
            # 조용히 유실될 수 있었음
            tmp_path = f"{self.config_local_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, self.config_local_path)
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
                    # 페이지 노출 순서를 그대로 믿지 않고 등락률 기준으로 명시적 재정렬 후 상위 5개 채택
                    candidates = []
                    for row in rows:
                        a = row.select_one("td.name a")
                        if not a:
                            continue
                        sname = a.text.strip()
                        scode = a["href"].split("code=")[1]
                        if any(kw in sname for kw in _EXCLUDE) or scode in seen_codes:
                            continue
                        tds = row.select("td")
                        change_val = 0.0
                        if len(tds) >= 5:
                            try:
                                ct = tds[4].get_text(strip=True)
                                change_val = float(ct.replace("%", "").replace("+", "").strip())
                                if "-" in ct:
                                    change_val = -abs(change_val)
                            except Exception:
                                change_val = 0.0
                        candidates.append({"code": scode, "name": sname, "change_val": change_val})
                    candidates.sort(key=lambda c: c["change_val"], reverse=True)
                    for cand in candidates[:5]:
                        leaders.append({"code": cand["code"], "name": cand["name"], "theme": theme["name"]})
                        seen_codes.add(cand["code"])
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
        self.stock_account_day = ""
        self.stock_account_swing = ""
        self.futures_account = ""
        self.portfolio_day = {}       # 주식 단타 보유 정보 (코드 -> 정보)
        self.portfolio_swing = {}     # 주식 스윙 보유 정보 (코드 -> 정보)
        self.portfolio = {}           # 주식 통합 보유 정보 (레거시/조회용 래퍼 및 호환)
        self.futures_positions = {}   # 선물 보유 정보 (코드 -> 정보)
        self.pending_orders = {}      # 주식 미체결/진입 대기
        self.pending_futures_orders = {} # 선물 미체결/진입 대기
        self.system_halted = False
        self.current_regime = "RANGE"    # [AMATS 최적화] 실시간 감지 레짐 기본값
        self.morning_worker = None
        
        # 자금 정보
        self.stock_total_balance = 0
        self.stock_total_balance_day = 0
        self.stock_total_balance_swing = 0
        self.stock_initial_balance = 0
        self.stock_daily_loss = 0
        self.stock_daily_loss_limit_pct = 0.08  # 일일 손실 서킷브레이커: 월초 기준잔고 대비 초과 시 당일 신규진입 중단
        self.stock_daily_halted = False
        self.is_physical_separated = False
        
        self.futures_available_balance = 0
        self.futures_margin_cap_ratio = 0.30  # [AMATS 최적화] KOSPI200 선물 30% 격리 캡
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
        self.load_futures_exit_state()
        
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

        # 선물 계좌 TR(예수금/잔고조회) 무응답 워치독용 타임스탬프 — check_connection_status에서 60초 주기로 점검
        self.futures_sync_requested_at = None
        self.futures_sync_responded_at = None
        self._futures_tr_timeout_alerted = False

        # 5. 매일 시스템 상태 일일 리셋 타이머 — 항상
        self.reset_timer = QTimer()
        self.reset_timer.timeout.connect(self._check_daily_reset)
        self.reset_timer.start(60000)

        # 5-1. 선물 ATR(변동성) 15분 주기 실시간 재계산 — 08:40 1회성 계산만으로는 장중 변동성 급변(예: 급락/급등 후 진정)을 못 따라가는 문제 보완
        self.futures_atr_timer = QTimer()
        self.futures_atr_timer.timeout.connect(self._check_periodic_atr_refresh)
        if self.trading_mode in ('futures', 'both'):
            self.futures_atr_timer.start(900000)  # 15분

        # 스윙(종가베팅) 최대 보유일수 — 국내 시장 모멘텀 연구에 따르면 보유기간이 길어질수록
        # 반전(reversal) 위험이 커지므로, 5MA/10MA 추세 이탈 신호가 안 떠도 일정 기간 후 강제 청산
        self.swing_max_holding_days = 15

        self.pending_5ma_checks = []
        self.today_5ma_checked = False
        self._daily_reset_done_date = ""   # 09:00 일일 리셋 중복 실행 방지
        self._night_reset_done_date = ""   # 05:00 야간 리셋 중복 실행 방지
        self._night_start_done_date = ""   # 18:00 야간 세션 시작 중복 실행 방지
        self.futures_day_consecutive_losses = 0
        self.futures_night_consecutive_losses = 0
        self.futures_consecutive_loss_limit = 5  # 이 횟수만큼 연속 손실 시 당일 신규진입 정지 (2026-07-07: 3회는 시뮬레이션상 초반 손실 클러스터에 과민 반응해 순손익 악화 확인, 5회로 완화)
        self.futures_day_trade_count = 0
        self.futures_night_trade_count = 0
        self.futures_max_trades_day = 4
        self.futures_max_trades_night = 4
        # [서킷브레이커 최종 안전장치] 전략 종류(무제한 여부)·승패와 무관하게 일일 총 거래횟수가
        # 이 값을 넘으면 신규 진입을 전면 차단 — 목표가 재추정 오류 등으로 진입-청산이 반복되는
        # 오작동이 발생해도(승리로 기록되어 연속손실 카운터가 못 잡는 경우 포함) 반드시 멈추게 함
        # (2026-07-01 실측: 무한루프 사고로 하루 7천여 건 체결 발생 후 도입)
        self.futures_day_max_trades_hard_cap = 50
        self.futures_night_max_trades_hard_cap = 50

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

        # 13. 선물 실계좌 잔고/포지션 동기화 타이머 (5분 주기) — futures/both만
        self.futures_sync_timer = QTimer()
        self.futures_sync_timer.timeout.connect(self.sync_futures_positions_and_balance)
        if self.trading_mode in ('futures', 'both'):
            self.futures_sync_timer.start(300000)  # 5분

        # (2026-08-04 #4) 틱과 무관한 주기적 청산 감시 타이머 — 틱이 끊기는 구간에서도
        # 보유 포지션의 샹들리에 스탑/강제청산이 계속 작동하도록. (2026-08-04 #1) 마감창
        # 고속 동기화 타이머 — 15:30~15:50 보유 포지션이 있으면 계좌동기화를 자주 돌려
        # 청산 체결 여부를 5분이 아니라 십수 초 내에 확인한다.
        self.futures_exit_monitor_timer = QTimer()
        self.futures_exit_monitor_timer.timeout.connect(self._futures_exit_monitor_tick)
        self.futures_eod_fast_sync_timer = QTimer()
        self.futures_eod_fast_sync_timer.timeout.connect(self._futures_eod_fast_sync_tick)
        if self.trading_mode in ('futures', 'both'):
            self.futures_exit_monitor_timer.start(5000)    # 5초
            self.futures_eod_fast_sync_timer.start(15000)  # 15초

        # ── 선물 실시간 K값 변동성 돌파 전략 ─────────────────────────────
        self.futures_strategy_active = False
        self.futures_best_k = 0.5
        self.futures_reentry_k = 0.25           # 재진입 휩소방지 폭 계수 (2026-07-07: futures_best_k는 BQA가 매주 자동 갱신하는 구버전 변동성돌파 전략 전용값이라 칼만 재진입필터와 무관하게 분리)
        self.futures_prev_range = 20.0
        
        # ── 선물 과거 5분봉 자동 동기화 상태 변수 ───────────────────────
        self.futures_sync_queue = []
        self.futures_sync_index = 0
        self.futures_sync_current_page = 0
        self.futures_sync_max_pages = 10     # 수집할 과거 데이터 페이지 수 (1페이지당 약 80~100개 캔들)
        self.futures_sync_active = False

        # 선물 동기화 타임아웃 타이머 추가
        self.futures_sync_timeout_timer = QTimer()
        self.futures_sync_timeout_timer.setSingleShot(True)
        self.futures_sync_timeout_timer.timeout.connect(self._on_futures_sync_timeout)

        # 선물 손절/익절 설정 (고정 pt)
        self.futures_stop_loss_pt = 2.0   # 주간선물 손절 (update_futures_dynamic_sl_tp가 덮어씀)
        self.futures_take_profit_pt = 5.0  # 주간선물 익절
        self.futures_atr_14 = 2.0
        self.futures_atr_14_updated_at = None
        # 야간선물 전용 고정 손절/익절 — ATR 동적 함수에 의해 절대 변경되지 않음
        self.futures_night_stop_loss_pt = 3.0
        self.futures_night_take_profit_pt = 6.0

        # 주간 선물 (09:00 ~ 익일 08:45)
        self.futures_day_open     = 0.0
        self._day_strategy_activated_at = None  # 09:00 개장 순차 실행 기준 시각(단타스캔/ISF체크 지연용)
        self.futures_target_long  = float('inf')
        self.futures_target_short = float('-inf')
        self.futures_tp_price_long = 0.0        # Overshoot 3-Sigma 주간 LONG 익절 타겟
        self.futures_tp_price_short = 0.0       # Overshoot 3-Sigma 주간 SHORT 익절 타겟
        self.futures_order_locked = False
        self.futures_day_entry_price = 0.0  # 주간 진입가 기록
        self.futures_day_peak = 0.0         # [대안 C] 주간 트레일링 스탑용 최고/최저가 추적
        # 진입 시점 스냅샷 — 보유 중 손절/익절 기준이 라이브 재추정값 변동으로 흔들리지 않도록 고정
        # 0.0은 "아직 진입 스냅샷이 찍힌 적 없음"을 뜻하는 센티널 값 — 0.5/5.0 같은 실제값을 기본값으로
        # 두면 진짜 0이 아니라서 폴백(getattr(...) or ...)이 항상 죽어버리는 버그가 있었음 (2026-07-01)
        self.futures_day_entry_std_error = 0.0
        self.futures_day_entry_atr = 0.0
        self.futures_day_entry_tp_price = 0.0
        self.futures_last_long_exit_price = 0.0  # 재진입 방지용 최종 청산가
        self.futures_last_long_exit_time = 0.0   # 재진입 쿨다운용 최종 청산 시각(epoch)
        self.futures_last_short_exit_price = 0.0
        self.futures_last_short_exit_time = 0.0
        self.futures_last_any_exit_time = 0.0    # (2026-08-04) 방향무관 재진입 쿨다운용 최종 청산 시각(epoch)
        self.futures_day_entry_time = 0.0        # (2026-08-07) 타임스톱용 진입 시각(epoch)
        self.futures_std_error = 0.5            # Kalman Filter 최근 주간 잔차 표준편차
        self.futures_kf_sl_mult = 5.0           # Kalman Filter 하이브리드 손절 배수 (최적값 5.0)
        self.futures_kf_ts_trigger_mult = 1.5   # 이익보전 활성화 배수 (기본값 1.5)
        self.futures_kf_ts_callback_mult = 0.5  # 이익보전 청산 폭 배수 (기본값 0.5)
        self.futures_kf_ts_floor = 0.3          # 이익보전 최소 한계선 (기본값 0.3pt)
        self.futures_kf_tp_sigma_mult = 3.0     # 3-Sigma 익절 배수 (백테스트 검증 결과 5.0 권장 — MDD 불변으로 PF 개선, 2026-07-01)
        self.futures_trend_tp_sigma_mult = None  # 추세 확인 구간 전용 3-Sigma 배수(None=비활성, 2026-07-11 백테스트로 10.0 채택)
        self.futures_session_range_mult = 1.2   # 세션마감 강제청산 ATR 배수 (백테스트 검증 결과 1.0 권장, 2026-07-01)
        self.futures_sl_hard_cap_pt = 15.0       # 손절폭 절대 상한 (2026-07-07: 실거래 데이터상 sl_floor=1.5*std_error가 이 상한에 거의 항상 걸려 손절이 사실상 고정 15pt로 굳어져 순손익이 악화됨을 확인, 10.0으로 하향)
        self.futures_min_std_error_entry = 0.0   # 저변동성 진입필터: std_error가 이 값보다 작으면 신규진입 차단 (기본 0.0=비활성)
        self.futures_std_trim_outliers = 0       # std_error 계산 시 절댓값 최대 잔차 N개 제외 (기본 0=비활성, 2026-07-11 백테스트로 1 채택)
        self.futures_dynamic_cap_mult = None     # 동적 SL캡 배수 (None이면 futures_sl_hard_cap_pt 고정값 사용)
        self.futures_dynamic_cap_min = None      # 동적 SL캡 하한
        self.futures_dynamic_cap_max = None      # 동적 SL캡 상한
        # (2026-07-09: 1.5개년 29,881봉 백테스트로 저변동성 국면에서 3*std_error 익절목표가 절대
        #  손절플로어보다 작아 손익비가 원천적으로 불리함을 확인, in/out-of-sample 교차검증 후 도입)
        self.futures_tick_max_jump_pct = 0.03   # 5분봉 OHLCV 이상치 필터 — 봉 시가 대비 이 비율 넘게 괴리된 틱은 무시 (2026-06-23 저가 1300pt 오류 틱이 ATR을 10배 이상 왜곡시킨 사고 재발 방지)
        self.futures_trend_direction = "NEUTRAL" # 15분봉 장기 칼만 추세 필터
        self.real_day_code = ""
        self.real_night_code = ""
        self.futures_day_session_high = 0.0     # 금일 주간 세션 고가
        self.futures_day_session_low = 0.0      # 금일 주간 세션 저가

        # Parabolic SAR 전략 상태변수
        self.sar_value = 0.0          # 현재 SAR 값
        self.sar_ep = 0.0             # Extreme Point (진입 후 최고가/최저가)
        self.sar_af = 0.02            # 현재 가속인수 (Acceleration Factor)
        self.sar_bull = True          # True=상승장(SAR이 아래), False=하락장(SAR이 위)
        self.sar_af_init = 0.02       # AF 초기값 (config에서 덮어쓰기 가능)
        self.sar_af_step = 0.02       # AF 증가폭
        self.sar_af_max = 0.20        # AF 최대값

        # BB + PSAR 결합 필터 실시간 변수
        self.current_bb_mid = 0.0
        self.current_bb_bandwidth = 0.0
        self.current_bb_squeeze_limit = 0.0

        # 볼린저 밴드 역추세 전략 상태변수
        self.bb_close_buf = []        # 실시간 5분봉 종가 롤링 버퍼
        self.bb_window    = 20        # 볼린저 밴드 창 크기 (config에서 덮어쓰기 가능)
        self.bb_sigma     = 2.0       # 볼린저 밴드 표준편차 배수 (config에서 덮어쓰기 가능)
        self.bb_upper     = 0.0       # 현재 시점 상단 밴드 (LONG 익절 목표)
        self.bb_lower     = 0.0       # 현재 시점 하단 밴드 (SHORT 익절 목표)

        # 야간 선물 (18:00 ~ 익일 04:45)
        self.futures_night_open         = 0.0
        self.futures_night_target_long  = float('inf')
        self.futures_night_target_short = float('-inf')
        self.futures_night_tp_price_long = 0.0   # Overshoot 3-Sigma 야간 LONG 익절 타겟
        self.futures_night_tp_price_short = 0.0  # Overshoot 3-Sigma 야간 SHORT 익절 타겟
        self.futures_night_order_locked = False
        self.futures_night_entry_price = 0.0  # 야간 진입가 기록
        self.futures_night_peak = 0.0         # 야간 트레일링 스탑용 최고/최저가 추적
        # 진입 시점 스냅샷 — 보유 중 손절/익절 기준이 라이브 재추정값 변동으로 흔들리지 않도록 고정
        # 0.0은 "아직 진입 스냅샷이 찍힌 적 없음"을 뜻하는 센티널 값 (주간과 동일한 이유, 2026-07-01)
        self.futures_night_entry_std_error = 0.0
        self.futures_night_entry_atr = 0.0
        self.futures_night_entry_tp_price = 0.0
        self.futures_night_last_long_exit_price = 0.0  # 야간 재진입 방지용 최종 청산가
        self.futures_night_last_long_exit_time = 0.0
        self.futures_night_last_short_exit_price = 0.0
        self.futures_night_last_short_exit_time = 0.0
        self.futures_night_std_error = 0.5      # Kalman Filter 최근 야간 잔차 표준편차
        self.futures_night_trend_direction = "NEUTRAL"

        # ── STA 통합: 테마 크롤링 + 실시간 OHLCV ─────────────────────────
        self.theme_stocks = {}        # {code: name} 오늘 실시간 구독 종목
        self.ohlcv_buffer = {}        # {code: {period_str: {o,h,l,c,v}}}
        self._tick_reject = {}        # {code: [최근 연속 거부된 틱 가격]} — 지속이동 락아웃 복구용(2026-07-31)
        self._tick_feed_alerted = {}  # {code: bool} 피드 동결 텔레그램 경고 중복 방지
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

        # load_config()를 다시 호출 — 위쪽(이 메서드 앞부분)의 최초 호출 이후 futures_kf_sl_mult/
        # kf_ts_trigger_mult/kf_ts_callback_mult/futures_max_trades_day 등 여러 속성이 __init__ 안에서
        # 다시 하드코딩 기본값으로 초기화(원복)되어, config.json/config_local.json 튜닝값이 재시작
        # 때마다 무시되고 매일 아침 장전준비(_on_morning_prep_finished)가 재적용할 때까지 방치되는
        # 문제가 있었음 (2026-07-01 발견: kf_sl_mult가 config상 2.0인데 재시작 직후 5.0으로 찍힘).
        # load_config()는 순수 대입으로만 구성돼 재호출 부작용이 없고, 최종값이 항상 config 기준으로 확정됨.
        self.load_config()

        # load_futures_exit_state()도 동일한 이유로 재호출 — 이 메서드는 459행에서 이미 한 번
        # 호출됐지만, 그 이후(515-522행 카운터/리셋플래그, 600-601/658-659행 청산가) __init__ 안의
        # 하드코딩 기본값 대입들이 방금 복원한 값을 전부 무조건 재덮어써서, 재연결/재시작마다
        # 연속손절 카운터·재진입방지 청산가·일일리셋 완료 플래그가 매번 초기화되는 버그가 있었음
        # (2026-07-09 최종점검에서 발견 — load_config()가 2026-07-01에 겪은 것과 동일한 유형의
        # __init__ 실행순서 문제). load_futures_exit_state()도 순수 대입 + 파일 재읽기뿐이라
        # 재호출 부작용이 없고, 여기서 마지막에 실행되어야 위 하드코딩 기본값들을 확실히 덮어쓴다.
        self.load_futures_exit_state()

    def get_swing_exit_ma_period(self):
        """현재 감지된 시장 레짐(UP/RANGE/DOWN)에 따라 스윙 청산 이평선 기간 자동 결정"""
        regime = getattr(self, 'current_regime', 'RANGE')
        if regime == "UP":
            return 10  # 강세장 -> 휩소 방지 및 이익 극대화를 위해 10일선(10MA) 지지력 추종
        else:
            return 5   # 횡보/약세장 -> 칼청산으로 단기 이익 실현 및 자산 격리를 위해 5일선(5MA) 추종

    def _clean_futures_ohlcv_outliers(self, df):
        """5분봉 데이터에서 이웃 봉들로 확인되지 않는 단발성 고/저가 이상치를 시가/종가 범위로 정정.
        Kiwoom 서버가 내려주는 과거 데이터 자체에 이런 이상치가 포함된 사례를 확인했음(2026-06-23,
        저가가 순간적으로 실제보다 ~65pt 낮게 찍혀 ATR을 10배 이상 왜곡) — 로컬 DB만 고쳐도 재시작 시
        서버 재동기화로 원복되므로, ATR 계산 직전 원본 데이터를 매번 재검증한다.
        date 오름차순 정렬된 open/high/low/close DataFrame을 받아 동일 구조로 반환한다."""
        n = len(df)
        if n < 7:
            return df
        opens = df['open'].to_numpy(dtype=float)
        highs = df['high'].to_numpy(dtype=float)
        lows = df['low'].to_numpy(dtype=float)
        closes = df['close'].to_numpy(dtype=float)
        fixed_high = highs.copy()
        fixed_low = lows.copy()
        fix_count = 0
        window = 3          # 앞뒤 3봉(±15분) 이내에서 확인
        wick_pct = 0.02      # 시가/종가 몸통 대비 2% 넘는 꼬리만 후보
        confirm_pct = 0.01   # 이웃 봉의 고/저가가 1% 이내로 근접하면 "실제 있었던 값"으로 인정
        for i in range(n):
            ref = opens[i] if opens[i] > 0 else closes[i]
            if ref <= 0:
                continue
            body_hi = max(opens[i], closes[i])
            body_lo = min(opens[i], closes[i])
            lo_i, hi_i = max(0, i - window), min(n, i + window + 1)
            neighbors = None

            wick_lo = body_lo - lows[i]
            if wick_lo > 0 and wick_lo / ref > wick_pct:
                neighbors = [v for j in range(lo_i, hi_i) if j != i for v in (lows[j], highs[j])]
                if neighbors and not any(abs(lows[i] - nv) / ref < confirm_pct for nv in neighbors):
                    fixed_low[i] = body_lo
                    fix_count += 1

            wick_hi = highs[i] - body_hi
            if wick_hi > 0 and wick_hi / ref > wick_pct:
                if neighbors is None:
                    neighbors = [v for j in range(lo_i, hi_i) if j != i for v in (lows[j], highs[j])]
                if neighbors and not any(abs(highs[i] - nv) / ref < confirm_pct for nv in neighbors):
                    fixed_high[i] = body_hi
                    fix_count += 1

        if fix_count > 0:
            print(f"[AMATS ATR 이상치 필터] 이웃봉으로 확인되지 않는 단발성 고/저가 이상치 {fix_count}건을 시가/종가 범위로 정정했습니다.")
            df = df.copy()
            df['high'] = fixed_high
            df['low'] = fixed_low
        return df

    def update_futures_dynamic_sl_tp(self):
        """BQA 역사적 데이터를 조회하여 실시간 선물 변동성(ATR) 기반 동적 익손절 라인 산출"""
        try:
            import pandas as pd
            import numpy as np
            if not os.path.exists(self.futures_db_path):
                print("[AMATS 파생 최적화] futures_data.db가 존재하지 않아 ATR 갱신을 건너뜁니다.")
                return

            # real_day_code(실제 체결되는 전월물 코드)를 우선 사용 — 과거엔 generic 코드(10500000)로
            # 고정되어 있어서 실제 거래 코드와 데이터 조회 코드가 어긋나는 문제가 있었음
            atr_code = getattr(self, "real_day_code", "") or getattr(self, "futures_target_code_day", "10500000")

            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            # 최근 충분한 일봉(resample) 확보를 위해 3000개 행 조회 (과거 400개는 일부 코드에서
            # 일중 밀도가 낮아 일봉 15개 미만으로 환산되는 문제가 있었음)
            df = pd.read_sql(
                "SELECT date, open, high, low, close FROM futures_ohlcv WHERE code=? ORDER BY date DESC LIMIT 3000",
                conn, params=(atr_code,)
            )
            conn.close()

            if df.empty or len(df) < 50:
                print(f"[AMATS 파생 최적화] {atr_code} 5분봉 데이터가 부족(행:{len(df)})하여 ATR 갱신을 건너뜁니다. (기존값 {self.futures_atr_14:.2f}pt 유지)")
                return

            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
            df.dropna(subset=['date'], inplace=True)
            df.sort_values('date', inplace=True)
            df.reset_index(drop=True, inplace=True)
            df = self._clean_futures_ohlcv_outliers(df)
            df.set_index('date', inplace=True)
            daily = df.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()

            # 오늘 날짜의 미완성 바가 있다면 제외하여 ATR 훼손 방지
            today_date = datetime.now().date()
            if not daily.empty and daily.index[-1].date() == today_date:
                daily = daily.iloc[:-1]

            if len(daily) < 15:
                print(f"[AMATS 파생 최적화] {atr_code} 일봉 환산 결과 {len(daily)}일뿐이라 ATR(15일 이상 필요) 갱신을 건너뜁니다. (기존값 {self.futures_atr_14:.2f}pt 유지)")
                return

            # TR 계산 (첫 행의 NaN 값을 high - low로 채워 Kalman Filter 전파 차단)
            daily['tr'] = np.maximum(daily['high'] - daily['low'],
                                     np.maximum(abs(daily['high'] - daily['close'].shift(1)),
                                                abs(daily['low'] - daily['close'].shift(1)))).fillna(daily['high'] - daily['low'])
            # 1차원 칼만 필터를 적용하여 지연 없는 변동성(Kalman ATR) 산출
            kf_atr = None
            P_atr = 1.0
            Q_atr = 0.002
            R_atr = 0.2
            for tr_val in daily['tr'].values:
                if kf_atr is None:
                    kf_atr = tr_val
                else:
                    P_atr = P_atr + Q_atr
                    K_atr = P_atr / (P_atr + R_atr)
                    kf_atr = kf_atr + K_atr * (tr_val - kf_atr)
                    P_atr = (1 - K_atr) * P_atr

            atr_val = kf_atr
            if pd.isna(atr_val) or atr_val <= 0:
                print(f"[AMATS 파생 최적화] {atr_code} ATR 계산값이 유효하지 않아(NaN/0) 갱신을 건너뜁니다. (기존값 {self.futures_atr_14:.2f}pt 유지)")
                return

            # 동적 SL / TP 연산 (손절 = 1.0 * ATR, 익절 = 2.0 * ATR)
            self.futures_stop_loss_pt = max(round(atr_val * 1.0, 2), 2.0)
            self.futures_take_profit_pt = max(round(atr_val * 2.0, 2), 4.0)
            self.futures_atr_14 = float(atr_val)
            self.futures_atr_14_updated_at = datetime.now()

            # Parabolic SAR 전략인 경우 볼린저 밴드 필터 실시간 변수 초기화
            if getattr(self, "futures_strategy_type", "") == "parabolic_sar":
                target_code = getattr(self, "real_day_code", "10100000")
                self.update_bb_psar_filters(target_code)

            print(f"[AMATS 파생 최적화] 선물({atr_code}) 동적 Kalman ATR 적용 완료: Kalman ATR={atr_val:.2f}pt ➡️ 손절={self.futures_stop_loss_pt}pt | 익절={self.futures_take_profit_pt}pt")
        except Exception as dynamic_err:
            print(f"[AMATS 파생 최적화] 동적 익손절 계산 에러 (기존값 {self.futures_atr_14:.2f}pt 유지): {dynamic_err}")

    def _get_today_futures_high_low(self, code):
        """오늘 주간 5분봉 중 최고가와 최저가를 DB에서 조회"""
        try:
            today_str = datetime.now().strftime("%Y%m%d")
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            for query_code in [code, self.futures_prefix + "00000", "10500000", "10100000"]:
                cursor.execute(
                    "SELECT MAX(high), MIN(low) FROM futures_ohlcv WHERE code = ? AND date LIKE ?",
                    (query_code, today_str + "%")
                )
                row = cursor.fetchone()
                if row and row[0] is not None and row[1] is not None:
                    conn.close()
                    return float(row[0]), float(row[1])
            conn.close()
        except Exception as e:
            print(f"[주간선물] 금일 고/저가 DB 조회 실패: {e}")
        return 0.0, 0.0

    def update_bb_psar_filters(self, code):
        """실시간 선물 데이터에서 볼린저 밴드 중심선 및 Squeeze 필터 변수 업데이트"""
        try:
            import pandas as pd
            import numpy as np
            if not os.path.exists(self.futures_db_path):
                return
            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            # 볼린저 밴드 및 100봉 롤링 Squeeze 계산을 위해 충분한 5분봉 조회 (최근 250개)
            df = pd.read_sql(
                f"SELECT close FROM futures_ohlcv WHERE code='{code}' ORDER BY date DESC LIMIT 250", conn
            )
            conn.close()
            
            if df.empty or len(df) < 120:
                return
            
            df = df.iloc[::-1].reset_index(drop=True) # 과거에서 최근 순으로 정렬
            
            # BB(20, 2)
            df['sma20'] = df['close'].rolling(window=20).mean()
            df['std20'] = df['close'].rolling(window=20).std()
            df['bb_upper'] = df['sma20'] + 2 * df['std20']
            df['bb_lower'] = df['sma20'] - 2 * df['std20']
            df['bandwidth'] = (df['bb_upper'] - df['bb_lower']) / df['sma20']
            # Squeeze Limit (최근 100봉의 25% 분위수)
            df['squeeze_limit'] = df['bandwidth'].rolling(window=100).quantile(0.25)
            
            last_row = df.iloc[-1]
            if not pd.isna(last_row['sma20']):
                self.current_bb_mid = float(last_row['sma20'])
                self.current_bb_bandwidth = float(last_row['bandwidth'])
                self.current_bb_squeeze_limit = float(last_row['squeeze_limit'])
                print(f"[ERA BB 필터] 갱신 완료 | 중심선={self.current_bb_mid:.2f} | 밴드폭={self.current_bb_bandwidth*100:.2f}% (임계={self.current_bb_squeeze_limit*100:.2f}%)")
        except Exception as e:
            print(f"[ERA BB 필터 오류] {e}")

    def _note_entry_block(self, reason, detail):
        """진입 게이트가 조용히 반환할 때 그 사실을 기록하고, 하루 종일 막히면 알린다.

        (2026-08-10) 08-09 SAR 전환 후 std_error 게이트가 모든 진입을 막았는데
        `return`만 하고 로그가 없어, 목표가를 양방향으로 관통한 08-10 하루가
        거래 0건으로 끝나도록 아무도 알아채지 못했다. 오류도 경고도 없어서
        로그만 보면 "조용한 정상"과 구별되지 않았다.
        어떤 게이트든 연속으로 막고 있으면 그 이름과 수치를 드러낸다.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if getattr(self, "_entry_block_date", None) != today:
            self._entry_block_date = today
            self._entry_block_counts = {}
            self._entry_block_alerted = set()

        # 사유별로 따로 센다. 한 카운터를 공유하면 먼저 걸린 사유가 계속 증가시켜
        # 다른 사유는 임계값에 영원히 도달하지 못한다.
        counts = self._entry_block_counts
        counts[reason] = counts.get(reason, 0) + 1

        # 5분봉 기준 하루 78봉. 같은 사유로 60회면 사실상 당일 전 구간이 막힌 것이다.
        if counts[reason] < 60 or reason in self._entry_block_alerted:
            return
        self._entry_block_alerted.add(reason)

        msg = (f"신규 진입이 60회 연속 차단됐습니다.\n"
               f"게이트: {reason}\n조건: {detail}\n"
               f"전략={getattr(self, 'futures_strategy_type', '?')}")
        print(f"⚠️ [주간선물] {msg}")
        if notifier:
            notifier.send_message(f"⚠️ <b>[진입 전면 차단]</b>\n{msg}")

    def update_kalman_targets(self, code, std_error_only=False):
        """로컬 DB의 최근 5분봉 데이터를 활용해 칼만 필터 예측값 및 오차 표준편차를 구하고 돌파 타점을 설정하며, 15분봉 장기 추세 필터를 계산합니다.

        std_error_only=True면 std_error(및 추세)만 갱신하고 **타점은 건드리지 않는다**.
        parabolic_sar 전용 경로다 — SAR은 타점을 돌파 방식(시초가±전일Range×K, 4065행)
        으로 잡는데, std_error는 진입 게이트(4604행 min_std_error_entry)가 쓰기 때문에
        갱신은 되어야 한다. 종전에는 이 함수가 kalman/chandelier에서만 호출돼
        SAR에서는 futures_std_error가 초기값 0.5(621행)에 그대로 머물렀고, 문턱 1.5와
        비교되어 **모든 신규 진입이 조용히 반환**됐다(2026-08-10 실거래 0건).
        """
        try:
            import sqlite3
            import pandas as pd
            import numpy as np
            
            if not os.path.exists(self.futures_db_path):
                print(f"[ERA 칼만] DB 파일 없음: {self.futures_db_path}")
                return

            # 주간/야간 구분 — 원래는 이 아래 목표가 반영부에서만 판정했으나, 주간 타점
            # 계산 시에만 KIS 야간데이터를 병합하려면 조회 시점에 먼저 알아야 하므로 앞당김
            is_night_target = (code == getattr(self, 'real_night_code', '10500000').replace("A", ""))
            if getattr(self, 'real_day_code', '10100000').replace("A", "") == getattr(self, 'real_night_code', '10500000').replace("A", ""):
                _h = datetime.now().hour
                is_night_target = (_h >= 18) or (_h < 5)

            conn = sqlite3.connect(self.futures_db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")

            # 최근 300개 5분봉 조회 (15분봉 리샘플링을 위한 충분한 윈도우 확보)
            df = pd.read_sql(
                "SELECT date, close FROM futures_ohlcv WHERE code = ? ORDER BY date DESC LIMIT 300",
                conn,
                params=(code,)
            )

            # 주간 타점 계산 시, KIS(한국투자증권)로 별도 수집 중인 야간선물 데이터를 병합 —
            # 09:00 최초 계산 시점엔 당일 주간봉이 아직 없어 간밤의 가격 흐름이 전혀 반영되지
            # 못하던 문제를 완화한다 (2026-07-17 도입). 검증(3개 아침): 2개는 KF 기준선의
            # 실제 개장가 대비 오차가 크게 줄었고(한 건은 71.66pt→8.73pt로 8배 개선), 나머지
            # 1건은 근소하게 악화했는데 그날은 원래 갭 자체가 커서 기존 방식도 40pt대 오차였던
            # 케이스였음. KIS는 1분봉이라 Kiwoom과 동일한 5분 그리드로 리샘플링해서 합친다.
            kis_night_code = getattr(self, "kis_night_code", "")
            if not is_night_target and kis_night_code:
                try:
                    cutoff = (datetime.now() - timedelta(hours=36)).strftime("%Y%m%d%H%M%S")
                    night_raw = pd.read_sql(
                        "SELECT date, close FROM futures_ohlcv WHERE code = ? AND date > ? ORDER BY date ASC",
                        conn,
                        params=(kis_night_code, cutoff)
                    )
                    if not night_raw.empty:
                        night_raw['dt'] = pd.to_datetime(night_raw['date'], format='%Y%m%d%H%M%S', errors='coerce')
                        night_raw = night_raw.dropna(subset=['dt']).set_index('dt')
                        night_5m = night_raw['close'].resample('5min').last().dropna().reset_index()
                        night_5m['date'] = night_5m['dt'].dt.strftime('%Y%m%d%H%M%S')
                        night_5m = night_5m[['date', 'close']]
                        df = pd.concat([df, night_5m], ignore_index=True)
                        df = df.drop_duplicates(subset='date').sort_values('date', ascending=False).head(300).reset_index(drop=True)
                except Exception as night_err:
                    print(f"[ERA 칼만] KIS 야간데이터 병합 실패 (주간 데이터만으로 계속 진행): {night_err}")

            conn.close()

            if df.empty or len(df) < 40:
                print(f"[ERA 칼만] 데이터 부족 ({len(df)}개)")
                return
                
            # 시간순(과거 -> 현재) 정렬
            df = df.iloc[::-1].reset_index(drop=True)
            
            # 단계 1. 단기 5분봉 칼만 필터 예측값 계산 (상위 40개 활용)
            df_short = df.tail(40).reset_index(drop=True)
            closes_short = df_short['close'].values
            
            q = getattr(self, 'futures_kf_q', 0.0001)
            r = getattr(self, 'futures_kf_r', 0.5)
            mult = getattr(self, 'futures_kf_mult', 1.0)
            
            kf_prices = []
            x = None
            P = 1.0
            for z in closes_short:
                if x is None:
                    x = z
                else:
                    P = P + q
                    K = P / (P + r)
                    x = x + K * (z - x)
                    P = (1 - K) * P
                kf_prices.append(x)
                
            kf_prices = np.array(kf_prices)
            errors = closes_short - kf_prices

            # std_error 계산 구간에서 절댓값이 가장 큰 잔차 N개를 제외 — 개장 갭처럼 단발성으로
            # 튀는 봉 하나가 SL/TP/트레일링 문턱(전부 std_error 연동)을 왜곡하는 것을 완화.
            # target/추세 판정에는 영향 없음(errors는 std_error 산출에만 쓰임, kf_price는 그대로).
            # (2026-07-11: bqa/kalman_backtester.py로 전체기간·최근90일·최근30일 교차검증 후 trim=1 채택)
            std_slice = errors[-20:]
            trim_n = getattr(self, "futures_std_trim_outliers", 0)
            if trim_n > 0 and len(std_slice) > trim_n:
                order = np.argsort(np.abs(std_slice))
                std_slice = std_slice[order[:-trim_n]]
            std_error = np.std(std_slice)
            if pd.isna(std_error) or std_error <= 0:
                std_error = 0.5
                
            kf_price = kf_prices[-1]
            band = std_error * mult
            
            # 단계 2. 15분봉 리샘플링 장기 칼만 추세 필터 계산
            trend_direction = "NEUTRAL"
            try:
                df_temp = df.copy()
                df_temp['dt'] = pd.to_datetime(df_temp['date'], format='%Y%m%d%H%M%S', errors='coerce')
                df_resampled = df_temp.dropna(subset=['dt']).set_index('dt')
                df_15m = df_resampled['close'].resample('15Min').last().dropna().reset_index()
                
                if len(df_15m) >= 5:
                    closes_15m = df_15m['close'].values
                    # 장기 칼만 필터 파라미터 (q_long=0.001, r_long=1.0)
                    q_long = 0.001
                    r_long = 1.0
                    kf_long = []
                    x_l = None
                    P_l = 1.0
                    for z_l in closes_15m:
                        if x_l is None:
                            x_l = z_l
                        else:
                            P_l = P_l + q_long
                            K_l = P_l / (P_l + r_long)
                            x_l = x_l + K_l * (z_l - x_l)
                            P_l = (1 - K_l) * P_l
                        kf_long.append(x_l)
                    
                    if len(kf_long) >= 2:
                        slope = kf_long[-1] - kf_long[-2]
                        if slope > 0.01:
                            trend_direction = "UP"
                        elif slope < -0.01:
                            trend_direction = "DOWN"
                        else:
                            trend_direction = "NEUTRAL"
            except Exception as trend_err:
                print(f"[ERA 칼만] 장기 추세 필터 연산 에러 (NEUTRAL 기본 설정): {trend_err}")
            
            # 주간/야간 구분하여 설정 — 이미 열려 있는 포지션의 세션을 그대로 따른다
            # (2026-07-23 추가) 여기도 벽시계로만 판정하면, 09:00~15:50에 연 주간 포지션이
            # 18:00을 넘긴 뒤에도 _process_day_tick으로 계속 관리되도록 방금 고쳤음에도
            # (포지션 세션 판정과 통일), 정작 그 로직이 참조하는 futures_target_long/short은
            # 여기서 계속 벽시계 기준으로 야간 타겟 쪽만 갱신되어 18:00 시점 값에 멈춰버리는
            # 정합성 불일치가 생김 — _resolve_is_night_session으로 통일해 해소한다.
            is_night = (code == getattr(self, 'real_night_code', '10500000').replace("A", ""))
            if getattr(self, 'real_day_code', '10100000').replace("A", "") == getattr(self, 'real_night_code', '10500000').replace("A", ""):
                is_night = self._resolve_is_night_session(self.futures_positions)

            if std_error_only:
                # SAR 경로 — 타점은 돌파 방식을 유지해야 하므로 std_error/추세만 반영한다.
                # 로그는 값이 눈에 띄게 바뀔 때만 남긴다(5분마다 찍히면 로그가 묻힌다).
                if is_night:
                    _prev = getattr(self, 'futures_night_std_error', 0.0)
                    self.futures_night_std_error = std_error
                    self.futures_night_trend_direction = trend_direction
                else:
                    _prev = getattr(self, 'futures_std_error', 0.0)
                    self.futures_std_error = std_error
                    self.futures_trend_direction = trend_direction
                if _prev <= 0 or abs(std_error - _prev) / max(_prev, 1e-9) > 0.5:
                    _sess = "야간" if is_night else "주간"
                    print(f"[ERA 칼만 {_sess}] std_error 갱신(SAR): {_prev:.2f} → {std_error:.2f}pt "
                          f"| 진입문턱={getattr(self, 'futures_min_std_error_entry', 0.0):.2f}pt | 타점 미변경")
                return

            if is_night:
                old_trend = self.futures_night_trend_direction
                self.futures_night_target_long = kf_price + band
                self.futures_night_target_short = kf_price - band
                self.futures_night_tp_price_long = kf_price + self.futures_kf_tp_sigma_mult * std_error
                self.futures_night_tp_price_short = kf_price - self.futures_kf_tp_sigma_mult * std_error
                self.futures_night_std_error = std_error
                self.futures_night_trend_direction = trend_direction
                print(f"[ERA 칼만 야간] 타점 설정 완료 | 코드={code} | KF={kf_price:.2f}pt | std_err={std_error:.2f}pt | band={band:.2f}pt | LONG={self.futures_night_target_long:.2f}pt | SHORT={self.futures_night_target_short:.2f}pt | 3Sig LONG TP={self.futures_night_tp_price_long:.2f}pt | 3Sig SHORT TP={self.futures_night_tp_price_short:.2f}pt | 장기추세(15M)={self.futures_night_trend_direction}")
                if notifier and trend_direction != old_trend:
                    notifier.send_message(f"📈 <b>[야간선물 칼만 추세 필터 전환]</b>\n이전: {old_trend} ➡️ 현재: {trend_direction}\n타점이 실시간으로 갱신되었습니다.")
            else:
                old_trend = self.futures_trend_direction
                self.futures_target_long = kf_price + band
                self.futures_target_short = kf_price - band
                self.futures_tp_price_long = kf_price + self.futures_kf_tp_sigma_mult * std_error
                self.futures_tp_price_short = kf_price - self.futures_kf_tp_sigma_mult * std_error
                self.futures_std_error = std_error
                self.futures_trend_direction = trend_direction
                print(f"[ERA 칼만 주간] 타점 설정 완료 | 코드={code} | KF={kf_price:.2f}pt | std_err={std_error:.2f}pt | band={band:.2f}pt | LONG={self.futures_target_long:.2f}pt | SHORT={self.futures_target_short:.2f}pt | 3Sig LONG TP={self.futures_tp_price_long:.2f}pt | 3Sig SHORT TP={self.futures_tp_price_short:.2f}pt | 장기추세(15M)={self.futures_trend_direction}")
                if notifier and trend_direction != old_trend:
                    notifier.send_message(f"📈 <b>[주간선물 칼만 추세 필터 전환]</b>\n이전: {old_trend} ➡️ 현재: {trend_direction}\n타점이 실시간으로 갱신되었습니다.")
                
        except Exception as ex:
            print(f"[ERA 칼만] 실시간 타점 연산 중 에러 발생: {ex}")

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
            self.stock_daily_loss_limit_pct = float(config.get("budget_allocation", {}).get("stock_daily_loss_limit_pct", 0.08))
            self.config_stock_acc_day = config.get("accounts", {}).get("stock_account_day", "")
            self.config_stock_acc_swing = config.get("accounts", {}).get("stock_account_swing", "")
            self.config_futures_acc = config.get("accounts", {}).get("futures_account", "")
            self.gemini_api_key = config.get("api_settings", {}).get("gemini_api_key", "")
            self.apply_rsa_in_mock = config.get("features", {}).get("apply_rsa_in_mock", False)

            # 선물 손절/익절 설정 로드 (고정 pt) — config.json 기본값
            futures_settings = config.get("futures_settings", {})
            self.trade_futures_night = bool(futures_settings.get("trade_futures_night", True))
            self.futures_stop_loss_pt = float(futures_settings.get("stop_loss_pt", 3.0))
            self.futures_take_profit_pt = float(futures_settings.get("take_profit_pt", 6.0))
            # 야간선물 고정값: config.json futures_settings 기준, active_strategy.json에 의해 절대 변경 안 됨
            self.futures_night_stop_loss_pt = float(futures_settings.get("stop_loss_pt", 3.0))
            self.futures_night_take_profit_pt = float(futures_settings.get("take_profit_pt", 6.0))
            self.futures_max_trades_day = int(futures_settings.get("max_trades_day", 4))
            self.futures_max_trades_night = int(futures_settings.get("max_trades_night", 4))
            self.futures_day_max_trades_hard_cap = int(futures_settings.get("max_trades_hard_cap", 50))
            self.futures_night_max_trades_hard_cap = int(futures_settings.get("max_trades_hard_cap", 50))
            self.enable_reentry_filter = bool(futures_settings.get("enable_reentry_filter", True))

            # 칼만 필터 기본값 설정
            self.futures_strategy_type = futures_settings.get("futures_strategy_type", "volatility_breakout")
            # (2026-08-09) 초저변동성 구간 진입 차단 ATR 임계값. 기존엔 active_strategy.json
            # (BQA 자동최적화 출력 전용 파일)에서만 덮어쓸 수 있어 수동으로 고른 값을 넣기엔
            # 용도가 안 맞았다. config.json에서도 직접 설정 가능하게 하되, active_strategy.json에
            # atr_cutoff가 있으면 여전히 그쪽이 우선한다(아래 로드 순서 그대로 유지).
            self.futures_atr_cutoff = float(futures_settings.get("atr_cutoff", 0.5))
            self.futures_kf_q = float(futures_settings.get("kf_q", 0.0001))
            self.futures_kf_r = float(futures_settings.get("kf_r", 0.5))
            self.futures_kf_mult = float(futures_settings.get("kf_mult", 1.0))
            self.futures_kf_sl_mult = float(futures_settings.get("kf_sl_mult", 5.0))
            self.futures_kf_ts_trigger_mult = float(futures_settings.get("kf_ts_trigger_mult", 1.5))
            self.futures_kf_ts_callback_mult = float(futures_settings.get("kf_ts_callback_mult", 0.5))
            self.futures_kf_ts_floor = float(futures_settings.get("kf_ts_floor", 0.3))
            self.futures_kf_ts_min_rr_ratio = float(futures_settings.get("kf_ts_min_rr_ratio", 0.0))  # 손절폭(sl_limit) 대비 최소 확보 비율 도달 전엔 트레일링 미발동 (0=비활성, 기존과 동일)
            # 샹들리에 청산 (2026-07-15 도입): futures_strategy_type="chandelier"일 때만 사용.
            # 진입후 고점/저점에서 mult*ATR14만큼 물러나면 청산하는 단일공식 트레일링. 라이브값은
            # 도입 첫날(2026-07-16)부터 mult=0.3(bqa/futures_trading_report_20260716.md).
            # (구 주석의 "mult=0.5, 601건, 포착비율 1.0" 검증은 백테스트-라이브 정합성이라는
            # 별개 목적의 예비 탐색이었고 실제 채택된 적은 없음 — 아래는 그 후 별도로 진행된,
            # 실전값(0.3) 자체에 대한 검증.) 2026-07-25 실전 공식 그대로 재현한
            # run_chandelier_live_replica(동적 계약수·미니선물 승수 5만원·연속손실한도 5회·
            # 장마감 무조건청산까지 실전과 일치)로 794건/7개 분기 전체 재검증 완료 — PF 8.77,
            # MDD 1.25%, 최악단일손실 -23.34pt, 악화 분기 0개 (오버나잇_갭리스크_대응_보고서.md
            # 7~9절). 0.3 vs 0.5를 이 교정된 방법론으로 직접 맞대결시킨 적은 아직 없음(후속검증 후보).
            self.futures_chandelier_mult = float(futures_settings.get("chandelier_mult", 0.3))
            self.futures_chandelier_hard_cap = float(futures_settings.get("chandelier_hard_cap_pt", 60.0))
            _srcm = futures_settings.get("session_range_cap_mult", None)
            self.futures_session_range_cap_mult = float(_srcm) if _srcm is not None else None
            self.futures_session_range_cap_min_bars = int(futures_settings.get("session_range_cap_min_bars", 6))
            # (2026-08-03) 진입 종료 시각 — None이면 비활성(기존 동작). 주간세션 전용.
            # 늦은 진입이 EOD 강제청산에 의존하는 구조와 2026-06-11 갭 꼬리위험을 회피한다.
            # 검증: 선물_15시진입차단_백테스트_20260803.md
            _eeh = futures_settings.get("entry_end_hour", None)
            self.futures_entry_end_hour = int(_eeh) if _eeh is not None else None
            self.futures_entry_end_minute = int(futures_settings.get("entry_end_minute", 0))
            # (2026-08-04) 계약당 고정 증거금. None이면 기존 정률(가격x승수x10%) 방식 유지.
            # 실측(모의계좌, 미니 코스피200): 10,360,560원/계약 — 명목가치와 무관하게 일정.
            # 실계좌·종목·시기에 따라 거래소가 조정하므로 하드코딩하지 않고 설정값으로 둔다.
            # (2026-08-04) 증거금 요율. 미설정 시 0.10이 적용돼 기존 동작을 유지한다.
            # 실측·공식 요율은 약 0.20(위탁증거금률 19.8%)이며, 기존 0.10은 실제의 절반이라
            # 계약수를 2.11배 과대 산정한다. margin_per_contract는 고정값 오버라이드로,
            # 기준가격이 갱신되면 어긋나므로 요율 방식을 권장한다.
            self.futures_margin_rate = float(futures_settings.get("margin_rate", 0.10))
            _mpc = futures_settings.get("margin_per_contract", None)
            self.futures_margin_per_contract = float(_mpc) if _mpc is not None else None
            # (2026-08-04) 최대 계약수 상한. 기존 하드코딩 15를 설정으로 옮긴 것으로,
            # 미설정 시 15가 그대로 적용돼 동작이 바뀌지 않는다.
            self.futures_max_contracts = int(futures_settings.get("max_contracts", 15))
            # KIS(한국투자증권)로 별도 수집 중인 야간선물 코드 (kis/kis_night_futures_collector.py가
            # futures_ohlcv에 1분봉으로 적재). 주간 진입타점 계산 시 이 데이터를 병합해 간밤의
            # 가격 흐름을 반영한다 (2026-07-17 도입). 월물 만기가 바뀌면 이 값도 갱신 필요.
            self.kis_night_code = futures_settings.get("kis_night_code", "A05608")
            self.futures_kf_tp_sigma_mult = float(futures_settings.get("kf_tp_sigma_mult", 3.0))
            _ttpm = futures_settings.get("trend_tp_sigma_mult", None)
            self.futures_trend_tp_sigma_mult = float(_ttpm) if _ttpm is not None else None
            self.futures_session_range_mult = float(futures_settings.get("session_range_mult", 1.2))
            self.futures_sl_hard_cap_pt = float(futures_settings.get("sl_hard_cap_pt", 15.0))
            self.futures_reentry_k = float(futures_settings.get("reentry_k", 0.25))
            self.futures_reentry_cooldown_sec = float(futures_settings.get("reentry_cooldown_sec", 0.0))  # 청산 후 같은 방향 재진입 최소 대기시간(초, 0=비활성)
            self.futures_consecutive_loss_limit = int(futures_settings.get("consecutive_loss_limit", 5))
            self.futures_min_std_error_entry = float(futures_settings.get("min_std_error_entry", 0.0))
            # ── (2026-07-30 도입) 레짐필터 + 이익보전 (샹들리에 전용) ──────────────────────
            # 백테스트(bqa 30,950봉, 레짐+이익보전+진입임계1.5): 승률 56%→82%, PF 4.2→13.6,
            # MDD 27.7%→8.6%, 최악단일손실 -77.7pt→-28.6pt. 표준코스피200·최근장에서도 동일 방향 개선.
            # 셋 다 config 플래그로 on/off 가능하며, 끄면 기존 라이브 동작과 100% 동일.
            # (1) 레짐필터: 장기 칼만 추세가 확실히 방향성을 가질 때만(UP=롱, DOWN=숏) 신규진입 허용.
            #     기존은 역추세(DOWN롱/UP숏)만 차단했으나, 켜면 NEUTRAL(횡보) 진입까지 차단해 박스권 오진입 제거.
            self.futures_regime_filter_enabled = bool(futures_settings.get("regime_filter_enabled", False))
            # (2) 이익보전: 미실현 최대이익(MFE)이 trigger_pt 도달 시 트레일링 폭을 profit_lock_mult*ATR로
            #     좁히고, 손절선을 본전±be_buffer_pt로 끌어올려 벌어둔 이익을 되뱉지 않도록 잠근다.
            self.futures_profit_lock_enabled = bool(futures_settings.get("profit_lock_enabled", False))
            self.futures_profit_lock_trigger_pt = float(futures_settings.get("profit_lock_trigger_pt", 8.0))
            self.futures_profit_lock_mult = float(futures_settings.get("profit_lock_mult", 0.10))
            self.futures_profit_lock_be_buffer_pt = float(futures_settings.get("profit_lock_be_buffer_pt", 1.0))
            # (2026-07-30) 2단계 이익보전 — 본전이동 단계. None이면 1단계만(기존 동작).
            _pl_be_mt = futures_settings.get("profit_lock_be_move_trigger_pt", None)
            self.futures_profit_lock_be_move_trigger_pt = float(_pl_be_mt) if _pl_be_mt is not None else None
            self.futures_profit_lock_be_stage_buffer_pt = float(futures_settings.get("profit_lock_be_stage_buffer_pt", 0.0))
            # (2026-07-31) 틱 이상치 필터 '지속이동 복구' — 3% 초과 틱이 좁은 범위에 뭉쳐 N개
            # 연속 거부되면 실제 이동으로 보고 내부가격을 새 레벨로 재기준(락아웃 해제). 단발
            # 스파이크는 계속 거부. tick_recovery_enabled=False면 기존 동작(무한 거부)과 동일.
            self.futures_tick_recovery_enabled = bool(futures_settings.get("tick_recovery_enabled", True))
            self.futures_tick_recovery_streak = int(futures_settings.get("tick_recovery_streak", 20))
            self.futures_tick_recovery_band_pct = float(futures_settings.get("tick_recovery_band_pct", 0.01))
            self.futures_tick_health_alert = bool(futures_settings.get("tick_health_alert", True))
            # (2026-08-04) 청산 신뢰성 강화 4종 (오버나잇 방치 사고 대응, 모두 config 플래그)
            self.futures_exit_confirm_resync = bool(futures_settings.get("exit_confirm_resync", True))   # #1 청산 직후 잔고 능동 재조회
            self.futures_exit_retry_max = int(futures_settings.get("exit_retry_max", 3))                  # #3 재시도 한도
            self.futures_exit_monitor_enabled = bool(futures_settings.get("exit_monitor_enabled", True))  # #4 틱무관 주기 청산감시
            self.futures_eod_fast_sync_enabled = bool(futures_settings.get("eod_fast_sync_enabled", True))# #1 마감창 고속 동기화
            # (2026-08-04) 재진입 즉시-플립 방지: (a) 청산 시점에 재진입 추적값을 기록해 체결콜백
            # 누락 시에도 필터가 작동하게, (b) 방향무관 짧은 쿨다운으로 청산 직후 반대방향 즉시
            # 진입(틱단위 플립)을 차단. 백테스트상 다봉(5분+) 차단은 추세추종까지 막아 손해라
            # 초단위(기본 90초)로만 둔다 — 정상 다음봉(300초) 진입엔 영향 없음.
            self.futures_exit_record_on_send = bool(futures_settings.get("exit_record_on_send", True))
            self.futures_global_cooldown_sec = float(futures_settings.get("global_cooldown_sec", 90.0))
            # (2026-08-04) 하드 초기손절 — 본전이동 도달 이전 구간 전용. se_mult 우선(변동성 적응),
            # hard_stop_pt 지정 시 고정값. 백테스트로 최악손실·PF·평균손 개선 검증.
            self.futures_hard_stop_enabled = bool(futures_settings.get("hard_stop_enabled", True))
            self.futures_hard_stop_se_mult = float(futures_settings.get("hard_stop_se_mult", 1.5))
            _hsp = futures_settings.get("hard_stop_pt", None)
            self.futures_hard_stop_pt = float(_hsp) if _hsp is not None else None
            # (2026-08-07) 타임스톱 — 진입 후 N분 내 MFE가 트리거에 못 미치면 즉시 청산. 하드손절
            # (가격캡)과 상호보완: '시간을 끌지만 이익권에 못 가는' 트레이드를 본전 근처에서
            # 조기 청산해 자본을 회전. 백테스트: PF 38.7→59.1, 수익·승률·평균손 전부 개선.
            self.futures_time_stop_enabled = bool(futures_settings.get("time_stop_enabled", True))
            self.futures_time_stop_minutes = float(futures_settings.get("time_stop_minutes", 10.0))
            self.futures_time_stop_mfe_pt = float(futures_settings.get("time_stop_mfe_pt", 4.0))
            self.futures_std_trim_outliers = int(futures_settings.get("std_trim_outliers", 0))
            _dcm = futures_settings.get("dynamic_cap_mult", None)
            self.futures_dynamic_cap_mult = float(_dcm) if _dcm is not None else None
            _dcn = futures_settings.get("dynamic_cap_min", None)
            self.futures_dynamic_cap_min = float(_dcn) if _dcn is not None else None
            _dcx = futures_settings.get("dynamic_cap_max", None)
            self.futures_dynamic_cap_max = float(_dcx) if _dcx is not None else None
            self.sar_af_init = float(futures_settings.get("sar_af_init", 0.02))
            self.sar_af_step = float(futures_settings.get("sar_af_step", 0.02))
            self.sar_af_max  = float(futures_settings.get("sar_af_max", 0.20))
            self.bb_window   = int(futures_settings.get("bb_window", 20))
            self.bb_sigma    = float(futures_settings.get("bb_sigma", 2.0))

            # active_strategy.json의 백테스트 파라미터로 주간선물만 오버라이드 (야간선물 제외)
            active_strategy_path = os.path.join(self.workspace_root, "config", "active_strategy.json")
            if os.path.exists(active_strategy_path):
                try:
                    with open(active_strategy_path, "r", encoding="utf-8") as f:
                        active = json.load(f)
                    if "approved_at" not in active:
                        # batch_optimizer.py는 매주 실행 시 개선 여부와 무관하게 항상 approved_at을
                        # 함께 기록하므로, 정상적인 자동 최적화 결과라면 이 키가 없을 수 없다.
                        # 키가 없다면 수동 편집 등으로 승인되지 않은 값일 가능성이 있으므로 적용을
                        # 건너뛰고 기존 config.json 값을 유지한다.
                        print("[ERA] ⚠️ active_strategy.json에 approved_at이 없어 적용을 건너뜁니다 (config.json 값 유지)")
                        active = {}
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
                    if "futures_strategy_type" in active:
                        self.futures_strategy_type = active["futures_strategy_type"]
                    if "kf_q" in active:
                        self.futures_kf_q = float(active["kf_q"])
                    if "kf_r" in active:
                        self.futures_kf_r = float(active["kf_r"])
                    if "kf_mult" in active:
                        self.futures_kf_mult = float(active["kf_mult"])
                    if "kf_sl_mult" in active:
                        self.futures_kf_sl_mult = float(active["kf_sl_mult"])
                    if "kf_ts_trigger_mult" in active:
                        self.futures_kf_ts_trigger_mult = float(active["kf_ts_trigger_mult"])
                    if "kf_ts_callback_mult" in active:
                        self.futures_kf_ts_callback_mult = float(active["kf_ts_callback_mult"])
                    if "kf_ts_floor" in active:
                        self.futures_kf_ts_floor = float(active["kf_ts_floor"])
                    if "sar_af_init" in active:
                        self.sar_af_init = float(active["sar_af_init"])
                    if "sar_af_step" in active:
                        self.sar_af_step = float(active["sar_af_step"])
                    if "sar_af_max" in active:
                        self.sar_af_max  = float(active["sar_af_max"])
                    if "bb_window" in active:
                        self.bb_window = int(active["bb_window"])
                    if "bb_sigma" in active:
                        self.bb_sigma  = float(active["bb_sigma"])
                    if active:
                        print(f"[ERA] active_strategy.json 파라미터 적용: K={self.futures_best_k} | 주간손절={self.futures_stop_loss_pt}pt | 주간익절={self.futures_take_profit_pt}pt | 야간손절={self.futures_night_stop_loss_pt}pt(고정) | 야간익절={self.futures_night_take_profit_pt}pt(고정) | 마진캡={self.futures_margin_cap_ratio:.2f} | ATR필터={self.futures_atr_cutoff:.2f}pt | 전략타입={self.futures_strategy_type}")
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
            self.config_stock_acc_day = ""
            self.config_stock_acc_swing = ""
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
                    "open_price": pos.get("open_price", pos.get("buy_price", 0)),
                    "entry_date": pos.get("entry_date", ""),
                    "max_profit_ratio": pos.get("max_profit_ratio", 0.0),
                    "super_trend_mode": pos.get("super_trend_mode", False)
                }
            with open(self.positions_persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[ERA] 포지션 저장 실패: {e}")

    def load_futures_exit_state(self):
        """선물 청산가(재진입 방지용) 복원"""
        self.futures_last_long_exit_price = 0.0
        self.futures_last_long_exit_time = 0.0
        self.futures_last_short_exit_price = 0.0
        self.futures_last_short_exit_time = 0.0
        self.futures_night_last_long_exit_price = 0.0
        self.futures_night_last_long_exit_time = 0.0
        self.futures_night_last_short_exit_price = 0.0
        self.futures_night_last_short_exit_time = 0.0
        self.futures_day_consecutive_losses = 0
        self.futures_night_consecutive_losses = 0
        self.futures_day_trade_count = 0
        self.futures_night_trade_count = 0
        # (2026-07-20 추가) 트레일링 스탑 기준점/진입 스냅샷 — 아래 기본값은 열린 포지션이 없을 때의
        # 값이며, 열린 포지션이 있으면 바로 아래에서 JSON으로부터 복원된다.
        self.futures_day_peak = 0.0
        self.futures_night_peak = 0.0
        self.futures_day_entry_std_error = 0.0
        self.futures_day_entry_atr = 0.0
        self.futures_day_entry_tp_price = 0.0
        self.futures_night_entry_std_error = 0.0
        self.futures_night_entry_atr = 0.0
        self.futures_night_entry_tp_price = 0.0

        path = os.path.join(self.workspace_root, "era", "futures_exit_state.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.futures_last_long_exit_price = float(state.get("last_long_exit_price", 0.0))
                self.futures_last_long_exit_time = float(state.get("last_long_exit_time", 0.0))
                self.futures_last_short_exit_price = float(state.get("last_short_exit_price", 0.0))
                self.futures_last_short_exit_time = float(state.get("last_short_exit_time", 0.0))
                self.futures_night_last_long_exit_price = float(state.get("night_last_long_exit_price", 0.0))
                self.futures_night_last_long_exit_time = float(state.get("night_last_long_exit_time", 0.0))
                self.futures_night_last_short_exit_price = float(state.get("night_last_short_exit_price", 0.0))
                self.futures_night_last_short_exit_time = float(state.get("night_last_short_exit_time", 0.0))
                self.futures_day_consecutive_losses = int(state.get("day_consecutive_losses", 0))
                self.futures_night_consecutive_losses = int(state.get("night_consecutive_losses", 0))
                self.futures_day_trade_count = int(state.get("day_trade_count", 0))
                self.futures_night_trade_count = int(state.get("night_trade_count", 0))
                # (2026-07-20 추가) 트레일링 스탑 기준점/진입 스냅샷 복원 — 이 값들이 저장되지 않던
                # 시절엔, 장중 통신 끊김으로 인한 하드 리셋(자동 재시작) 시 포지션 자체는 실계좌
                # 조회로 복원되지만 이 값들은 __init__에서 0으로 리셋된 뒤 재시작 시점의 현재가로
                # 다시 초기화되어, 트레일링이 마치 그 시점에 새로 진입한 것처럼 오판하는 버그가
                # 있었음(2026-07-20 실측: 진입가 기준이면 -28.70pt에서 끊겼어야 할 샹들리에 청산이
                # 재시작 시점 가격 기준으로 재설정되어 -37.74pt까지 커짐).
                self.futures_day_peak = float(state.get("day_peak", 0.0))
                self.futures_night_peak = float(state.get("night_peak", 0.0))
                self.futures_day_entry_std_error = float(state.get("day_entry_std_error", 0.0))
                self.futures_day_entry_atr = float(state.get("day_entry_atr", 0.0))
                self.futures_day_entry_tp_price = float(state.get("day_entry_tp_price", 0.0))
                self.futures_night_entry_std_error = float(state.get("night_entry_std_error", 0.0))
                self.futures_night_entry_atr = float(state.get("night_entry_atr", 0.0))
                self.futures_night_entry_tp_price = float(state.get("night_entry_tp_price", 0.0))
                # 프로세스가 재연결/재시작되어도 "오늘 이미 일일 리셋을 했는지"를 잊지 않도록 복원
                # (2026-07-08: 메모리 변수만으로 판단하던 시절엔 재시작마다 리셋이 중복 실행되어
                #  day_consecutive_losses 등 당일 상태가 통째로 지워지는 버그가 있었음 — 실거래로 확인.
                #  날짜가 실제로 바뀐 경우엔 _do_daily_reset()의 자체 날짜 비교로 정상적으로 다시 실행됨)
                # load_futures_exit_state()는 __init__ 초반(line 459)에 호출되어 이 시점엔 아직
                # self._daily_reset_done_date 등이 정의되기 전이므로 getattr로 안전하게 접근해야 함
                # (2026-07-08: self._daily_reset_done_date로 직접 접근했다가 AttributeError로 이
                #  블록 전체가 실패해 재진입방지가와 연속손절 카운터 복원이 통째로 안 되는 회귀가 있었음)
                self._daily_reset_done_date = state.get("daily_reset_done_date", "") or getattr(self, "_daily_reset_done_date", "")
                self._night_reset_done_date = state.get("night_reset_done_date", "") or getattr(self, "_night_reset_done_date", "")
                self._night_start_done_date = state.get("night_start_done_date", "") or getattr(self, "_night_start_done_date", "")
                print(f"[ERA] 선물 재진입 방지 청산가 및 거래제한 정보 복원 완료: "
                      f"주간LONG={self.futures_last_long_exit_price}, 주간SHORT={self.futures_last_short_exit_price}, "
                      f"야간LONG={self.futures_night_last_long_exit_price}, 야간SHORT={self.futures_night_last_short_exit_price}, "
                      f"주간연속손절={self.futures_day_consecutive_losses}, 야간연속손절={self.futures_night_consecutive_losses}, "
                      f"주간거래횟수={self.futures_day_trade_count}, 야간거래횟수={self.futures_night_trade_count}")
                if self.futures_day_peak != 0.0 or self.futures_night_peak != 0.0:
                    print(f"[ERA] 트레일링 스탑 기준점 복원 완료: 주간peak={self.futures_day_peak:.2f}, 야간peak={self.futures_night_peak:.2f}")
            except Exception as e:
                print(f"[ERA] 선물 청산가 복원 실패: {e}")

    def save_futures_exit_state(self):
        """선물 청산가(재진입 방지용) 저장 — 신고점/신저가 갱신마다 실시간 틱 콜백 안에서
        호출되므로(트레일링 기준점 체크포인트), 쓰는 도중 프로세스가 죽어도(이 기능이
        원래 대비하려는 "통신 끊김→하드 리셋" 상황 그 자체) 파일이 손상되지 않도록 임시파일
        작성 후 os.replace로 원자적 치환한다 (apply_to_config()와 동일 패턴, 2026-07-26).
        직접 쓰기였다면 쓰는 도중 크래시 시 JSON이 잘려 load_futures_exit_state()가 실패하고
        조용히 전부 0으로 초기화되어, 트레일링 기준점 유실 방지라는 이 기능의 목적 자체가
        무력화되는 문제가 있었음."""
        try:
            path = os.path.join(self.workspace_root, "era", "futures_exit_state.json")
            state = {
                "last_long_exit_price": self.futures_last_long_exit_price,
                "last_long_exit_time": self.futures_last_long_exit_time,
                "last_short_exit_price": self.futures_last_short_exit_price,
                "last_short_exit_time": self.futures_last_short_exit_time,
                "night_last_long_exit_price": self.futures_night_last_long_exit_price,
                "night_last_long_exit_time": self.futures_night_last_long_exit_time,
                "night_last_short_exit_price": self.futures_night_last_short_exit_price,
                "night_last_short_exit_time": self.futures_night_last_short_exit_time,
                "day_consecutive_losses": self.futures_day_consecutive_losses,
                "night_consecutive_losses": self.futures_night_consecutive_losses,
                "day_trade_count": self.futures_day_trade_count,
                "night_trade_count": self.futures_night_trade_count,
                "day_peak": self.futures_day_peak,
                "night_peak": self.futures_night_peak,
                "day_entry_std_error": self.futures_day_entry_std_error,
                "day_entry_atr": self.futures_day_entry_atr,
                "day_entry_tp_price": self.futures_day_entry_tp_price,
                "night_entry_std_error": self.futures_night_entry_std_error,
                "night_entry_atr": self.futures_night_entry_atr,
                "night_entry_tp_price": self.futures_night_entry_tp_price,
                "daily_reset_done_date": self._daily_reset_done_date,
                "night_reset_done_date": self._night_reset_done_date,
                "night_start_done_date": self._night_start_done_date
            }
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, path)
        except Exception as e:
            print(f"[ERA] 선물 청산가 저장 실패: {e}")

    def _is_reentry_allowed(self, direction, current_price, is_night=False):
        """청산 후 재진입 조건 검사 (쿨다운 경과 + 유리한 가격대 또는 추가 돌파 시에만 진입 허용)"""
        if is_night:
            exit_price = self.futures_night_last_long_exit_price if direction == "LONG" else self.futures_night_last_short_exit_price
            exit_time = self.futures_night_last_long_exit_time if direction == "LONG" else self.futures_night_last_short_exit_time
        else:
            exit_price = self.futures_last_long_exit_price if direction == "LONG" else self.futures_last_short_exit_price
            exit_time = self.futures_last_long_exit_time if direction == "LONG" else self.futures_last_short_exit_time

        # (2026-08-04 #2) 방향무관 쿨다운 — 어떤 청산이든 직후 N초는 양방향 신규 진입을 모두 차단해
        # '청산 직후 즉시 반대방향 플립'을 막는다. 같은 방향 쿨다운/휩소밴드(아래)와 독립적으로,
        # 이 검사는 해당 방향의 exit_price가 0이어도(=방금 반대방향을 청산한 경우) 동작한다.
        gcd = getattr(self, "futures_global_cooldown_sec", 0.0)
        last_any = getattr(self, "futures_last_any_exit_time", 0.0)
        if gcd > 0 and last_any > 0:
            import time as _t_gc
            _elapsed_any = _t_gc.time() - last_any
            if _elapsed_any < gcd:
                _lk = f"global_{direction}_{is_night}"
                if not hasattr(self, "_last_reentry_log_time"):
                    self._last_reentry_log_time = {}
                if _t_gc.time() - self._last_reentry_log_time.get(_lk, 0) > 60:
                    print(f"[선물 재진입 차단] 방향무관 쿨다운: 최근 청산 후 {_elapsed_any:.0f}초 경과 (최소 {gcd:.0f}초 필요) — {direction} 진입 보류")
                    self._last_reentry_log_time[_lk] = _t_gc.time()
                return False

        if exit_price <= 0:
            return True

        # 청산 직후 즉시 같은 방향 재진입해 스윙형 전략이 스캘핑화되는 것을 막는 쿨다운
        # (2026-07-15 도입, reentry_cooldown_sec=0이면 기존과 100% 동일하게 비활성)
        cooldown = getattr(self, "futures_reentry_cooldown_sec", 0.0)
        if cooldown > 0 and exit_time > 0:
            import time
            elapsed = time.time() - exit_time
            if elapsed < cooldown:
                log_key = f"cooldown_{direction}_{is_night}"
                last_logged = getattr(self, "_last_reentry_log_time", {})
                if not hasattr(self, "_last_reentry_log_time"):
                    self._last_reentry_log_time = last_logged
                if time.time() - last_logged.get(log_key, 0) > 60:
                    print(f"[선물 재진입 차단] {direction} 쿨다운 작동: 청산 후 {elapsed:.0f}초 경과 (최소 {cooldown:.0f}초 필요)")
                    self._last_reentry_log_time[log_key] = time.time()
                return False

        breakout_unit = self.futures_prev_range * self.futures_reentry_k
        if breakout_unit <= 0:
            breakout_unit = 0.5 # 예외 대비 기본값

        if direction == "LONG":
            lower_bound = exit_price - breakout_unit * 0.5
            upper_bound = exit_price + breakout_unit * 0.2
            if lower_bound < current_price < upper_bound:
                import time
                now_time = time.time()
                log_key = f"{direction}_{is_night}"
                last_logged = getattr(self, "_last_reentry_log_time", {})
                if not hasattr(self, "_last_reentry_log_time"):
                    self._last_reentry_log_time = last_logged
                if now_time - last_logged.get(log_key, 0) > 60:
                    print(f"[선물 재진입 차단] LONG 휩소 방지 작동: 현재가 {current_price:.2f}pt가 차단 범위 [{lower_bound:.2f} ~ {upper_bound:.2f}] (이전 청산가: {exit_price:.2f}pt) 내에 있음")
                    self._last_reentry_log_time[log_key] = now_time
                return False
        else: # SHORT
            lower_bound = exit_price - breakout_unit * 0.2
            upper_bound = exit_price + breakout_unit * 0.5
            if lower_bound < current_price < upper_bound:
                import time
                now_time = time.time()
                log_key = f"{direction}_{is_night}"
                last_logged = getattr(self, "_last_reentry_log_time", {})
                if not hasattr(self, "_last_reentry_log_time"):
                    self._last_reentry_log_time = last_logged
                if now_time - last_logged.get(log_key, 0) > 60:
                    print(f"[선물 재진입 차단] SHORT 휩소 방지 작동: 현재가 {current_price:.2f}pt가 차단 범위 [{lower_bound:.2f} ~ {upper_bound:.2f}] (이전 청산가: {exit_price:.2f}pt) 내에 있음")
                    self._last_reentry_log_time[log_key] = now_time
                return False

        return True

    def _effective_sl_hard_cap(self, std_error):
        """동적 SL 캡이 설정되어 있으면 변동성(std_error) 기반으로, 아니면 고정 캡을 반환.
        (2026-07-09: 1.5개년 백테스트로 고변동성 국면일수록 3*std_error 익절목표가 고정 15pt 캡을
        웃돌아 손익비가 유리해짐을 확인 — 이 비율을 변동성 규모와 무관하게 유지하기 위해 도입)"""
        if self.futures_dynamic_cap_mult is not None:
            cap = self.futures_dynamic_cap_mult * std_error
            if self.futures_dynamic_cap_min is not None:
                cap = max(cap, self.futures_dynamic_cap_min)
            if self.futures_dynamic_cap_max is not None:
                cap = min(cap, self.futures_dynamic_cap_max)
            return cap
        return self.futures_sl_hard_cap_pt

    def _apply_session_range_cap(self, dist):
        """샹들리에 트레일링 폭(dist)에 '오늘 세션 레인지' 상한을 추가 적용 (2026-07-27 도입, 주간세션 전용).

        dist=mult*ATR14는 ATR14가 전일까지의 일봉 기준이라 지연 지표다 — 어제 이전 변동성이
        컸으면 오늘 실제 흐름과 무관하게 dist가 크게 유지된다(2026-07-27 실측: dist 25.40pt가
        당일 레인지 40.64pt의 62%에 달해 +22.4pt 평가익을 전부 반납하고 -3.10pt로 청산된 사례,
        선물매매_점검보고서_20260727.md 1.1절). 개장 후 session_range_cap_min_bars*5분이 지난
        뒤부터, dist를 session_range_cap_mult * (오늘 지금까지의 세션 레인지)로 추가 상한한다 —
        기존값보다 항상 같거나 좁아지기만 하므로 손절폭이 더 넓어지는 방향의 부작용은 없다.
        bqa/kalman_backtester.py의 run_chandelier_live_replica(session_range_cap_mult)로 전체기간+
        최근60/30일+7분기 교차검증 완료(10구간 중 9개 개선, 1개는 절대금액상 무시할 수준의 악화).
        야간세션은 검증 범위 밖이라 이 함수를 적용하지 않는다(_process_night_tick 미호출).
        """
        cap_mult = getattr(self, "futures_session_range_cap_mult", None)
        if cap_mult is None:
            return dist
        activated_at = getattr(self, "_day_strategy_activated_at", None)
        min_bars = getattr(self, "futures_session_range_cap_min_bars", 6)
        if activated_at is None or (datetime.now() - activated_at).total_seconds() < min_bars * 300:
            return dist
        session_range_so_far = self.futures_day_session_high - self.futures_day_session_low
        if session_range_so_far > 0:
            dist = min(dist, cap_mult * session_range_so_far)
        return dist

    def _apply_profit_lock(self, dist, entry, peak, is_long):
        """이익보전(2026-07-30 도입, 샹들리에 전용). 2단계 구조:
        - 1단계(본전이동, be_move_trigger_pt): 미실현 최대이익(MFE)이 be_move_trigger_pt에 도달하면
          손절선을 본전±be_stage_buffer_pt로 끌어올린다(트레일링 폭은 아직 그대로). MFE 3~8pt
          '사각지대'에서 소액 평가익이 큰 손실로 뒤집히던 케이스를 본전 근처에서 끊기 위함.
        - 2단계(타이트 트레일, trigger_pt): MFE가 trigger_pt(기본 8)에 도달하면 트레일링 폭을
          profit_lock_mult*ATR14로 좁히고 손절선을 본전±be_buffer_pt로 잠근다.
        반환값 (조정된 dist, 손절선 하한/상한 floor). profit_lock_enabled=False면 (dist, None)로
        기존 동작과 100% 동일. be_move_trigger_pt=None(미설정)이면 1단계 없이 기존 1단계식으로 동작.
        bqa 백테스트 검증: 2단계(BE@4) 적용 시 평균손실 −6.7→−4.2pt, PF 23.7→28.1로 개선."""
        if not getattr(self, "futures_profit_lock_enabled", False):
            return dist, None
        mfe = (peak - entry) if is_long else (entry - peak)
        floor = None
        be_trig = getattr(self, "futures_profit_lock_be_move_trigger_pt", None)
        if be_trig is not None and mfe >= be_trig:
            be_buf = getattr(self, "futures_profit_lock_be_stage_buffer_pt", 0.0)
            floor = (entry + be_buf) if is_long else (entry - be_buf)
        elif getattr(self, "futures_hard_stop_enabled", False):
            # (2026-08-04) 하드 초기손절 — 본전이동(be_move_trigger_pt) 도달 '이전' 구간에만 적용.
            # 한 번도 이익권(4pt)에 못 간 트레이드가 넓은 샹들리에 트레일(≈28pt)을 그대로 맞는 걸
            # 방지한다. 이익보전 대상(4pt 도달 트레이드)은 위 if 분기라 전혀 건드리지 않는다.
            # 백테스트(31,277봉): 최악손실 -108.6→-17.9pt, PF 28.95→37.28, 평균손 -4.2→-2.7pt,
            # 수익 유지(+13,881→+13,957%). se_mult 우선, hard_stop_pt 지정 시 고정값 사용.
            _hspt = getattr(self, "futures_hard_stop_pt", None)
            if _hspt is not None and _hspt > 0:
                _hs = _hspt
            else:
                _hs = getattr(self, "futures_hard_stop_se_mult", 0.0) * getattr(self, "futures_day_entry_std_error", 0.0)
            if _hs and _hs > 0:
                floor = (entry - _hs) if is_long else (entry + _hs)
        trig = getattr(self, "futures_profit_lock_trigger_pt", 8.0)
        if mfe >= trig:
            atr = getattr(self, "futures_atr_14", 5.0)
            dist = min(dist, getattr(self, "futures_profit_lock_mult", 0.10) * atr)
            buf = getattr(self, "futures_profit_lock_be_buffer_pt", 1.0)
            floor = (entry + buf) if is_long else (entry - buf)
        return dist, floor

    def _day_time_stop_fire(self, entry, is_long):
        """(2026-08-07) 타임스톱: 진입 후 time_stop_minutes 경과 시점에 미실현 최대이익(MFE)이
        time_stop_mfe_pt에 못 미치면 True(→ 즉시 시장가 청산). 하드손절이 '가격'을 캡한다면
        타임스톱은 '시간은 끌지만 이익권(4pt)에 못 가는' 정체 트레이드를 본전 근처에서 조기
        청산해 자본을 회전시킨다. 샹들리에 주간 전용. time_stop_enabled=False면 항상 False."""
        if not getattr(self, "futures_time_stop_enabled", False):
            return False
        et = getattr(self, "futures_day_entry_time", 0.0)
        if not et or entry <= 0:
            return False
        import time as _t_ts
        if (_t_ts.time() - et) < getattr(self, "futures_time_stop_minutes", 10.0) * 60:
            return False
        peak = getattr(self, "futures_day_peak", 0.0)
        if peak <= 0:
            return False
        mfe = (peak - entry) if is_long else (entry - peak)
        return mfe < getattr(self, "futures_time_stop_mfe_pt", 4.0)

    def _check_daily_reset(self):
        try:
            self._do_daily_reset()
        except Exception as e:
            print(f"[ERA _check_daily_reset 오류] {e}")

    def _check_periodic_atr_refresh(self):
        """15분 주기로 선물 ATR을 재계산 — 08:40 1회성 계산만으로는 장중 변동성 급변을 못 따라감"""
        try:
            now = datetime.now()
            is_day_session = (now.hour == 9) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 45)
            is_night_session = (now.hour >= 18) or (now.hour < 5)
            if not (is_day_session or is_night_session):
                return
            self.update_futures_dynamic_sl_tp()
        except Exception as e:
            print(f"[ERA _check_periodic_atr_refresh 오류] {e}")

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
            self.save_futures_exit_state()
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
            if self.system_halted or self.stock_daily_loss > 0 or self.stock_daily_halted:
                self.system_halted = False
                self.stock_daily_loss = 0
                self.stock_daily_halted = False
                print("[ERA Kill Switch 리셋] 새 거래일 시작 - 시스템 재가동, 손익 한도 초기화")
                if notifier:
                    notifier.send_message("🔄 <b>[Kill Switch 자동 해제]</b>\n새 거래일이 시작되어 시스템이 재가동됩니다.")
            self.futures_day_open = 0.0
            self._day_strategy_activated_at = None
            self.futures_order_locked = False
            self.futures_day_entry_price = 0.0
            self.futures_day_peak = 0.0
            self.futures_last_long_exit_price = 0.0
            self.futures_last_long_exit_time = 0.0
            self.futures_last_short_exit_price = 0.0
            self.futures_last_short_exit_time = 0.0
            self.futures_day_consecutive_losses = 0
            self.futures_day_trade_count = 0
            self.ohlcv_buffer.clear()
            # (2026-08-04) 틱 이상치 거부 스트릭도 함께 초기화한다. ohlcv_buffer만 비우면
            # 어제 쌓인 거부 이력이 남아, 오늘 첫 거부부터 어제·오늘 가격이 섞인 채로 군집
            # 판정이 이뤄질 수 있다(_is_sustained_tick_move는 최근 N건만 보므로 경계가 흐려짐).
            self._tick_reject.clear()
            self._tick_feed_alerted.clear()
            self.save_futures_exit_state()
            self._load_prev_range()

            # (2026-08-04) 08:45 안전청산 실패 감시 — 15:46 마감청산 감시와 짝을 이룬다.
            # 08:45~08:55는 장전 단일가 구간이라 틱이 희소해, 최악의 경우 첫 청산 주문조차
            # 나가지 못한 채 조용히 넘어갈 수 있다(재시도는 타이머가 돌지만, 그건 첫 주문이
            # 나간 뒤의 이야기다). 이 예약은 틱과 무관한 일일 리셋 타이머에서 걸어두므로
            # 그날 틱이 한 건도 없어도 반드시 실행된다.
            # 늦게 기동해 이미 08:57을 지난 경우엔 예약하지 않는다 — 장중 정상 보유 포지션을
            # 청산 실패로 오인해 거짓 경보를 울리는 것을 막기 위함.
            _morning_check_at = now.replace(hour=8, minute=57, second=0, microsecond=0)
            if datetime.now() < _morning_check_at:
                def _warn_if_morning_close_failed():
                    _stuck = [(k, v) for k, v in self.futures_positions.items()
                              if k in ("KOSPI200", "KOSPI200_NIGHT")]
                    if not _stuck:
                        return
                    _lines = "\n".join(
                        f"• {k}: {v.get('type')} {v.get('qty')}계약 @ {v.get('price', 0):.2f}pt"
                        for k, v in _stuck
                    )
                    # 밤새 벌어진 갭과 그로 인한 평가손익을 함께 싣는다 — 오버나잇으로
                    # 넘어간 포지션에서 정작 궁금한 건 "얼마나 물렸나"이기 때문.
                    _gap = getattr(self, "_last_overnight_gap", None)
                    _gap_txt = ""
                    if _gap:
                        _gap_txt = (f"\n• 야간 갭: {_gap['gap_pt']:+.2f}pt ({_gap['gap_pct']:+.2f}%), "
                                    f"야간 최종가 {_gap['night_close']:.2f}pt")
                        # 승수(미니 50,000 / 표준 250,000)를 확실히 아는 경우에만 금액을 싣는다.
                        # 종목코드 문자열로 '105'를 찾는 방식은 못 쓴다 — 실전 코드는 'A0568000'
                        # 형태라 '105'가 들어있지 않아 표준선물로 오판되고 금액이 5배로 부풀려진다
                        # (2026-08-04 테스트로 발견). 긴급 알림에서 5배 틀린 손익은
                        # 없느니만 못하므로, 모르면 pt 단위 괴리만 알리고 금액은 생략한다.
                        _prefix = getattr(self, 'futures_prefix', None)
                        _pv = {'105': 50000, '101': 250000}.get(_prefix)
                        _pnl = 0.0
                        _ok = _pv is not None
                        for _k, _v in _stuck:
                            _e = _v.get('price', 0) or 0
                            _q = _v.get('qty', 0) or 0
                            if _e <= 0:
                                _ok = False
                                break
                            _d = (_gap['night_close'] - _e) if _v.get('type') == 'LONG' else (_e - _gap['night_close'])
                            if _ok:
                                _pnl += _d * _q * _pv
                        if _ok:
                            _gap_txt += f"\n• 추정 평가손익: <b>{_pnl:+,.0f}원</b>"
                        else:
                            _gap_txt += "\n• (승수 미확인 — 금액 환산 생략)"
                    print(f"[선물 안전청산] 🚨 08:45 청산 창을 넘겼는데 포지션 잔존 — {len(_stuck)}건")
                    if notifier:
                        notifier.send_message(
                            f"🚨 <b>[선물 08:45 안전청산 실패]</b>\n"
                            f"장전 청산 창(08:45~08:55)을 넘겼는데 포지션이 남아 있습니다.\n{_lines}{_gap_txt}\n"
                            f"⚠️ 정규장 개장 전 수동 확인 필요"
                        )
                QTimer.singleShot(
                    int((_morning_check_at - datetime.now()).total_seconds() * 1000),
                    _warn_if_morning_close_failed
                )
            if self.trading_mode in ('futures', 'both'):
                self.update_futures_dynamic_sl_tp()
            print(f"[ERA 주간선물] {now.strftime('%H:%M')} 세션 준비 — 전일 Range 갱신 및 카운터 초기화")

            # [관찰용, 매매 로직 영향 없음] 야간 갭 로깅. KIS 실시간 수집기(kis/kis_night_futures_collector.py)가
            # 쌓는 미니선물 야간 데이터(코드 'A05'로 시작 — 만기월이 롤오버돼도 항상 DB에서 최신 코드를 자동
            # 탐색하므로 별도 매핑 유지 불필요)와 어제 주간선물 종가를 비교해 갭만 기록한다. 며칠~몇 주 쌓여서
            # 갭 크기와 그날 방향/승률의 상관관계가 실제로 검증되기 전까지는 절대 트레이딩 로직에 반영하지
            # 않는다 — 지금은 순수 데이터 축적 단계(2026-07-15 도입).
            try:
                conn_gap = sqlite3.connect(self.futures_db_path, timeout=10)
                cur_gap = conn_gap.cursor()
                night_row = cur_gap.execute(
                    "SELECT code, date, close FROM futures_ohlcv WHERE code LIKE 'A05%' ORDER BY date DESC LIMIT 1"
                ).fetchone()
                day_row = cur_gap.execute(
                    "SELECT date, close FROM futures_ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1",
                    (getattr(self, "real_day_code", "A0568000"),)
                ).fetchone()
                conn_gap.close()
                if night_row and day_row and night_row[2] and day_row[1]:
                    night_code, night_date, night_close = night_row
                    day_date, day_close = day_row
                    if night_date > day_date:
                        gap_pt = night_close - day_close
                        gap_pct = (gap_pt / day_close) * 100
                        print(f"[ERA 야간갭] 어제 주간종가({day_date}, {day_close:.2f}pt) → "
                              f"야간 마지막가({night_code}, {night_date}, {night_close:.2f}pt) | "
                              f"갭 {gap_pt:+.2f}pt ({gap_pct:+.2f}%)")
                        # (2026-08-04) 08:45 안전청산 실패 알림에서 쓰기 위해 보관한다.
                        # 매매 판단에는 여전히 쓰지 않는다(위 주석의 원칙 유지) — 포지션이
                        # 밤을 넘겨버린 비정상 상황에서 "얼마나 물렸는지"를 알리기 위한 참고값이다.
                        # 2026-08-03 사고 때 -33.94pt(-3.43%) 갭이 실제로 발생했고, 그 규모를
                        # 아침에 즉시 알 수 있었다면 대응 판단이 빨랐다.
                        self._last_overnight_gap = {
                            'gap_pt': gap_pt, 'gap_pct': gap_pct,
                            'night_close': night_close, 'night_code': night_code,
                        }
                    else:
                        print(f"[ERA 야간갭] 야간 데이터가 어제 종가보다 오래됨({night_date} <= {day_date}) — 수집 공백 의심, 스킵")
            except Exception as e:
                print(f"[ERA 야간갭] 계산 실패(무시, 매매 영향 없음): {e}")

            # 주간 세션 시작 전 (08:40) 실계좌 포지션과 동기화하여 청산 정확도 향상
            QTimer.singleShot(2000, self.sync_futures_positions_and_balance)

        # ── 18:00 야간선물 세션 시작 리셋 ──────────────────────────────
        night_start_key = f"{today}_1800"
        is_after_18pm = (now.hour == 18 and now.minute >= 0) or (now.hour > 18)
        if is_after_18pm and self._night_start_done_date != night_start_key:
            self._night_start_done_date = night_start_key
            self.futures_night_open         = 0.0
            self.futures_night_target_long  = float('inf')
            self.futures_night_target_short = float('-inf')
            self.futures_night_order_locked = False
            self.futures_night_last_long_exit_price = 0.0
            self.futures_night_last_long_exit_time = 0.0
            self.futures_night_last_short_exit_price = 0.0
            self.futures_night_last_short_exit_time = 0.0
            self.futures_night_consecutive_losses = 0
            self.futures_night_trade_count = 0
            self.save_futures_exit_state()
            self.futures_night_entry_price  = 0.0
            print(f"[ERA 야간선물] {now.strftime('%H:%M')} 세션 시작 대기 — 상태 및 카운터 초기화")

    def _is_trading_day(self, check_date=None):
        """지정일(기본: 오늘)이 거래일인지 확인 (주말 + KRX 휴장일)"""
        now = check_date or datetime.now()
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
                    # 기존에는 CommConnect()로 3회까지 인앱 재연결을 시도한 뒤에야 하드 리셋했으나,
                    # 이미 초기화된 OCX 인스턴스에 CommConnect()를 다시 거는 것 자체가 MSVCR100.dll
                    # 0xc0000417 네이티브 크래시의 방아쇠로 추정됨 — 2026-07-13 조사에서 Windows
                    # 이벤트 로그로 07-07/07-08/07-09/07-13 4개 거래일 모두 동일 오프셋(0x0008af3e)
                    # 크래시가 활성 시간대 통신 끊김 감지 직후 재현되는 것을 확인. 인앱 재연결 시도 없이
                    # 활성 거래시간대 끊김을 감지하는 즉시(1회만) 프로세스를 통째로 재기동한다.
                    if getattr(self, '_reconnect_attempts', 0) == 0:
                        self._reconnect_attempts = 1
                        print("🚨 [ERA] 활성 거래시간대 통신 끊김 — 인앱 재연결 없이 즉시 하드 리셋(자동 재시작)을 실행합니다.")
                        if notifier:
                            notifier.send_message(
                                "🚨 <b>[통신 끊김 - 즉시 재기동]</b>\n"
                                "Kiwoom OpenAPI 세션 초기화 및 ERA 엔진 자동 재구동을 진행합니다 (약 60초 소요)."
                            )
                        import subprocess
                        # 직접 .bat을 띄우면 그 안의 UAC 자체승격(-Verb RunAs)이 무인 환경(화면잠김/RDP끊김)에서
                        # 동의를 받을 인터랙티브 데스크톱이 없어 pause에서 영원히 멈추는 문제가 있었음.
                        # 작업 스케줄러에 RunLevel=Highest로 등록해둔 "AMATS ERA Reconnect" 태스크를
                        # schtasks /run으로 트리거하면, 호출자의 권한과 무관하게 UAC 동의창 없이 조용히 승격되어 실행됨.
                        subprocess.Popen('schtasks /run /tn "AMATS ERA Reconnect"', shell=True)
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

        # 선물 계좌 TR(예수금조회) 무응답 워치독 — 요청 후 60초 넘게 응답이 없으면 1회 알림
        requested_at = getattr(self, 'futures_sync_requested_at', None)
        responded_at = getattr(self, 'futures_sync_responded_at', None)
        if requested_at is not None and not getattr(self, '_futures_tr_timeout_alerted', False):
            if responded_at is None or responded_at < requested_at:
                elapsed = (now - requested_at).total_seconds()
                if elapsed > 60:
                    self._futures_tr_timeout_alerted = True
                    print(f"🚨 [ERA] 선물 계좌조회 TR 무응답 {elapsed:.0f}초 경과 — 키움 서버 응답 지연 의심")
                    if notifier:
                        notifier.send_message(
                            f"🚨 <b>[선물 계좌조회 TR 무응답]</b>\n"
                            f"{elapsed:.0f}초 동안 응답이 없습니다. 키움 서버 상태를 확인해주세요."
                        )

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
                self.stock_account_day = self.config_stock_acc_day
                self.stock_account_swing = self.config_stock_acc_swing
                
                # 수동 미지정 시 자동 감지 로직 (끝자리 11계좌 우선 스캔)
                if not self.stock_account_day or not self.stock_account_swing:
                    stock_candidates = [acc for acc in accounts if acc.endswith('11')]
                    if not stock_candidates:
                        stock_candidates = accounts.copy()
                        
                    # 단타 계좌 지정
                    if not self.stock_account_day and len(stock_candidates) > 0:
                        self.stock_account_day = stock_candidates[0]
                    # 스윙 계좌 지정 (후순위 후보가 있으면 다르게 지정, 없으면 단일 계좌 사용)
                    if not self.stock_account_swing:
                        if len(stock_candidates) > 1:
                            self.stock_account_swing = stock_candidates[1]
                        elif len(stock_candidates) > 0:
                            self.stock_account_swing = stock_candidates[0]
                
                # 하이브리드 폴백 판단
                if self.stock_account_day and self.stock_account_swing and self.stock_account_day != self.stock_account_swing:
                    self.is_physical_separated = True
                    print(f"[ERA] 주식 단타/스윙 물리적 계좌 분리 모드 활성화 (단타={self.stock_account_day}, 스윙={self.stock_account_swing})")
                else:
                    self.is_physical_separated = False
                    if not self.stock_account_day and self.stock_account_swing:
                        self.stock_account_day = self.stock_account_swing
                    elif self.stock_account_day and not self.stock_account_swing:
                        self.stock_account_swing = self.stock_account_day
                    print(f"[ERA] 주식 단타/스윙 단일 계좌 가상 분할 모드 활성화 (계좌={self.stock_account_day})")
            else:
                self.stock_account_day = ""
                self.stock_account_swing = ""
                self.is_physical_separated = False
                print("[ERA] 선물 전용 모드 — 주식 계좌 비활성화")

            # ── 선물 계좌 감지 (futures/both 모드에서만) ──────────────────
            if self.trading_mode in ('futures', 'both'):
                self.futures_account = self.config_futures_acc
                if not self.futures_account:
                    if is_mock:
                        for acc in accounts:
                            if acc != self.stock_account_day and acc != self.stock_account_swing and not acc.endswith('11'):
                                self.futures_account = acc
                                break
                        if not self.futures_account:
                            for acc in accounts:
                                if acc != self.stock_account_day:
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
            print(f"    주식 계좌(단타): {self.stock_account_day or '비활성'}")
            print(f"    주식 계좌(스윙): {self.stock_account_swing or '비활성'} (분리여부: {self.is_physical_separated})")
            print(f"    선물 계좌: {self.futures_account or '비활성'}")

            # ── 계좌 감지 결과 텔레그램 알림 ────────────────────────────
            acc_list_str = "\n".join(f"  [{i}] <code>{a}</code>" for i, a in enumerate(accounts))
            if notifier:
                mode_info = f"물리적 분리 완료 (단타/스윙)" if self.is_physical_separated else f"단일 계좌 가상분할"
                notifier.send_message(
                    f"🔑 <b>[계좌 감지 / {mode_tag} / {trading_label}]</b>\n\n"
                    f"<b>전체 계좌 목록:</b>\n{acc_list_str}\n\n"
                    f"{'✅' if self.stock_account_day else '⬜'} 주식(단타): <code>{self.stock_account_day or '비활성'}</code>\n"
                    f"{'✅' if self.stock_account_swing else '⬜'} 주식(스윙): <code>{self.stock_account_swing or '비활성'}</code> (<i>{mode_info}</i>)\n"
                    f"{'✅' if self.futures_account else '⬜'} 선물: <code>{self.futures_account or '비활성'}</code>\n\n"
                    f"💡 <i>모드: {trading_label} (config_local.json으로 변경 가능)</i>"
                )

            # ── 예수금 및 기존 주식 보유 종목 조회 (stock/both만) ──────────────────
            if self.trading_mode in ('stock', 'both') and self.stock_account_day:
                if self.is_physical_separated:
                    # [물리적 분리 모드] 단타 계좌 조회 실행
                    print("[ERA] 단타 계좌 조회 요청 시작...")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account_day)
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                    self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식예수금조회_단타", "opw00001", 0, "0201")

                    def _rq_day_balance():
                        if self.stock_account_day:
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account_day)
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "계좌평가잔고내역요청_단타", "opw00018", 0, "0202")
                    QTimer.singleShot(1000, _rq_day_balance)

                    # 2초 후 스윙 계좌 조회 실행 (API 과부하 및 요청 거절 원천 방지)
                    def _rq_swing_session():
                        if self.stock_account_swing:
                            print("[ERA] 스윙 계좌 조회 요청 시작 (2초 지연 시차 적용)...")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account_swing)
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식예수금조회_스윙", "opw00001", 0, "0203")

                    def _rq_swing_balance():
                        if self.stock_account_swing:
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account_swing)
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "계좌평가잔고내역요청_스윙", "opw00018", 0, "0204")

                    QTimer.singleShot(2000, _rq_swing_session)
                    QTimer.singleShot(3000, _rq_swing_balance)
                else:
                    # [단일 계좌 가상분할 모드] 단타/스윙 통합 조회 실행 (동일 계좌)
                    print("[ERA] 단일 주식 계좌 조회 요청 시작...")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account_day)
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                    self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "주식예수금조회_단타", "opw00001", 0, "0201")

                    def _rq_single_balance():
                        if self.stock_account_day:
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.stock_account_day)
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "계좌평가잔고내역요청_단타", "opw00018", 0, "0202")
                    QTimer.singleShot(1000, _rq_single_balance)

            if self.futures_account:
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.futures_account)
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선물예수금조회", "opw20010", 0, "2001")
                
                # [선물 포지션 연동 추가] opw20007 선물옵션계좌평가잔고현황요청
                # 1초 뒤에 잔고조회 요청을 보내어 연속 요청 제한 방지
                def _rq_futures_balance():
                    if self.futures_account:
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.futures_account)
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "1")
                        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선물잔고조회", "opw20007", 0, "2002")
                QTimer.singleShot(1000, _rq_futures_balance)

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
        # COM 콜백(OnReceiveTrData) 도중 발생한 예외가 Qt5Core/OCX 호출 스택을 타고
        # 그대로 전파되면 프로세스 전체가 네이티브 크래시(0xc0000409)로 종료될 수 있어,
        # 실제 처리는 _impl에서 수행하고 예외는 여기서 전부 흡수한다.
        try:
            self._on_receive_tr_data_impl(screen_no, rqname, trcode, record_name, next_str)
        except Exception as e:
            print(f"[ERA _on_receive_tr_data 오류] rqname={rqname} | {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    def _on_receive_tr_data_impl(self, screen_no, rqname, trcode, record_name, next_str):
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
                
        elif rqname in ("주식예수금조회_단타", "주식예수금조회_스윙"):
            d2_deposit = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "d+2추정예수금").strip()
            if d2_deposit:
                dep_val = int(d2_deposit)
                if rqname == "주식예수금조회_단타":
                    self.stock_total_balance_day = dep_val
                    if not self.is_physical_separated:
                        self.stock_total_balance_swing = dep_val
                else:
                    self.stock_total_balance_swing = dep_val
                
                # 전체 합산 예수금 계산 (단일계좌일 시 중복합산 방지)
                if self.is_physical_separated:
                    self.stock_total_balance = self.stock_total_balance_day + self.stock_total_balance_swing
                else:
                    self.stock_total_balance = self.stock_total_balance_day

                if self.stock_initial_balance == 0:
                    self.stock_initial_balance = self.stock_total_balance
                if self.stock_monthly_initial == 0:
                    self.stock_monthly_initial = self.stock_total_balance
                    
                # 안전 매수한도(Budget) 계산
                if self.is_physical_separated:
                    self.budget_day = min(int(self.stock_total_balance * self.ratio_day), self.stock_total_balance_day)
                    self.budget_swing = min(int(self.stock_total_balance * self.ratio_swing), self.stock_total_balance_swing)
                else:
                    self.budget_day = int(self.stock_total_balance * self.ratio_day)
                    self.budget_swing = int(self.stock_total_balance * self.ratio_swing)
                
            print(f"\n=> 💰 [주식 자금 파티셔닝 / 물리분리여부: {self.is_physical_separated}]")
            print(f"   - 총 합산 예수금: {self.stock_total_balance:,}원 (단타용 잔고: {self.stock_total_balance_day:,}원 | 스윙용 잔고: {self.stock_total_balance_swing:,}원)")
            print(f"   - 단타용 매수한도({int(self.ratio_day*100)}%): {self.budget_day:,}원 (최대 {self.max_day_positions}종목)")
            print(f"   - 스윙용 매수한도({int(self.ratio_swing*100)}%): {self.budget_swing:,}원 (최대 {self.max_swing_positions}종목)")
            
        elif rqname == "선물예수금조회":
            self.futures_sync_responded_at = datetime.now()
            available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "주문가능현금").strip()
            if not available_cash or int(available_cash) == 0:
                available_cash = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, 0, "예탁금").strip()
            if available_cash:
                self.futures_available_balance = int(available_cash)
            print(f"\n=> 💸 [선물 계좌 자금]")
            print(f"   - 선물 예수금: {self.futures_available_balance:,}원")
            print(f"   - {int(self.futures_margin_cap_ratio * 100)}% 캡 적용 가용금액: {int(self.futures_available_balance * self.futures_margin_cap_ratio):,}원")
            
        elif rqname == "선물잔고조회":
            rows = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            print(f"\n=> 📦 [기존 선물 포지션 실계좌 연동]")

            # 주간/야간 코드를 매핑하여 futures_positions 초기화용 (비교용으로 A 접두사 제거)
            real_day = getattr(self, 'real_day_code', '10100000')
            real_night = getattr(self, 'real_night_code', '10500000')
            clean_real_day = real_day.replace("A", "").strip()
            clean_real_night = real_night.replace("A", "").strip()

            # 브로커 매입단가로 entry_price를 덮어쓸지 판단하기 위해, 초기화 직전 추적 상태를 스냅샷
            prev_positions = dict(self.futures_positions)

            # 기존 잔고 초기화 (중복 방지, 실계좌 기준으로 새로 세팅)
            self.futures_positions = {}

            for i in range(rows):
                code = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "종목코드").strip()
                code_clean = code.replace("A", "").strip()
                
                # 코스피200 선물(주간 혹은 야간) 종목코드에 해당하는 잔고만 매핑하여 타 파생상품(옵션 등) 차단
                if code_clean != clean_real_day and code_clean != clean_real_night:
                    continue
                
                # 매도매수구분 (1: 매도, 2: 매수) 또는 "매도"/"매수" 문자열
                raw_dir = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "매도매수구분").strip()
                raw_qty = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "수량").strip()
                raw_buy = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "매입단가").strip()
                raw_cur = self.kiwoom.dynamicCall("GetCommData(QString, QString, int, QString)", trcode, rqname, i, "현재가").strip()

                # [방어] 콤마/공백/빈문자열 등 비정상 응답값으로 인한 int()/float() 예외 차단
                try:
                    qty = int(raw_qty.replace(",", "")) if raw_qty else 0
                    buy_price = float(raw_buy.replace(",", "")) if raw_buy else 0.0
                    current_price = float(raw_cur.replace(",", "")) if raw_cur else 0.0
                except ValueError:
                    print(f"[ERA 선물잔고조회 파싱 오류] code={code_clean} qty={raw_qty!r} buy={raw_buy!r} cur={raw_cur!r} (해당 행 스킵)")
                    continue
                
                # Kiwoom TR opw20007 returns futures prices multiplied by 1000.0 (e.g. 1339500 instead of 1339.50)
                while buy_price > 5000.0:
                    buy_price /= 1000.0
                while current_price > 5000.0:
                    current_price /= 1000.0
                
                if qty <= 0:
                    continue
                
                # 방향 판단
                p_type = "LONG"
                if raw_dir == "1" or "매도" in raw_dir:
                    p_type = "SHORT"
                
                # 미니선물(105)인지 주간(101)/야간(105)인지 세션 키 구분 — 이미 열려 있던
                # 포지션(동기화 시작 전 스냅샷 prev_positions)의 세션을 그대로 유지한다
                if clean_real_day == clean_real_night:
                    is_night = self._resolve_is_night_session(prev_positions)
                else:
                    is_night = (code_clean == clean_real_night)
                    
                pos_key = "KOSPI200_NIGHT" if is_night else "KOSPI200"
                
                self.futures_positions[pos_key] = {
                    'type': p_type,
                    'qty': qty,
                    'price': buy_price,
                    'current_price': current_price
                }

                # 프로그램 내 진입 평단가 동기화 — 단, 이미 같은 방향/수량으로 추적 중인 포지션이면
                # 브로커가 보고하는 매입단가로 무조건 덮어쓰지 않고 우리 쪽 entry_price를 유지한다.
                # (2026-07-01 실측: 실거래·잔고 변동 없이 5분 동기화 사이 브로커 평단가만 1340.50→
                # 1336.86pt로 이동한 사례 확인 — 모의투자 서버의 정산성 재계산으로 추정되며, 그대로
                # 받아쓰면 실거래 없이도 우리 손익 계산(sl_limit/3-Sigma 판정 기준)이 왜곡됨)
                prev = prev_positions.get(pos_key)
                is_same_position = (prev is not None and prev.get('type') == p_type and prev.get('qty') == qty)
                cur_entry_attr = "futures_night_entry_price" if is_night else "futures_day_entry_price"
                cur_entry = getattr(self, cur_entry_attr, 0.0)
                session_label = "야간" if is_night else "주간"

                if not is_same_position or cur_entry <= 0:
                    if prev is not None and not is_same_position:
                        print(f"[ERA 포지션 동기화] {session_label} 포지션 변경 감지({prev.get('type')} {prev.get('qty')}계약 → {p_type} {qty}계약) — entry_price {cur_entry:.2f} → {buy_price:.2f}pt 갱신")
                    setattr(self, cur_entry_attr, buy_price)
                elif abs(cur_entry - buy_price) > 0.01:
                    print(f"[ERA 포지션 동기화] ⚠️ {session_label} 브로커 평단가 드리프트 감지(실거래 없음, 동일 포지션 유지 판단) — 내부 추적값 {cur_entry:.2f}pt 유지 (브로커 보고값: {buy_price:.2f}pt)")

                # 드리프트 감지 시 내부 추적값을 유지하므로, 표시도 buy_price(브로커 보고값)가 아니라
                # 실제로 손익절 계산에 쓰이는 값을 다시 읽어와야 함 (2026-07-16: 로그만 브로커값을
                # 찍어 내부 추적값과 표시가 어긋나 모니터링 혼선을 유발하던 버그 수정)
                tracked_entry = getattr(self, cur_entry_attr, 0.0)
                print(f"   - [선물] {code_clean} | {p_type} | {qty}계약 | 평단: {tracked_entry:.2f}pt (현재가: {current_price:.2f}pt)")
                
            self.export_status()
            
        elif rqname in ("계좌평가잔고내역요청_단타", "계좌평가잔고내역요청_스윙"):
            rows = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
            is_day_rq = (rqname == "계좌평가잔고내역요청_단타")
            print(f"\n=> 📦 [기존 주식 포지션 실계좌 연동 / 요청: {rqname}] {rows}개 종목 감지")
            
            # 수신 전 각 딕셔너리 초기화 (중복 적재 방지)
            if is_day_rq:
                self.portfolio_day = {}
                # 단일 계좌 폴백일 경우 스윙 딕셔너리도 함께 초기화
                if not self.is_physical_separated:
                    self.portfolio_swing = {}
                    self.portfolio = {}
                else:
                    # 물리 분리일 시 portfolio에서 DAY 전략들만 제거
                    self.portfolio = {k: v for k, v in self.portfolio.items() if v['strategy'] != 'DAY'}
            else:
                self.portfolio_swing = {}
                # 물리 분리일 시 portfolio에서 SWING 전략들만 제거
                self.portfolio = {k: v for k, v in self.portfolio.items() if v['strategy'] != 'SWING'}

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
                    # entry_date 미복원 시 오늘 날짜로 폴백되어 스윙 최대보유일(15일) 카운트가
                    # 재시작마다 0으로 리셋되는 문제가 있었음 — 저장된 진입일을 그대로 복원
                    entry_date = persist_val.get("entry_date") or datetime.now().strftime('%Y-%m-%d')
                    # max_profit_ratio 미복원 시 본전보장손절(BE Stop) 기준선이 재접속 시점부터
                    # 조용히 사라지던 문제가 있었음 — 저장된 최고수익률을 그대로 복원
                    max_profit_ratio = persist_val.get("max_profit_ratio", 0.0)
                    super_trend_mode = persist_val.get("super_trend_mode", False)
                else:
                    strategy_tag = persist_val
                    half_sold = False
                    open_price = buy_price
                    entry_date = datetime.now().strftime('%Y-%m-%d')
                    max_profit_ratio = 0.0
                    super_trend_mode = False

                # 물리적 계좌 분리 모드일 경우 강제로 계좌 속성에 맞게 라우팅
                if self.is_physical_separated:
                    strategy_tag = "DAY" if is_day_rq else "SWING"

                pos_info = {
                    'name': name,
                    'strategy': strategy_tag,
                    'buy_price': buy_price,
                    'current_price': current_price,
                    'qty': qty,
                    'max_price': current_price,
                    'open_price': open_price,
                    'super_trend_mode': super_trend_mode,
                    'ma_10': 0, 'ma_20': 0,
                    'half_sold': half_sold,
                    'entry_date': entry_date,
                    'max_profit_ratio': max_profit_ratio
                }

                # 알맞은 딕셔너리에 저장
                if strategy_tag == 'DAY':
                    self.portfolio_day[code] = pos_info
                else:
                    self.portfolio_swing[code] = pos_info

                self.portfolio[code] = pos_info

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
                    # 레짐(UP=10일선 추종/RANGE·DOWN=5일선 칼청산)에 따라 전량청산 기준선을 동적으로 결정
                    full_period = self.get_swing_exit_ma_period()
                    ma_full = sum(closes[:full_period]) / full_period
                    current_price = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10)))
                    if current_price == 0:
                        current_price = closes[0]

                    print(f"   => [스윙 하프익절 검증] {pos['name']} 현재가: {current_price:,} / 5MA: {ma_5:,.1f} / {full_period}일MA: {ma_full:,.1f} (레짐: {getattr(self, 'current_regime', 'RANGE')}, 하프매도여부: {pos.get('half_sold', False)})")

                    # 1. 레짐 적응형 청산선 하향 이탈 시: 전량 청산
                    if current_price < ma_full:
                        print(f"   🚨 [스윙 전량 청산] {pos['name']} {full_period}일선 하향 이탈! 전량 매도.")
                        res = self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Swing_10MA_Sell]", "0103", self.stock_account_swing, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if res == 0:
                            if notifier:
                                notifier.send_message(f"📉 <b>[스윙 익절/청산] {pos['name']}</b>\n• 종가 {full_period}일선 이탈로 실계좌 시장가 전량 청산합니다.")
                        else:
                            # 이 핸들러는 하루 한 번(15:14+)만 실행되어 자체 재시도가 없으므로,
                            # 실패 시 30초 뒤 단 한 번 재시도하고 그래도 실패하면 알림으로 남긴다.
                            print(f"   ⚠️ [스윙 전량청산 주문 실패] res={res} → 30초 후 재시도")
                            def _retry_full_exit(_code=code, _qty=pos['qty'], _name=pos['name'], _period=full_period):
                                _res2 = self.kiwoom.dynamicCall(
                                    "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                                    ["[ERA_Swing_10MA_Sell]", "0103", self.stock_account_swing, 2, _code, _qty, 0, "03", ""]
                                )
                                if notifier:
                                    if _res2 == 0:
                                        notifier.send_message(f"📉 <b>[스윙 익절/청산 재시도 성공] {_name}</b>\n• 종가 {_period}일선 이탈 전량 청산 주문이 재시도로 정상 전송되었습니다.")
                                    else:
                                        notifier.send_message(f"❌ <b>[스윙 전량청산 주문 실패] {_name}</b>\n• 재시도(res={_res2})도 실패했습니다. 수동 확인이 필요합니다.")
                            QTimer.singleShot(30000, _retry_full_exit)
                    # 2. 5MA 하향 이탈 시 (10MA 위이고, 아직 하프매도가 안 된 상태): 50% 분할 매도
                    elif current_price < ma_5 and not pos.get('half_sold', False):
                        half_qty = max(1, pos['qty'] // 2)
                        print(f"   🚨 [스윙 하프 익절] {pos['name']} 5일선 하향 이탈! 절반({half_qty}주) 매도.")

                        res = self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Swing_5MA_Half]", "0103", self.stock_account_swing, 2, code, half_qty, 0, "03", ""]
                        )
                        if res == 0:
                            pos['half_sold'] = True
                            self.persist_positions()  # 상태 저장
                            if notifier:
                                notifier.send_message(f"📉 <b>[스윙 하프 익절] {pos['name']}</b>\n• 종가 5일선 이탈로 보유 물량의 절반({half_qty}주)을 시장가 매도합니다.")
                        else:
                            # half_sold를 True로 미리 세팅하지 않으므로 실패 시 다음 조회에서 자연히 재시도됨
                            print(f"   ⚠️ [스윙 하프익절 주문 실패] res={res}")
                            if notifier:
                                notifier.send_message(f"❌ <b>[스윙 하프 익절 주문 실패] {pos['name']}</b>\n• res={res}. 다음 조회 주기에 자동 재시도됩니다.")
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
        added_codes = []
        for item in leaders:
            code = item["code"]
            name = item["name"]
            if code not in self.theme_stocks:
                self.theme_stocks[code] = name
                added_names.append(name)
                added_codes.append((code, name))
                # 실시간 데이터 감시 등록
                self.kiwoom.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    "THEME_RT", code, "10;11;12;15", "1"
                )

        if added_names:
            print(f"[ERA 장중 동적 편입] {len(added_names)}종목 추가 등록 완료: {added_names}")
            # top_volume_theme DB에도 추가 기록 — 여기 없으면 _run_day_screening/RSA가 이 종목을
            # 전혀 인지하지 못해(신호도, RSA 평가 대상도 안 됨) 장중 신규 편입이 무의미해짐.
            # (기존 오늘자 레코드를 지우지 않는 추가(additive) 삽입만 수행)
            try:
                conn = sqlite3.connect(self.unified_db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL;")
                cursor = conn.cursor()
                cursor.execute("""CREATE TABLE IF NOT EXISTS top_volume_theme
                                  (date TEXT, code TEXT, name TEXT, volume TEXT, UNIQUE(date, code))""")
                today = datetime.now().strftime("%Y-%m-%d")
                for code, name in added_codes:
                    cursor.execute("INSERT OR IGNORE INTO top_volume_theme (date,code,name,volume) VALUES(?,?,?,?)",
                                   (today, code, name, "INTRADAY_LEADER"))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[ERA 장중 동적 편입] top_volume_theme 기록 실패: {e}")

            if notifier:
                notifier.send_message(
                    f"🔥 <b>[장중 주도주 동적 편입]</b>\n"
                    f"거래대금 급증 감지로 인해 새로운 주도주들을 감시 목록에 추가합니다.\n"
                    f"➕ <b>추가 종목:</b> {', '.join(added_names)}\n"
                    f"💡 <i>단타 5분 스캔 감시 실시간 연동 완료</i>"
                )
            # 새로 편입된 종목은 오늘 RSA 평가 대상에 없을 수 있으므로, 장전 1회성으로 그쳤던
            # RSA를 여기서도 재기동해 PENDING 무기한 대기(자동 만료 전까지)를 줄인다.
            self._trigger_rsa_premarket()

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

    def _is_sustained_tick_move(self, code, price):
        """이상치로 거부된 틱을 누적해, 좁은 범위에 N개 연속 뭉치면 단발 글리치가 아니라
        실제 가격 이동으로 판정한다. (True, 재기준가) 또는 (False, 0.0)을 반환. (2026-07-31)

        정규장 개장(09:00) 갭처럼 봉 경계에서 3%를 넘는 '정당한' 이동이 들어오면, 기존
        로직은 신규봉 시가를 직전 종가로 대체 → 이후 모든 틱이 그 스테일 기준 대비 3% 밖이라
        전부 거부 → 봉이 바뀌어도 대체가 반복되어 하루 종일 락아웃되는 문제가 실측 확인됨
        (2026-07-31: 09:05 +6.31% 갭 직후부터 93,894틱 거부, 당일 체결 0건, DB에 평탄봉 82개
        기록. 매매시스템_점검보고서_20260731.md 참조). 신규봉/기존봉 양쪽 경로가 이 함수로
        거부 스트릭을 공유해서, 봉 경계를 넘어가도 지속 이동이면 곧바로 새 레벨을 채택한다."""
        if not getattr(self, "futures_tick_recovery_enabled", True):
            return False, 0.0
        st = self._tick_reject.setdefault(code, [])
        st.append(price)
        need = getattr(self, "futures_tick_recovery_streak", 20)
        if len(st) < need:
            return False, 0.0
        recent = st[-need:]
        st[:] = recent  # 스트릭 길이 제한
        med = sorted(recent)[len(recent) // 2]
        band = getattr(self, "futures_tick_recovery_band_pct", 0.01)
        if med > 0 and (max(recent) - min(recent)) / med <= band:
            return True, med
        return False, 0.0

    def _notify_tick_recovery(self, code, old_price, new_price, where):
        """이상치 락아웃 해제 알림 (코드별 1회만 — 복구가 반복돼도 도배하지 않음)"""
        if not getattr(self, "futures_tick_health_alert", True):
            return
        if notifier and not self._tick_feed_alerted.get(code):
            self._tick_feed_alerted[code] = True
            notifier.send_message(
                f"⚠️ <b>[선물 피드 복구]</b> {code} 내부가격 {old_price:.2f}→{new_price:.2f}pt "
                f"재기준 ({where}, 지속 이동 감지로 이상치 락아웃 해제)"
            )

    def _update_futures_ohlcv(self, code, price):
        """선물 실시간 틱 → 5분봉 OHLCV 인메모리 버퍼 갱신 (30초마다 DB 동기화)
        야간 세션 데이터를 futures_ohlcv 테이블에 축적해서 향후 야간 백테스트 가능하게 함"""
        if price <= 0:
            return
        now = datetime.now()
        period_min = (now.minute // 5) * 5
        period_str = now.strftime(f"%Y%m%d{now.hour:02d}") + f"{period_min:02d}00"
        if code not in self.ohlcv_buffer:
            self.ohlcv_buffer[code] = {}
        buf = self.ohlcv_buffer[code]
        max_jump = getattr(self, "futures_tick_max_jump_pct", 0.03)
        if period_str not in buf:
            is_new_candle = len(buf) > 0
            # 신규 봉 시가가 직전 봉 종가 대비 급격히 괴리되면(순간 오류 틱/글리치) 이상치로 보고 직전 종가로 대체
            if is_new_candle:
                prev_close = buf[max(buf.keys())]['c']
                if prev_close > 0 and abs(price - prev_close) / prev_close > max_jump:
                    # (2026-07-31) 지속 이동이면 대체하지 않고 새 레벨을 그대로 채택한다.
                    # 대체를 반복하면 스테일 가격이 봉을 넘어 계속 승계되어 락아웃이 하루 종일
                    # 풀리지 않는다(2026-07-31 09:05 개장갭 사고의 직접 원인).
                    _ok, _med = self._is_sustained_tick_move(code, price)
                    if _ok:
                        print(f"[ERA 이상치 복구] {code} 신규봉 시가 {price:.2f}pt 채택 — 지속 이동 판단(직전 종가 {prev_close:.2f}pt로 대체하지 않음)")
                        self._tick_reject[code] = []
                        self._notify_tick_recovery(code, prev_close, price, "신규봉")
                    else:
                        print(f"[ERA 이상치 필터] {code} 신규봉 시가 {price:.2f}pt가 직전 종가 {prev_close:.2f}pt 대비 {abs(price-prev_close)/prev_close*100:.1f}% 괴리 — 직전 종가로 대체")
                        price = prev_close
            buf[period_str] = {'o': price, 'h': price, 'l': price, 'c': price, 'v': 1}
            if is_new_candle:
                # (2026-07-20 추가) DB 조회+KIS 야간데이터 병합+칼만 재계산을 포함하는 무거운 작업을
                # 실시간 틱 콜백 안에서 동기 실행하면, 정규장 개장 시각(09:00:00)처럼 5분봉 경계와
                # 실시간 이벤트 폭주가 겹치는 순간 메인 스레드가 길게 블로킹되어 Kiwoom 세션이 통신
                # 끊김으로 오판되고 하드 리셋되는 사고가 반복됨(2026-07-14/15/16/20 4개 거래일
                # 09:00:36~09:01:08 사이 동일 패턴 확인). 호출 순서상 이번 틱의 매매판단
                # (_process_futures_tick)은 이 함수보다 항상 먼저 실행되어 이미 계산된 target값을
                # 쓰므로, 재계산을 다음 이벤트 루프로 미뤄도 이번 틱 판단엔 영향이 없음 — 새
                # target 반영이 최대 수 초 늦어질 뿐, 5분 주기 전략 특성상 무시 가능한 수준.
                QTimer.singleShot(0, lambda c=code: self._refresh_futures_targets_deferred(c))
        else:
            c = buf[period_str]
            # 봉 시가 대비 급격히 괴리된 틱(순간 오류/글리치)은 high/low/종가 반영에서 제외하여
            # ATR·손절폭 계산이 단발성 이상 틱 하나로 몇 주씩 왜곡되는 사고를 방지 (2026-06-23 사례)
            if c['o'] > 0 and abs(price - c['o']) / c['o'] > max_jump:
                # (2026-07-31) 지속이동 복구: 3% 초과 틱이 좁은 범위에 N개 연속 뭉쳐 거부되면
                # 단발 글리치가 아니라 실제 이동으로 보고 내부가격을 새 레벨로 재기준(락아웃 해제).
                _msg = f"[ERA 이상치 필터] {code} 틱 {price:.2f}pt가 봉 시가 {c['o']:.2f}pt 대비 {abs(price-c['o'])/c['o']*100:.1f}% 괴리 — 이상치로 판단해 무시"
                _ok, _med = self._is_sustained_tick_move(code, price)
                if not _ok:
                    print(_msg); return
                old_o = c['o']
                c['o'] = c['h'] = c['l'] = c['c'] = _med
                self._tick_reject[code] = []
                print(f"[ERA 이상치 복구] {code} 연속 거부틱이 {_med:.2f}pt에 군집 — 지속 이동 판단, 내부가격 {old_o:.2f}→{_med:.2f}pt 재기준(락아웃 해제)")
                self._notify_tick_recovery(code, old_o, _med, "봉 내부")
                # 재기준 후 이번 틱은 아래 정상 반영 경로로 진행
            else:
                # 정상 채택 틱 — 거부 스트릭·경고 상태 리셋
                if self._tick_reject.get(code):
                    self._tick_reject[code] = []
                if self._tick_feed_alerted.get(code):
                    self._tick_feed_alerted[code] = False
            if price > c['h']:
                c['h'] = price
            if price < c['l']:
                c['l'] = price
            c['c'] = price
            c['v'] += 1

    def _refresh_futures_targets_deferred(self, code):
        """5분봉 경계에서의 OHLCV 플러시 + target 재계산 — 실시간 콜백의 동기 블로킹을 피하기
        위해 QTimer.singleShot(0, ...)으로 이벤트 루프에 양보한 뒤 실행됨 (2026-07-20 추가).
        singleShot 콜백은 OnReceiveRealData의 try/except 밖에서 실행되므로 예외를 직접 흡수해야 함."""
        try:
            self._flush_ohlcv_buffer()
            if getattr(self, "futures_strategy_type", "volatility_breakout") in ("kalman", "chandelier"):
                self.update_kalman_targets(code)
            elif getattr(self, "futures_strategy_type", "volatility_breakout") == "parabolic_sar":
                # (2026-08-10) BB/PSAR 필터만 갱신하던 것을 std_error 갱신과 함께 돌린다.
                # std_error는 진입 게이트(min_std_error_entry)가 참조하는 값인데 SAR에서는
                # 아무도 채우지 않아 초기값 0.5로 굳어 있었고, 그 탓에 진입이 전면 차단됐다.
                # 타점은 std_error_only=True로 보호한다 — SAR은 돌파 타점을 쓴다.
                self.update_kalman_targets(code, std_error_only=True)
                self.update_bb_psar_filters(code)
        except Exception as e:
            import traceback
            print(f"[ERA 선물 target 재계산 오류] {e}\n{traceback.format_exc()}")

    def _flush_ohlcv_buffer(self):
        """30초마다 인메모리 OHLCV 버퍼를 DB에 일괄 동기화
        - 주식 코드 (6자리 이하): unified_data.db intraday_ohlcv
        - 선물 코드 (8자리+): futures_data.db futures_ohlcv (야간 데이터 축적용)
        - 개별주식선물 (ISF) 코드: futures_data.db isf_ohlcv
        """
        if not self.ohlcv_buffer:
            return

        futures_codes = {getattr(self, 'real_day_code', '10100000'),
                         getattr(self, 'real_night_code', '10500000')}

        stock_rows   = []
        futures_rows = []
        isf_rows     = []
        for code, periods in self.ohlcv_buffer.items():
            for period_str, c in periods.items():
                row = (code, period_str, c['o'], c['h'], c['l'], c['c'], c['v'])
                if code in futures_codes:
                    futures_rows.append(row)
                elif len(code) > 6:
                    isf_rows.append(row)
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

        try:
            if isf_rows:
                conn = sqlite3.connect(self.futures_db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL;")
                cursor = conn.cursor()
                cursor.execute("""CREATE TABLE IF NOT EXISTS isf_ohlcv
                                  (code TEXT, date TEXT, open REAL, high REAL,
                                   low REAL, close REAL, volume INTEGER, UNIQUE(code, date))""")
                cursor.executemany(
                    "REPLACE INTO isf_ohlcv (code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                    isf_rows
                )
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[ERA ISF OHLCV 플러시 오류] {e}")

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
            self.futures_sync_timeout_timer.stop()
            return
            
        if self.futures_sync_index >= len(self.futures_sync_queue):
            # 모든 코드의 동기화 완료!
            self.futures_sync_active = False
            self.futures_sync_timeout_timer.stop()
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
        
        # 5초 타임아웃 타이머 작동
        self.futures_sync_timeout_timer.start(5000)
        
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
            if "approved_at" not in data:
                # load_config()의 active_strategy.json 적용부와 동일한 원칙: 정상적인
                # batch_optimizer.py 결과라면 항상 approved_at이 함께 기록되어 있다.
                print("[ERA] ⚠️ active_strategy.json에 approved_at이 없어 K값 적용을 건너뜁니다 (기존 값 유지)")
                return
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

    def _on_futures_sync_timeout(self):
        if not self.futures_sync_active:
            return
        print("\n[ERA 선물 동기화 경고] 5초간 TR 응답이 없어 동기화를 강제 종료하고 메인 엔진을 구동합니다.")
        if notifier:
            try:
                notifier.send_message("⚠️ <b>[선물 동기화 타임아웃]</b>\n키움 서버의 응답이 지연되어 과거 데이터 동기화를 건너뛰고 주문 엔진을 즉시 구동합니다.")
            except:
                pass
        
        self.futures_sync_active = False
        self._load_prev_range()
        self.update_futures_dynamic_sl_tp()
        self.futures_strategy_active = True
        
        print(f"\n[ERA 선물 강제 가동] K={self.futures_best_k:.2f} | 전일Range={self.futures_prev_range:.2f}pt")

    def sync_futures_positions_and_balance(self):
        """실제 선물계좌의 예수금 및 포지션을 동기화하기 위해 키움 서버에 TR 요청"""
        if not self.futures_account:
            return

        # 휴장일에는 키움 서버가 TR에 응답하지 않아 60초 워치독 알림이 5분마다 반복
        # 발생하는 문제가 있었음 — 시간대만 보고 요청을 쏘던 기존 로직에 거래일 여부를
        # 추가로 확인해 휴장일에는 아예 요청 자체를 보내지 않는다.
        if not self._is_trading_day():
            return

        now = datetime.now()
        # 장중 시간대에만 실시간 조회 요청 (과도한 요청 방지 및 API 보호)
        # 주간 세션: 08:30 ~ 15:50
        # 야간 세션: 17:50 ~ 익일 05:10
        is_active_session = (
            (8 <= now.hour < 16) or 
            (now.hour >= 17) or 
            (now.hour < 6)
        )
        if not is_active_session:
            return

        # (2026-07-21 추가) 09:00~09:02는 정규장 개장 직후 실시간 시세 폭주와 겹쳐 통신 끊김
        # 하드 리셋이 반복 재현된 구간(2026-07-14/15/16/20/21 확인). 이 5분 주기 TR 요청이
        # 매번 그 시점 근처에 걸려 있었으므로, 이 구간만 한 차례 건너뛰어 하필 서버가 가장
        # 붐빌 시점에 추가 요청을 보태지 않는다 — 다음 5분 주기(09:05)에 정상적으로 재개됨.
        if now.hour == 9 and now.minute < 2:
            return

        print(f"\n=> 🔄 [선물 실계좌 동기화 TR 요청] 시각: {now.strftime('%H:%M:%S')}")
        self.futures_sync_requested_at = now
        self._futures_tr_timeout_alerted = False
        try:
            # 1. 선물 예수금 조회
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.futures_account)
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선물예수금조회", "opw20010", 0, "2001")
            
            # 2. 선물잔고조회 (opw20007)
            # 1초 뒤에 잔고조회 요청을 보내어 연속 요청 제한 방지
            def _rq_balance():
                if self.futures_account:
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.futures_account)
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                    self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "1")
                    self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", "선물잔고조회", "opw20007", 0, "2002")
            QTimer.singleShot(1000, _rq_balance)
        except Exception as e:
            print(f"[선물 동기화 요청 오류] {e}")

    def _futures_exit_monitor_tick(self):
        """(2026-08-04 #4) 틱과 무관하게 주기적으로 보유 주간 포지션의 청산 감시를 돌린다.
        개장/마감 단일가나 피드 락아웃으로 실시간 틱이 끊겨도 샹들리에 스탑·강제청산이
        작동하도록. 포지션이 있을 때만 호출하므로 _process_day_tick은 청산/보유 관리만 하고
        신규 진입 경로엔 들어가지 않는다(보유 중이면 진입블록 직전에서 return)."""
        try:
            if not getattr(self, "futures_exit_monitor_enabled", True):
                return
            if not getattr(self, "futures_strategy_active", False):
                return
            pos_key = "KOSPI200"
            pos = self.futures_positions.get(pos_key)
            if not pos:
                return  # 포지션이 없을 때는 절대 호출하지 않음(진입 오발 방지)
            now = datetime.now()
            if not (8 <= now.hour < 16):
                return  # 주간 세션 시간대에서만(야간은 틱 경로가 담당)
            last_price = pos.get('current_price') or getattr(self, 'futures_day_entry_price', 0.0)
            if not last_price or last_price <= 0:
                return
            code = getattr(self, 'real_day_code', '') or (getattr(self, 'futures_prefix', '101') + '00000')
            self._process_day_tick(code, float(last_price), now)
        except Exception as e:
            print(f"[선물 청산감시 타이머 오류] {e}")

    def _futures_eod_fast_sync_tick(self):
        """(2026-08-04 #1) 마감 청산 창(15:30~15:50)에 주간 포지션이 남아 있으면 계좌동기화를
        더 자주 돌려, 청산 체결 여부를 5분 주기가 아니라 십수 초 내에 확인한다."""
        try:
            if not getattr(self, "futures_eod_fast_sync_enabled", True):
                return
            now = datetime.now()
            if not (now.hour == 15 and 30 <= now.minute <= 50):
                return
            if "KOSPI200" not in self.futures_positions:
                return
            self.sync_futures_positions_and_balance()
        except Exception as e:
            print(f"[선물 EOD 고속동기화 오류] {e}")

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
                    GROUP BY SUBSTR(date, 1, 8) ORDER BY d DESC LIMIT 3
                """, (query_code,))
                rows = cursor.fetchall()
                if len(rows) >= 1:
                    break
            conn.close()
            
            target_row = None
            today_str = datetime.now().strftime("%Y%m%d")
            if len(rows) >= 1:
                if rows[0][0] == today_str:
                    if len(rows) >= 2:
                        target_row = rows[1]
                else:
                    target_row = rows[0]

            if target_row:
                prev_h, prev_l = target_row[1], target_row[2]
                calc = prev_h - prev_l
                if calc > 0:
                    self.futures_prev_range = calc
                    print(f"[ERA 선물] 전일 Range 로드 완료: {calc:.2f}pt (조회코드: {query_code}, 날짜: {target_row[0]})")
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
                GROUP BY d ORDER BY d DESC LIMIT 3
            """, (fc,))
            rows = cursor.fetchall()
            conn.close()
            
            target_row = None
            today_str = datetime.now().strftime("%Y%m%d")
            if len(rows) >= 1:
                if rows[0][0] == today_str:
                    if len(rows) >= 2:
                        target_row = rows[1]
                else:
                    target_row = rows[0]

            if target_row:
                prev_range = target_row[1] - target_row[2]
                if prev_range > 0:
                    self.isf_prev_range[sc] = prev_range
                    print(f"[ISF] {isf_cfg['name']} 전일 Range: {prev_range:,.0f}원 (날짜: {target_row[0]})")
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
            # 09:00 개장 직후엔 주간선물전략(1순위)·단타스캔(2순위)이 먼저 실행되게 최소 20초 대기
            # (2026-07-17: 개장 순간 여러 로직이 한꺼번에 몰려 키움 API에 순간 부하가 걸리는 것을 완화)
            if now.minute == 0:
                activated_at = getattr(self, "_day_strategy_activated_at", None)
                if activated_at is None or (now - activated_at).total_seconds() < 20:
                    return
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

    def _resolve_is_night_session(self, positions):
        """미니선물(주간=야간 동일 코드)의 세션 슬롯("KOSPI200" vs "KOSPI200_NIGHT")을 판정한다.

        (2026-07-23 추가) 기존에는 이 판정을 매번 현재 시각(18시 이후=야간)만으로 다시
        내렸는데, 이러면 09:00~15:50 사이 열린 포지션이 18:00을 넘기는 순간 물리적으로는
        같은 포지션인데도 "야간 포지션"으로 재분류됐다. 그 결과 (1) 청산/트레일링을 담당하는
        함수 자체가 _process_day_tick에서 _process_night_tick으로 바뀌고, (2) 마침 18:00
        리셋으로 0이 된 futures_night_entry_price를 드리프트 방지 로직이 "신규 포지션"으로
        오판해 모의투자 정산가 재계산값을 그대로 진입가로 흡수해버렸다(2026-07-21/23 실측:
        진입가가 각각 26.7pt, 1.74pt 오염). 이미 열려 있는 포지션은 그 포지션이 실제로 열린
        세션 슬롯을 그대로 유지하고, 아직 아무 포지션도 없을 때만 현재 시각으로 새로 판정한다.
        """
        has_day = "KOSPI200" in positions
        has_night = "KOSPI200_NIGHT" in positions
        if has_day and not has_night:
            return False
        if has_night and not has_day:
            return True
        h = datetime.now().hour
        return (h >= 18) or (h < 5)

    def _process_futures_tick(self, code, current_price):
        """실시간 선물 현재가 수신 — 주간/야간 세션 분리 처리"""
        if current_price <= 0:
            return

        # 미니선물(105)은 주간/야간 코드가 동일하므로 세션 구분이 필요 — 이미 열려 있는
        # 포지션이 있으면 그 세션을 유지하고, 없을 때만 현재 시각으로 새로 판정
        real_day = getattr(self, 'real_day_code', '10100000')
        real_night = getattr(self, 'real_night_code', '10500000')
        if real_day == real_night:
            is_night = self._resolve_is_night_session(self.futures_positions)
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
            # trade_futures_night=False여도 기존 야간 포지션의 손절/익절 감시는 계속되어야 하므로
            # 여기서 통째로 차단하지 않고 _process_night_tick 내부(신규 진입 직전)에서만 차단한다.
            self._process_night_tick(code, current_price, now)
        else:
            self._process_day_tick(code, current_price, now)

    def _process_day_tick(self, code, current_price, now):
        """주간 선물 전략 (09:00 진입 → 익일 08:45 청산, 3pt 손절 / 대안 C 트레일링 스탑)"""
        pos_key = "KOSPI200"

        # 08:45~08:55 익일 장전 강제 청산
        if now.hour == 8 and 45 <= now.minute <= 55:
            # 주간 및 야간 포지션 모두 강제 청산 시도 (이중 안전장치)
            for k in ("KOSPI200", "KOSPI200_NIGHT"):
                if k in self.futures_positions and not self.futures_positions[k].get('is_exiting', False):
                    pos = self.futures_positions[k]
                    target_code = self.real_night_code if k == "KOSPI200_NIGHT" else self.real_day_code
                    print(f"[선물 안전 청산] ⏰ 08:45 강제 청산 실행 ({k}) | 종목코드: {target_code}")
                    self._execute_futures_direct("LONG_EXIT" if pos["type"] == "LONG" else "SHORT_EXIT",
                                                 current_price, target_code, k)
            self.futures_day_entry_price = 0.0
            self.futures_day_peak = 0.0
            self.futures_night_entry_price = 0.0
            return

        # 09:00 ~ 15:45 정규장 시초가 및 목표가 동적 생성
        is_day_session = (now.hour == 9 and now.minute >= 0) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 45)
        if is_day_session and self.futures_day_open == 0:
            db_open = self._get_today_futures_open(code)
            day_open = db_open if db_open > 0 else current_price
            self.futures_day_open     = day_open
            # 09:00 개장 시 여러 로직(단타 신호 스캔, ISF 방향체크)이 한꺼번에 몰려 키움 API에
            # 순간 부하가 걸리는 것을 완화하기 위한 기준 시각 — 주간선물전략이 항상 가장 먼저
            # 실행되고, 단타 스캔/ISF 체크는 이 시각 기준으로 지연 실행됨 (2026-07-17 도입).
            # 반복 타이머(5분/1분 주기)의 위상에 기대면 순서가 우연에 좌우되므로, 여기서
            # QTimer.singleShot으로 명시적 순차 실행도 함께 예약해 순서를 확정한다 — 각 함수는
            # 이미 "오늘 처음이면 1회만 실행" 패턴이라 반복 타이머와 겹쳐 불려도 안전함.
            self._day_strategy_activated_at = datetime.now()
            if self.trading_mode in ('stock', 'both'):
                QTimer.singleShot(10000, self._run_day_screening)
            if self.trading_mode in ('futures', 'both'):
                QTimer.singleShot(20000, self._update_isf_direction_if_needed)

            # 금일 주간 세션 고가/저가 DB 복원 또는 초기화
            db_high, db_low = self._get_today_futures_high_low(code)
            self.futures_day_session_high = db_high if db_high > 0 else current_price
            self.futures_day_session_low = db_low if db_low > 0 else current_price

            # 칼만 필터/샹들리에 전략인 경우 타점을 덮어쓰지 않고 필요시 초기화 (둘 다 켈만밴드 돌파 타점을 공유)
            if getattr(self, "futures_strategy_type", "volatility_breakout") in ("kalman", "chandelier"):
                if self.futures_target_long == float('inf') or self.futures_target_short == float('-inf'):
                    self.update_kalman_targets(code)
                src_label = "DB 시초가" if db_open > 0 else "현재가(폴백)"
                print(f"\n[주간선물(칼만)] ✅ 시초가 설정: {day_open:.2f}pt ({src_label})")
                print(f"  LONG목표: {self.futures_target_long:.2f}  SHORT목표: {self.futures_target_short:.2f}")
                print(f"  손절: 하이브리드 동적 ({self.futures_kf_sl_mult:.2f}배) | 익절: 동적 3-Sigma")
                if notifier:
                    notifier.send_message(
                        f"🌅 <b>[주간선물(칼만) 목표가]</b>\n"
                        f"• 시초가: {day_open:.2f}pt ({src_label})\n"
                        f"• LONG ▲ {self.futures_target_long:.2f}pt (3Sig TP: {getattr(self, 'futures_tp_price_long', 0.0):.2f}pt)\n"
                        f"• SHORT ▼ {self.futures_target_short:.2f}pt (3Sig TP: {getattr(self, 'futures_tp_price_short', 0.0):.2f}pt)\n"
                        f"• 손절: 하이브리드 동적 ({self.futures_kf_sl_mult:.2f}배) | 익절: 동적 3-Sigma"
                    )
            else:
                self.futures_target_long  = day_open + self.futures_prev_range * self.futures_best_k
                self.futures_target_short = day_open - self.futures_prev_range * self.futures_best_k
                # (2026-08-10) 돌파 타점을 쓰는 전략도 진입 게이트가 std_error를 참조한다.
                # 장 초반 5분봉 경계 갱신(3102행)이 돌기 전까지 초기값 0.5로 남지 않도록
                # 세션 시작 시점에 한 번 채워둔다. 타점은 위에서 정한 값을 유지한다.
                if getattr(self, "futures_strategy_type", "") == "parabolic_sar":
                    self.update_kalman_targets(code, std_error_only=True)
                src_label = "DB 시초가" if db_open > 0 else "현재가(폴백)"
                print(f"\n[주간선물] ✅ 시초가 설정: {day_open:.2f}pt ({src_label})")
                print(f"  LONG목표: {self.futures_target_long:.2f}  SHORT목표: {self.futures_target_short:.2f}")
                print(f"  std_error: {getattr(self, 'futures_std_error', 0.0):.2f}pt "
                      f"(진입문턱 {getattr(self, 'futures_min_std_error_entry', 0.0):.2f}pt)")
                print(f"  손절: {self.futures_stop_loss_pt}pt | 익절: {self.futures_take_profit_pt}pt (고정)")
                if notifier:
                    notifier.send_message(
                        f"🌅 <b>[주간선물 목표가]</b>\n"
                        f"• 시초가: {day_open:.2f}pt ({src_label})\n"
                        f"• LONG ▲ {self.futures_target_long:.2f}pt\n"
                        f"• SHORT ▼ {self.futures_target_short:.2f}pt\n"
                        f"• 손절: {self.futures_stop_loss_pt}pt | 익절: {self.futures_take_profit_pt}pt (고정)"
                    )

            if getattr(self, "futures_atr_14", 2.0) == 2.0 and not getattr(self, "futures_atr_14_updated_at", None):
                print("[ERA 주간선물] ⚠️ ATR14가 초기값(2.0pt)에 머물러 있어 장 시작 전 재계산을 재시도합니다.")
                self.update_futures_dynamic_sl_tp()

        if self.futures_day_open == 0:
            return

        # 실시간 금일 주간 세션 고가/저가 업데이트
        if current_price > self.futures_day_session_high:
            self.futures_day_session_high = current_price
        if current_price < self.futures_day_session_low or self.futures_day_session_low == 0:
            self.futures_day_session_low = current_price

        # 장마감 전 무조건 강제 청산 (Overnight Gap Risk Control)
        # (2026-07-24: 기존엔 "고변동성 세션마감"에만(변동폭>ATR 임계값) 조건부로 청산했으나,
        #  오버나잇/휴장 갭은 손절선을 그냥 건너뛰어 버려 손절 자체가 무력화되는 사례가 실측
        #  확인됨(2026-06-11→12, 의도한 캡 9.29pt인데 실제 -114.53pt 청산). 분기별 백테스트
        #  검증 결과 조건 없이 매일 장마감 전 청산하도록 바꿔도 7개 분기 전부 baseline 대비
        #  악화가 없었고, 2개 분기는 최악 단일손실이 40~66% 줄었음 — 조건을 없애고 항상 청산.)
        is_market_close_time = (now.hour == 15 and 35 <= now.minute <= 45)
        if is_market_close_time:
            if pos_key in self.futures_positions and not self.futures_positions[pos_key].get('is_exiting', False):
                pos = self.futures_positions[pos_key]
                session_range = self.futures_day_session_high - self.futures_day_session_low
                msg = f"[주간선물] ⏰ 장마감 전 강제 청산 (당일 변동폭: {session_range:.2f}pt) | 진입가: {self.futures_day_entry_price:.2f} ➡️ 청산가: {current_price:.2f}"
                print(msg)
                self._execute_futures_direct("LONG_EXIT" if pos["type"] == "LONG" else "SHORT_EXIT",
                                             current_price, code, pos_key)
                self.futures_day_entry_price = 0.0
                self.futures_day_peak = 0.0
                if notifier:
                    notifier.send_message(f"⏰ <b>[장마감 전 강제 청산]</b>\n• 당일 변동폭: {session_range:.2f}pt\n• 오버나잇 갭 리스크 회피를 위해 포지션 청산 완료 ({pos['type']})")
                return

        # ── 포지션 보유 중: 손절 / 대안 C 트레일링 스탑 감시 ──
        if pos_key in self.futures_positions:
            if not self.futures_positions[pos_key].get('is_exiting', False):
                pos = self.futures_positions[pos_key]
                entry = self.futures_day_entry_price
                if entry <= 0:
                    # (2026-07-27 추가) 청산 주문이 체결 확인 전에 futures_day_entry_price를
                    # 낙관적으로 0으로 초기화하는 20여 곳의 기존 호출부(장마감 강제청산 포함) 때문에,
                    # 주문이 15초 내 미체결로 끝나면 포지션은 self.futures_positions에 그대로
                    # 남아있는데 이 아래 감시 블록 전체가 통째로 꺼져버리는 문제가 실측 확인됨
                    # (2026-07-27 15:44 장마감청산 미체결 → entry=0 → 샹들리에 트레일링 감시가
                    # 08:45 다음날 재시작 전까지 완전히 죽어있었음). pos['price']는 이 초기화의
                    # 영향을 받지 않고 원래 체결가를 그대로 들고 있으므로, 여기서 이걸로 복구해
                    # 미체결 재시도 구간에도 감시 공백이 생기지 않게 한다.
                    entry = pos.get('price', 0.0)
                if entry > 0:
                    strategy_type = getattr(self, "futures_strategy_type", "volatility_breakout")
                    is_kalman = (strategy_type == "kalman")
                    is_sar    = (strategy_type == "parabolic_sar")
                    is_bb     = (strategy_type == "bollinger_band")
                    is_chandelier = (strategy_type == "chandelier")

                    # 볼린저 밴드 실시간 업데이트 (모든 전략에서 통외로 쪽 추적)
                    import numpy as _np
                    self.bb_close_buf.append(current_price)
                    if len(self.bb_close_buf) > self.bb_window:
                        self.bb_close_buf = self.bb_close_buf[-self.bb_window:]
                    if len(self.bb_close_buf) >= self.bb_window:
                        _arr = _np.array(self.bb_close_buf)
                        _mid = _arr.mean()
                        _std = _arr.std(ddof=1)
                        self.bb_upper = _mid + self.bb_sigma * _std
                        self.bb_lower = _mid - self.bb_sigma * _std

                    if is_kalman:
                        # 진입 시점에 스냅샷해둔 std_error/ATR을 사용 — 보유 중에 라이브 값이 재추정되어
                        # 튀어도 손절/익절 기준이 흔들리지 않도록 고정 (무한루프 버그 수정, 2026-07-01)
                        c_std = getattr(self, "futures_day_entry_std_error", None) or getattr(self, "futures_std_error", 0.5)
                        # 변동성 비례 동적 손절 상한선 (Dynamic Cap) + 5분봉 표준편차 연동형 동적 손절 하한선 (Floor)
                        c_atr = getattr(self, "futures_day_entry_atr", None) or getattr(self, "futures_atr_14", 5.0)
                        sl_floor = max(1.5 * c_std, 2.0)
                        sl_limit = max(min(self.futures_kf_sl_mult * c_std, 1.2 * c_atr), sl_floor)
                        sl_limit = min(sl_limit, self._effective_sl_hard_cap(c_std))  # 절대적인 최대 손절폭 상한(고정 또는 동적 Hard Cap) 적용
                    elif is_sar:
                        # Parabolic SAR: ATR 기반 초기 손절
                        sl_limit = max(getattr(self, "futures_atr_14", 5.0) * 1.0, 2.0)
                    elif is_bb:
                        # 볼린저 밴드: ATR 데반 동적 손절 (SAR보다 서퍼)
                        sl_limit = max(getattr(self, "futures_atr_14", 5.0) * 1.2, 2.0)
                    elif is_chandelier:
                        # 샹들리에 청산은 진입 후 고점/저점 기준 자체 트레일링 폭으로 손익절을 통합 처리하므로
                        # 아래 공용 sl_limit 기반 손절검사를 쓰지 않음(무한대로 둬서 절대 발동하지 않게 함)
                        sl_limit = float('inf')
                    else:
                        sl_limit = self.futures_stop_loss_pt
                        
                    if pos['type'] == 'LONG':
                        # 최고가 추적 및 갱신
                        if current_price > self.futures_day_peak:
                            self.futures_day_peak = current_price
                            # (2026-07-20 추가) 재시작 시 트레일링 기준점이 유실되지 않도록, 유리한
                            # 방향으로 갱신될 때마다 즉시 디스크에 체크포인트
                            self.save_futures_exit_state()

                        pnl_pt = current_price - entry
                        max_pnl_pt = self.futures_day_peak - entry # 진입 후 도달한 최고 수익폭

                        # ── Parabolic SAR 전략 청산 ──
                        if is_sar:
                            # SAR 실시간 업데이트
                            if self.sar_bull:  # 상승장: SAR이 아래
                                self.sar_value = self.sar_value + self.sar_af * (self.sar_ep - self.sar_value)
                                self.sar_value = min(self.sar_value, self.futures_day_peak)
                                if current_price > self.sar_ep:
                                    self.sar_ep = current_price
                                    self.sar_af = min(self.sar_af + self.sar_af_step, self.sar_af_max)
                                # SAR 역전(청산) 체크
                                if current_price <= self.sar_value or pnl_pt <= -sl_limit:
                                    self.sar_bull = False
                                    realized_pnl = current_price - entry
                                    exit_reason_str = "SAR역전" if current_price > entry - sl_limit else "SAR손절"
                                    self.futures_day_consecutive_losses += (1 if realized_pnl < 0 else 0)
                                    print(f"[주간선물(SAR)] 🔄 LONG {exit_reason_str} 청산! 진입:{entry:.2f} SAR:{self.sar_value:.2f} 현재:{current_price:.2f} 손익:{realized_pnl:+.2f}pt")
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"🔄 <b>[주간선물(SAR) LONG {exit_reason_str}]</b> {realized_pnl:+.2f}pt | SAR:{self.sar_value:.2f} → 현재:{current_price:.2f}")
                                    return
                            else:  # SAR이 위에 있는데 LONG → 즉시 초기 손절
                                if pnl_pt <= -sl_limit:
                                    self.futures_day_consecutive_losses += 1
                                    print(f"[주간선물(SAR)] 🛑 LONG ATR손절! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"🛑 <b>[주간선물(SAR) LONG ATR손절]</b> {pnl_pt:+.2f}pt")
                                    return
                        # ── 볼린저 밴드 역추세 전략 청산 ──
                        elif is_bb:
                            bb_tp = self.bb_upper  # LONG 익절: 상단 밴드 터치
                            if pnl_pt <= -sl_limit:
                                self.futures_day_consecutive_losses += 1
                                # BB 전략은 3회 연속 손절 시에도 당일 거래정지(Circuit Breaker)를 생략합니다.
                                print(f"[주간선물(BB)] 🛑 LONG 손절! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt (SL:{sl_limit:.2f}pt)")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"🛑 <b>[주간선물(BB) LONG 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (SL:{sl_limit:.2f}pt)")
                                return
                            elif bb_tp > 0 and current_price >= bb_tp:
                                realized_pnl = current_price - entry
                                self.futures_day_consecutive_losses = 0
                                print(f"[주간선물(BB)] 🎯 LONG 볼린저 상단밴드 익절! 진입:{entry:.2f} 현재:{current_price:.2f} BB상단:{bb_tp:.2f}pt 수익:{realized_pnl:+.2f}pt")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[주간선물(BB) LONG 밴드 익절]</b> {realized_pnl:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (BB상단:{bb_tp:.2f})")
                                return
                            else:
                                # 트레일링 스탑 (폴백)
                                c_std_bb = getattr(self, "futures_std_error", 0.5)
                                if (max_pnl_pt >= 1.5 * c_std_bb) and (current_price <= self.futures_day_peak - 0.5 * c_std_bb):
                                    realized_pnl = current_price - entry
                                    peak_snap = self.futures_day_peak
                                    self.futures_day_consecutive_losses = 0
                                    print(f"[주간선물(BB)] 💎 LONG 트레일링 스탑! 피크:{peak_snap:.2f} 현재:{current_price:.2f} 수익:{realized_pnl:+.2f}pt")
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"💎 <b>[주간선물(BB) LONG 트레일링]</b> {realized_pnl:+.2f}pt | 피크:{peak_snap:.2f} → 현재:{current_price:.2f}")
                                    return
                        # ── 샹들리에 청산 전략 (2026-07-15 도입) ──
                        elif is_chandelier:
                            dist = min(self.futures_chandelier_mult * getattr(self, "futures_atr_14", 5.0), self.futures_chandelier_hard_cap)
                            dist = self._apply_session_range_cap(dist)
                            dist, _pl_floor = self._apply_profit_lock(dist, entry, self.futures_day_peak, True)
                            stop_price = self.futures_day_peak - dist
                            if _pl_floor is not None:
                                stop_price = max(stop_price, _pl_floor)
                            _ts_fire = self._day_time_stop_fire(entry, True)
                            if current_price <= stop_price or _ts_fire:
                                if _ts_fire:
                                    print(f"[주간선물(샹들리에)] ⏱️ 타임스톱 LONG 청산 (진입 {self.futures_time_stop_minutes:.0f}분 경과, MFE<{self.futures_time_stop_mfe_pt:.0f}pt)")
                                realized_pnl = current_price - entry
                                peak_snapshot = self.futures_day_peak
                                if realized_pnl < 0:
                                    self.futures_day_consecutive_losses += 1
                                    if self.futures_day_consecutive_losses >= self.futures_consecutive_loss_limit:
                                        self.futures_day_trade_count = self.futures_max_trades_day
                                        if notifier:
                                            notifier.send_message(f"🚨 <b>[주간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 주간 거래가 정지되었습니다.")
                                else:
                                    self.futures_day_consecutive_losses = 0
                                print(f"[주간선물(샹들리에)] 💎 LONG 청산! 진입:{entry:.2f} 최고가:{peak_snapshot:.2f} 현재:{current_price:.2f} (되돌림:{dist:.2f}pt) 손익:{realized_pnl:+.2f}pt")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"💎 <b>[주간선물(샹들리에) LONG 청산]</b> {realized_pnl:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (최고가:{peak_snapshot:.2f}, 되돌림:{dist:.2f}pt)")
                                return
                        # ── 기존 Kalman / 변동성돌파 청산 ──
                        else:
                        # 1. 고정 손절 감시
                         if pnl_pt <= -sl_limit:
                            print(f"[주간선물] 🛑 LONG 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt (SL:{sl_limit:.2f}pt)")
                            self.futures_day_consecutive_losses += 1
                            if self.futures_day_consecutive_losses >= self.futures_consecutive_loss_limit:
                                self.futures_day_trade_count = self.futures_max_trades_day
                                if notifier:
                                    notifier.send_message(f"🚨 <b>[주간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 주간 거래가 정지되었습니다.")
                            self.save_futures_exit_state()
                            self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                            self.futures_day_entry_price = 0.0
                            self.futures_day_peak = 0.0
                            if notifier:
                                notifier.send_message(f"🛑 <b>[주간선물 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f} (SL:{sl_limit:.2f}pt)")
                            return
                         
                         # 2. 익절 감시
                         if is_kalman:
                            tp_price = getattr(self, 'futures_day_entry_tp_price', 0.0)
                            if tp_price > 0 and current_price >= tp_price:
                                print(f"[주간선물(칼만)] 🎯 LONG 3-Sigma 익절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 목표가:{tp_price:.2f}pt")
                                if pnl_pt > 0:
                                    self.futures_day_consecutive_losses = 0
                                else:
                                    self.futures_day_consecutive_losses += 1
                                self.save_futures_exit_state()
                                self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0
                                self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[주간선물(칼만) 3-Sigma 익절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (목표가:{tp_price:.2f}pt)")
                                return
                            # 트레일링 스탑 적용
                            elif max_pnl_pt >= max(self.futures_kf_ts_trigger_mult * c_std, self.futures_kf_ts_min_rr_ratio * sl_limit):
                                # [개선] 수익 크기(변동성 대비 상대값)에 따라 콜백 비율을 계단식으로 축소 + 수익 확정 보증(Lock-In)
                                # (2026-07-15 수정) kf_ts_trigger_mult가 작으면 tier 발동폭이 잠금 하한(2.0/5.0pt)보다
                                # 작아져, 그 이익폭에 아직 도달하지 못한 상태에서 ts_price가 entry+lock으로 뛰어올라
                                # current_price와 자명하게 교차 -> 즉시 오청산되는 버그가 있었음. tier 발동폭 자체를
                                # 잠금폭 이상으로 하한을 둬서, lock이 걸릴 때는 항상 그 이익을 실제로 달성한 뒤이도록 보장.
                                ts_tier2 = max(4 * self.futures_kf_ts_trigger_mult * c_std, 5.0)
                                ts_tier1 = max(2 * self.futures_kf_ts_trigger_mult * c_std, 2.0)
                                active_cb_mult = self.futures_kf_ts_callback_mult
                                if max_pnl_pt >= ts_tier2:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.4  # 콜백 비율 60% 축소
                                elif max_pnl_pt >= ts_tier1:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.6  # 콜백 비율 40% 축소
                                # kf_ts_floor를 수수료/슬리피지 감안해 0.5pt로 하한 보장
                                ts_price = self.futures_day_peak - max(active_cb_mult * c_std, max(self.futures_kf_ts_floor, 0.5))
                                # 수익 확정 보증 규칙(Lock-In): 되돌림 폭과 무관하게 절대 최소 보장선을 둔다
                                if max_pnl_pt >= ts_tier2:
                                    ts_price = max(ts_price, entry + 5.0)
                                elif max_pnl_pt >= ts_tier1:
                                    ts_price = max(ts_price, entry + 2.0)
                                if current_price <= ts_price:
                                    realized_pnl = current_price - entry
                                    peak_snapshot = self.futures_day_peak
                                    print(f"[주간선물(칼만)] 💎 LONG 트레일링 스탑 청산! 최고수익:{max_pnl_pt:.2f}pt (피크:{peak_snapshot:.2f}) ➡️ 현재가:{current_price:.2f} (이익보전:{realized_pnl:+.2f}pt)")
                                    if realized_pnl > 0:
                                        self.futures_day_consecutive_losses = 0
                                    else:
                                        self.futures_day_consecutive_losses += 1
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0
                                    self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"💎 <b>[주간선물(칼만) 트레일링 익절]</b> {realized_pnl:+.2f}pt | 최고가:{peak_snapshot:.2f} ➡️ 현재가:{current_price:.2f} (TS 가동:{self.futures_kf_ts_trigger_mult}*std ➡️ 되돌림:max({self.futures_kf_ts_callback_mult}*std, {self.futures_kf_ts_floor}pt))")
                                    return
                         else:
                            if max_pnl_pt >= 3.0:
                                ts_price = self.futures_day_peak - 2.0
                                if current_price <= ts_price:
                                    realized_pnl = current_price - entry
                                    peak_snapshot = self.futures_day_peak
                                    print(f"[주간선물] 🎯 LONG 트레일링 스탑 발동! 최고가:{peak_snapshot:.2f} 현재가(청산):{current_price:.2f} 익절:{realized_pnl:+.2f}pt")
                                    self.futures_day_consecutive_losses = 0
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0
                                    self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"🎯 <b>[주간선물 트레일링 익절]</b> {realized_pnl:+.2f}pt (최고가:{peak_snapshot:.2f} ➡️ 청산:{current_price:.2f})")
                                    return

                    elif pos['type'] == 'SHORT':
                        # 최저가 추적 및 갱신
                        if current_price < self.futures_day_peak or self.futures_day_peak == 0:
                            self.futures_day_peak = current_price
                            # (2026-07-20 추가) 재시작 시 트레일링 기준점이 유실되지 않도록, 유리한
                            # 방향으로 갱신될 때마다 즉시 디스크에 체크포인트
                            self.save_futures_exit_state()

                        pnl_pt = entry - current_price
                        max_pnl_pt = entry - self.futures_day_peak

                        # ── Parabolic SAR 전략 청산 ──
                        if is_sar:
                            if not self.sar_bull:  # 하락장: SAR이 위
                                self.sar_value = self.sar_value - self.sar_af * (self.sar_value - self.sar_ep)
                                self.sar_value = max(self.sar_value, self.futures_day_peak)
                                if current_price < self.sar_ep:
                                    self.sar_ep = current_price
                                    self.sar_af = min(self.sar_af + self.sar_af_step, self.sar_af_max)
                                if current_price >= self.sar_value or pnl_pt <= -sl_limit:
                                    self.sar_bull = True
                                    realized_pnl = entry - current_price
                                    exit_reason_str = "SAR역전" if realized_pnl >= 0 else "SAR손절"
                                    self.futures_day_consecutive_losses += (1 if realized_pnl < 0 else 0)
                                    print(f"[주간선물(SAR)] 🔄 SHORT {exit_reason_str} 청산! 진입:{entry:.2f} SAR:{self.sar_value:.2f} 현재:{current_price:.2f} 손익:{realized_pnl:+.2f}pt")
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"🔄 <b>[주간선물(SAR) SHORT {exit_reason_str}]</b> {realized_pnl:+.2f}pt | SAR:{self.sar_value:.2f} → 현재:{current_price:.2f}")
                                    return
                            else:
                                if pnl_pt <= -sl_limit:
                                    self.futures_day_consecutive_losses += 1
                                    print(f"[주간선물(SAR)] 🛑 SHORT ATR손절! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt")
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"🛑 <b>[주간선물(SAR) SHORT ATR손절]</b> {pnl_pt:+.2f}pt")
                                    return
                        # ── 볼린저 밴드 역추세 전략 청산 ──
                        elif is_bb:
                            bb_tp = self.bb_lower  # SHORT 익절: 하단 밴드 터치
                            if pnl_pt <= -sl_limit:
                                self.futures_day_consecutive_losses += 1
                                # BB 전략은 3회 연속 손절 시에도 당일 거래정지(Circuit Breaker)를 생략합니다.
                                print(f"[주간선물(BB)] 🛑 SHORT 손절! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt (SL:{sl_limit:.2f}pt)")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"🛑 <b>[주간선물(BB) SHORT 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (SL:{sl_limit:.2f}pt)")
                                return
                            elif bb_tp > 0 and current_price <= bb_tp:
                                realized_pnl = entry - current_price
                                self.futures_day_consecutive_losses = 0
                                print(f"[주간선물(BB)] 🎯 SHORT 볼린저 하단밴드 익절! 진입:{entry:.2f} 현재:{current_price:.2f} BB하단:{bb_tp:.2f}pt 수익:{realized_pnl:+.2f}pt")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[주간선물(BB) SHORT 밴드 익절]</b> {realized_pnl:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (BB하단:{bb_tp:.2f})")
                                return
                            else:
                                c_std_bb = getattr(self, "futures_std_error", 0.5)
                                if (max_pnl_pt >= 1.5 * c_std_bb) and (current_price >= self.futures_day_peak + 0.5 * c_std_bb):
                                    realized_pnl = entry - current_price
                                    peak_snap = self.futures_day_peak
                                    self.futures_day_consecutive_losses = 0
                                    print(f"[주간선물(BB)] 💎 SHORT 트레일링 스탑! 피크:{peak_snap:.2f} 현재:{current_price:.2f} 수익:{realized_pnl:+.2f}pt")
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"💎 <b>[주간선물(BB) SHORT 트레일링]</b> {realized_pnl:+.2f}pt | 피크:{peak_snap:.2f} → 현재:{current_price:.2f}")
                                    return
                        # ── 샹들리에 청산 전략 (2026-07-15 도입) ──
                        elif is_chandelier:
                            dist = min(self.futures_chandelier_mult * getattr(self, "futures_atr_14", 5.0), self.futures_chandelier_hard_cap)
                            dist = self._apply_session_range_cap(dist)
                            dist, _pl_floor = self._apply_profit_lock(dist, entry, self.futures_day_peak, False)
                            stop_price = self.futures_day_peak + dist
                            if _pl_floor is not None:
                                stop_price = min(stop_price, _pl_floor)
                            _ts_fire = self._day_time_stop_fire(entry, False)
                            if current_price >= stop_price or _ts_fire:
                                if _ts_fire:
                                    print(f"[주간선물(샹들리에)] ⏱️ 타임스톱 SHORT 청산 (진입 {self.futures_time_stop_minutes:.0f}분 경과, MFE<{self.futures_time_stop_mfe_pt:.0f}pt)")
                                realized_pnl = entry - current_price
                                peak_snapshot = self.futures_day_peak
                                if realized_pnl < 0:
                                    self.futures_day_consecutive_losses += 1
                                    if self.futures_day_consecutive_losses >= self.futures_consecutive_loss_limit:
                                        self.futures_day_trade_count = self.futures_max_trades_day
                                        if notifier:
                                            notifier.send_message(f"🚨 <b>[주간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 주간 거래가 정지되었습니다.")
                                else:
                                    self.futures_day_consecutive_losses = 0
                                print(f"[주간선물(샹들리에)] 💎 SHORT 청산! 진입:{entry:.2f} 최저가:{peak_snapshot:.2f} 현재:{current_price:.2f} (되돌림:{dist:.2f}pt) 손익:{realized_pnl:+.2f}pt")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0; self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"💎 <b>[주간선물(샹들리에) SHORT 청산]</b> {realized_pnl:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (최저가:{peak_snapshot:.2f}, 되돌림:{dist:.2f}pt)")
                                return
                        # ── 기존 Kalman / 변동성돌파 청산 ──
                        else:
                        # 1. 고정 손절 감시
                         if pnl_pt <= -sl_limit:
                            print(f"[주간선물] 🛑 SHORT 손절 발동! 진입:{entry:.2f} 현재:{current_price:.2f} 손실:{pnl_pt:+.2f}pt (SL:{sl_limit:.2f}pt)")
                            self.futures_day_consecutive_losses += 1
                            if self.futures_day_consecutive_losses >= self.futures_consecutive_loss_limit:
                                self.futures_day_trade_count = self.futures_max_trades_day
                                if notifier:
                                    notifier.send_message(f"🚨 <b>[주간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 주간 거래가 정지되었습니다.")
                            self.save_futures_exit_state()
                            self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                            self.futures_day_entry_price = 0.0
                            self.futures_day_peak = 0.0
                            if notifier:
                                notifier.send_message(f"🛑 <b>[주간선물 손절]</b> {pnl_pt:+.2f}pt | 진입:{entry:.2f} → 청산:{current_price:.2f} (SL:{sl_limit:.2f}pt)")
                            return
                         
                         # 2. 익절 감시
                         if is_kalman:
                            tp_price = getattr(self, 'futures_day_entry_tp_price', 0.0)
                            if tp_price > 0 and current_price <= tp_price:
                                print(f"[주간선물(칼만)] 🎯 SHORT 3-Sigma 익절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 목표가:{tp_price:.2f}pt")
                                if pnl_pt > 0:
                                    self.futures_day_consecutive_losses = 0
                                else:
                                    self.futures_day_consecutive_losses += 1
                                self.save_futures_exit_state()
                                self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                self.futures_day_entry_price = 0.0
                                self.futures_day_peak = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[주간선물(칼만) 3-Sigma 익절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (목표가:{tp_price:.2f}pt)")
                                return
                            # 트레일링 스탑 적용
                            elif max_pnl_pt >= max(self.futures_kf_ts_trigger_mult * c_std, self.futures_kf_ts_min_rr_ratio * sl_limit):
                                # [개선] 수익 크기(변동성 대비 상대값)에 따라 콜백 비율을 계단식으로 축소 + 수익 확정 보증(Lock-In)
                                # (2026-07-15 수정) kf_ts_trigger_mult가 작으면 tier 발동폭이 잠금 하한(2.0/5.0pt)보다
                                # 작아져, 그 이익폭에 아직 도달하지 못한 상태에서 ts_price가 entry+lock으로 뛰어올라
                                # current_price와 자명하게 교차 -> 즉시 오청산되는 버그가 있었음. tier 발동폭 자체를
                                # 잠금폭 이상으로 하한을 둬서, lock이 걸릴 때는 항상 그 이익을 실제로 달성한 뒤이도록 보장.
                                ts_tier2 = max(4 * self.futures_kf_ts_trigger_mult * c_std, 5.0)
                                ts_tier1 = max(2 * self.futures_kf_ts_trigger_mult * c_std, 2.0)
                                active_cb_mult = self.futures_kf_ts_callback_mult
                                if max_pnl_pt >= ts_tier2:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.4  # 콜백 비율 60% 축소
                                elif max_pnl_pt >= ts_tier1:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.6  # 콜백 비율 40% 축소
                                # kf_ts_floor를 수수료/슬리피지 감안해 0.5pt로 하한 보장
                                ts_price = self.futures_day_peak + max(active_cb_mult * c_std, max(self.futures_kf_ts_floor, 0.5))
                                # 수익 확정 보증 규칙(Lock-In): SHORT는 가격이 낮을수록 유리하므로 min()으로 더 낮은 쪽을 채택
                                if max_pnl_pt >= ts_tier2:
                                    ts_price = min(ts_price, entry - 5.0)
                                elif max_pnl_pt >= ts_tier1:
                                    ts_price = min(ts_price, entry - 2.0)
                                if current_price >= ts_price:
                                    realized_pnl = entry - current_price
                                    peak_snapshot = self.futures_day_peak
                                    print(f"[주간선물(칼만)] 💎 SHORT 트레일링 스탑 청산! 최고수익:{max_pnl_pt:.2f}pt (피크:{peak_snapshot:.2f}) ➡️ 현재가:{current_price:.2f} (이익보전:{realized_pnl:+.2f}pt)")
                                    if realized_pnl > 0:
                                        self.futures_day_consecutive_losses = 0
                                    else:
                                        self.futures_day_consecutive_losses += 1
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0
                                    self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"💎 <b>[주간선물(칼만) 트레일링 익절]</b> {realized_pnl:+.2f}pt | 최저가:{peak_snapshot:.2f} ➡️ 현재가:{current_price:.2f} (TS 가동:{self.futures_kf_ts_trigger_mult}*std ➡️ 되돌림:max({self.futures_kf_ts_callback_mult}*std, {self.futures_kf_ts_floor}pt))")
                                    return
                         else:
                            if max_pnl_pt >= 3.0:
                                ts_price = self.futures_day_peak + 2.0
                                if current_price >= ts_price:
                                    realized_pnl = entry - current_price
                                    peak_snapshot = self.futures_day_peak
                                    print(f"[주간선물] 🎯 SHORT 트레일링 스탑 발동! 최저가:{peak_snapshot:.2f} 현재가(청산):{current_price:.2f} 익절:{realized_pnl:+.2f}pt")
                                    self.futures_day_consecutive_losses = 0
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                    self.futures_day_entry_price = 0.0
                                    self.futures_day_peak = 0.0
                                    if notifier:
                                        notifier.send_message(f"🎯 <b>[주간선물 트레일링 익절]</b> {realized_pnl:+.2f}pt (최저가:{peak_snapshot:.2f} ➡️ 청산:{current_price:.2f})")
                                    return
            return  # 포지션 보유 중이면 (체결 대기 상태 포함) 신규 진입 불가

        # ── 신규 진입 조건 (09:00 장 초반 15분 노이즈 필터 연동) ──
        if not self.futures_order_locked and not self.system_halted:
            # [서킷브레이커 최종 안전장치] 전략 종류와 무관하게 일일 총 거래횟수 하드캡 초과 시 신규 진입 전면 차단
            if self.futures_day_trade_count >= self.futures_day_max_trades_hard_cap:
                return
            # Parabolic SAR / Bollinger Band / Kalman 전략은 거래 횟수 제한 제외 (무제한)
            is_unlimited = getattr(self, "futures_strategy_type", "") in ["parabolic_sar", "bollinger_band", "kalman", "chandelier"]
            if not is_unlimited and self.futures_day_trade_count >= self.futures_max_trades_day:
                return
            # 칼만/샹들리에는 무제한 거래 중에도 연속 손실 N회 시 즉시 당일 거래 정지
            is_kalman = (getattr(self, "futures_strategy_type", "") == "kalman")
            is_chandelier = (getattr(self, "futures_strategy_type", "") == "chandelier")
            if (is_kalman or is_chandelier) and getattr(self, "futures_day_consecutive_losses", 0) >= self.futures_consecutive_loss_limit:
                return
            # (2026-07-21 추가) 09:00 정각 개장은 실시간 시세 폭주 + 통신 끊김 하드 리셋이
            # 반복 재현되는 구간(2026-07-14/15/16/20/21 확인)이자, KIS 야간데이터 병합이 구조적으로
            # 불가능한 월요일 등에는 KF 기준선이 실제 개장가와 크게 괴리되는 구간이기도 함
            # (2026-07-20 39pt 오차 사례). 09:10부터 신규 진입을 허용해 그 노이즈 구간을 피한다 —
            # 기존 포지션의 청산/트레일링 로직(위쪽)은 이 게이트와 무관하게 계속 정상 동작함.
            is_after_910 = (now.hour == 9 and now.minute >= 10) or (now.hour > 9)

            # (2026-08-03) 진입 종료 게이트 — 위 09:10 시작 게이트의 반대편 짝.
            # 15:35~15:45 장마감 무조건청산이 도입된 뒤로 늦은 진입은 트레일링이 작동할
            # 시간 자체가 없어 강제청산으로 끝나는 비율이 압도적으로 높다(15시 이후 진입의
            # 57.8%가 강제청산, 전체 평균은 5.8%). 더 결정적으로, 이 시스템 최대 꼬리위험인
            # 2026-06-11 거래정지 갭 사고(-117.98pt, 익일 08:45까지 방치)가 15:05 진입
            # 건이었다 — session_range_cap도 EOD 무조건청산도 막지 못하는 구간이라
            # 진입 자체를 막는 것이 유일한 방어다. 게다가 2026-08-03에 그 EOD 강제청산이
            # 실제로 실패해(15:42 청산 미확인 → 오버나잇 방치) 의존도를 줄일 이유가 하나 더 늘었다.
            # 백테스트(31,277봉, 현실 비용): 최악손실 -118.14 → -26.32pt, 최근60일 MDD
            # 12.79% → 0.89%, PF 21.59 → 25.88. 대가는 전체 최종자본 -2.7%(-3.6억).
            # 상세: 선물_15시진입차단_백테스트_20260803.md
            # 기존 포지션의 청산/트레일링은 위쪽에서 이미 처리되므로 이 게이트와 무관하게 계속 동작한다.
            _entry_end_h = getattr(self, "futures_entry_end_hour", None)
            if _entry_end_h is not None:
                _entry_end_m = getattr(self, "futures_entry_end_minute", 0)
                if (now.hour, now.minute) >= (_entry_end_h, _entry_end_m):
                    return

            if is_after_910:
                # [AMATS 최적화] 초저변동성 구간 진입 차단 필터링 (ATR Cutoff)
                atr_val = getattr(self, 'futures_atr_14', 2.0)
                if atr_val < self.futures_atr_cutoff:
                    self._note_entry_block("ATR컷오프", f"ATR {atr_val:.2f} < {self.futures_atr_cutoff:.2f}")
                    return
                # 저변동성(std_error) 진입 필터 — 3*std_error 익절목표가 손절플로어보다 작아지는
                # 국면 자체를 회피 (2026-07-09 백테스트 검증 도입, 기본 0.0이면 비활성)
                _se = getattr(self, 'futures_std_error', 0.5)
                if _se < self.futures_min_std_error_entry:
                    self._note_entry_block("std_error", f"std_error {_se:.2f} < {self.futures_min_std_error_entry:.2f}")
                    return
                # 게이트를 다 통과했으면 연속 카운트를 푼다 (막힘이 지속될 때만 알리기 위함)
                if getattr(self, "_entry_block_counts", None):
                    self._entry_block_counts.clear()

                # 장기 칼만 필터 추세 필터링 (Proposal 3) — 샹들리에도 백테스트와 동일하게 추세필터 적용
                is_kalman = (getattr(self, "futures_strategy_type", "volatility_breakout") == "kalman")
                is_chandelier = (getattr(self, "futures_strategy_type", "") == "chandelier")
                use_trend_filter = is_kalman or is_chandelier
                trend = getattr(self, "futures_trend_direction", "NEUTRAL") if use_trend_filter else "NEUTRAL"

                if current_price >= self.futures_target_long:
                    # 장기 추세가 하락세(DOWN)일 때 LONG 진입 무시
                    if use_trend_filter and trend == "DOWN":
                        return
                    # (2026-07-30) 레짐필터: 켜져 있으면 추세가 확실히 상승(UP)일 때만 LONG 허용(NEUTRAL 차단)
                    if is_chandelier and getattr(self, "futures_regime_filter_enabled", False) and trend != "UP":
                        return
                    # 칼만 필터인 경우 이미 익절 타겟을 초과했으면 진입 금지 (무한 루프 방지)
                    if is_kalman and self.futures_tp_price_long > 0 and current_price >= self.futures_tp_price_long:
                        return
                    if not self.enable_reentry_filter or self._is_reentry_allowed("LONG", current_price, is_night=False):
                            # 볼린저 밴드 결합 필터링
                            if getattr(self, "futures_strategy_type", "") == "parabolic_sar" and self.current_bb_mid > 0:
                                if self.current_bb_bandwidth < self.current_bb_squeeze_limit:
                                    print(f"[주간선물(SAR)] 🚫 LONG 진입 차단 (Squeeze 수축 구간): 밴드폭={self.current_bb_bandwidth*100:.2f}% (임계={self.current_bb_squeeze_limit*100:.2f}%)")
                                    return
                                if current_price <= self.current_bb_mid:
                                    print(f"[주간선물(SAR)] 🚫 LONG 진입 차단 (방향성 부적합): 현재가 {current_price:.2f} <= BB 중심선 {self.current_bb_mid:.2f}")
                                    return
                            
                            self.futures_day_entry_price = current_price
                            import time as _t_ent; self.futures_day_entry_time = _t_ent.time()  # 타임스톱 기준
                            self.futures_day_peak = current_price # 진입 즉시 초기화
                            # 진입 시점의 std_error/ATR/3-Sigma 목표가를 스냅샷 — 보유 중 매 5분봉마다
                            # 칼만 필터가 재추정되며 이 값들이 크게 튀면(재시딩 특성상 발생 가능), 라이브 값을
                            # 그대로 쓸 경우 진입 직후 "이미 3-Sigma 도달"로 오판해 즉시 재청산되는 무한루프
                            # 버그가 있었음(2026-07-01 실측: 4,243건 체결/6,122건 텔레그램 429 발생 사고).
                            # 보유 중인 포지션의 손절/익절 기준은 진입 시점 값으로 고정해야 안전함.
                            self.futures_day_entry_std_error = getattr(self, "futures_std_error", 0.5)
                            self.futures_day_entry_atr = getattr(self, "futures_atr_14", 5.0)
                            self.futures_day_entry_tp_price = getattr(self, "futures_tp_price_long", 0.0)
                            # 추세 확인(LONG인데 trend=UP) 시에만 3-Sigma 목표가를 더 멀리 확대.
                            # 진입 시점에 한 번만 계산해 스냅샷 — 위 무한루프 버그와 동일한 이유로
                            # 보유 중 매 틱마다 재계산하면 안 됨. (2026-07-11 백테스트 검증: trend_tp_sigma_mult=10)
                            trend_tp_mult = getattr(self, "futures_trend_tp_sigma_mult", None)
                            if trend_tp_mult is not None and is_kalman and trend == "UP":
                                self.futures_day_entry_tp_price += (trend_tp_mult - self.futures_kf_tp_sigma_mult) * self.futures_day_entry_std_error
                            # Parabolic SAR 진입 초기화
                            if getattr(self, "futures_strategy_type", "") == "parabolic_sar":
                                self.sar_value = current_price - getattr(self, 'futures_atr_14', 5.0)
                                self.sar_ep    = current_price
                                self.sar_af    = self.sar_af_init
                                self.sar_bull  = True
                                print(f"[주간선물(SAR)] 🚀 LONG 진입 SAR 초기화: SAR={self.sar_value:.2f} EP={self.sar_ep:.2f} AF={self.sar_af}")
                            self.futures_day_trade_count += 1
                            if self.futures_day_trade_count >= self.futures_day_max_trades_hard_cap and notifier:
                                notifier.send_message(f"🚨 <b>[주간선물 거래정지 - 하드캡]</b>\n금일 총 거래횟수가 {self.futures_day_trade_count}회로 하드캡({self.futures_day_max_trades_hard_cap}회)에 도달하여 신규 진입이 정지되었습니다.\n(승패와 무관한 총 거래횟수 기준 최종 안전장치)")
                            self.save_futures_exit_state()
                            self._execute_futures_direct("LONG_ENTER", current_price, code, pos_key)
                elif current_price <= self.futures_target_short:
                    # 장기 추세가 상승세(UP)일 때 SHORT 진입 무시
                    if use_trend_filter and trend == "UP":
                        return
                    # (2026-07-30) 레짐필터: 켜져 있으면 추세가 확실히 하락(DOWN)일 때만 SHORT 허용(NEUTRAL 차단)
                    if is_chandelier and getattr(self, "futures_regime_filter_enabled", False) and trend != "DOWN":
                        return
                    # 칼만 필터인 경우 이미 익절 타겟을 초과했으면 진입 금지 (무한 루프 방지)
                    if is_kalman and self.futures_tp_price_short > 0 and current_price <= self.futures_tp_price_short:
                        return
                    if not self.enable_reentry_filter or self._is_reentry_allowed("SHORT", current_price, is_night=False):
                            # 볼린저 밴드 결합 필터링
                            if getattr(self, "futures_strategy_type", "") == "parabolic_sar" and self.current_bb_mid > 0:
                                if self.current_bb_bandwidth < self.current_bb_squeeze_limit:
                                    print(f"[주간선물(SAR)] 🚫 SHORT 진입 차단 (Squeeze 수축 구간): 밴드폭={self.current_bb_bandwidth*100:.2f}% (임계={self.current_bb_squeeze_limit*100:.2f}%)")
                                    return
                                if current_price >= self.current_bb_mid:
                                    print(f"[주간선물(SAR)] 🚫 SHORT 진입 차단 (방향성 부적합): 현재가 {current_price:.2f} >= BB 중심선 {self.current_bb_mid:.2f}")
                                    return
                            
                            self.futures_day_entry_price = current_price
                            import time as _t_ent; self.futures_day_entry_time = _t_ent.time()  # 타임스톱 기준
                            self.futures_day_peak = current_price # 진입 즉시 초기화
                            # 진입 시점의 std_error/ATR/3-Sigma 목표가를 스냅샷 (LONG 쪽과 동일한 이유 — 위 주석 참조)
                            self.futures_day_entry_std_error = getattr(self, "futures_std_error", 0.5)
                            self.futures_day_entry_atr = getattr(self, "futures_atr_14", 5.0)
                            self.futures_day_entry_tp_price = getattr(self, "futures_tp_price_short", 0.0)
                            # 추세 확인(SHORT인데 trend=DOWN) 시에만 3-Sigma 목표가를 더 멀리 확대(LONG과 동일 원리)
                            trend_tp_mult = getattr(self, "futures_trend_tp_sigma_mult", None)
                            if trend_tp_mult is not None and is_kalman and trend == "DOWN":
                                self.futures_day_entry_tp_price -= (trend_tp_mult - self.futures_kf_tp_sigma_mult) * self.futures_day_entry_std_error
                            # Parabolic SAR 진입 초기화
                            if getattr(self, "futures_strategy_type", "") == "parabolic_sar":
                                self.sar_value = current_price + getattr(self, 'futures_atr_14', 5.0)
                                self.sar_ep    = current_price
                                self.sar_af    = self.sar_af_init
                                self.sar_bull  = False
                                print(f"[주간선물(SAR)] 🚀 SHORT 진입 SAR 초기화: SAR={self.sar_value:.2f} EP={self.sar_ep:.2f} AF={self.sar_af}")
                            self.futures_day_trade_count += 1
                            if self.futures_day_trade_count >= self.futures_day_max_trades_hard_cap and notifier:
                                notifier.send_message(f"🚨 <b>[주간선물 거래정지 - 하드캡]</b>\n금일 총 거래횟수가 {self.futures_day_trade_count}회로 하드캡({self.futures_day_max_trades_hard_cap}회)에 도달하여 신규 진입이 정지되었습니다.\n(승패와 무관한 총 거래횟수 기준 최종 안전장치)")
                            self.save_futures_exit_state()
                            self._execute_futures_direct("SHORT_ENTER", current_price, code, pos_key)

    def _process_night_tick(self, code, current_price, now):
        """야간 선물 전략 (18:00 진입 → 익일 04:45 청산, config.json futures_settings 고정 SL/TP)"""
        pos_key = "KOSPI200_NIGHT"

        # 04:45~04:55 야간장 마감 전 강제 청산
        if now.hour == 4 and 45 <= now.minute <= 55:
            if pos_key in self.futures_positions and not self.futures_positions[pos_key].get('is_exiting', False):
                pos = self.futures_positions[pos_key]
                print(f"[야간선물] ⏰ 04:45 시간 청산 실행")
                self._execute_futures_direct("LONG_EXIT" if pos["type"] == "LONG" else "SHORT_EXIT",
                                             current_price, code, pos_key)
                self.futures_night_entry_price = 0.0
            return

        # 18:00 ~ 새벽 04:45 사이 야간 세션 중도 기동 시에도 즉시 야간 시초가 및 목표가 동적 생성
        is_night_session = (now.hour >= 18) or (now.hour < 5)
        if is_night_session and self.futures_night_open == 0:
            # 늦은 기동 시 실제 야간 시초가 DB 조회 (없으면 현재가 폴백)
            db_night_open = self._get_today_night_open(code, now)
            night_open = db_night_open if db_night_open > 0 else current_price
            self.futures_night_open         = night_open
            
            # 칼만 필터/샹들리에 전략인 경우 타점을 덮어쓰지 않고 필요시 초기화 (둘 다 켈만밴드 돌파 타점을 공유)
            if getattr(self, "futures_strategy_type", "volatility_breakout") in ("kalman", "chandelier"):
                if self.futures_night_target_long == float('inf') or self.futures_night_target_short == float('-inf'):
                    self.update_kalman_targets(code)
                src_label = "DB 시초가" if db_night_open > 0 else "현재가(폴백)"
                print(f"\n[야간선물(칼만)] ✅ 시초가 설정: {night_open:.2f}pt ({src_label})")
                print(f"  LONG목표: {self.futures_night_target_long:.2f}  SHORT목표: {self.futures_night_target_short:.2f}")
                print(f"  손절: 하이브리드 동적 ({self.futures_kf_sl_mult:.2f}배) | 익절: 동적 3-Sigma")
                if notifier:
                    notifier.send_message(
                        f"🌙 <b>[야간선물(칼만) 목표가]</b>\n"
                        f"• 시초가: {night_open:.2f}pt ({src_label})\n"
                        f"• LONG ▲ {self.futures_night_target_long:.2f}pt (3Sig TP: {getattr(self, 'futures_night_tp_price_long', 0.0):.2f}pt)\n"
                        f"• SHORT ▼ {self.futures_night_target_short:.2f}pt (3Sig TP: {getattr(self, 'futures_night_tp_price_short', 0.0):.2f}pt)\n"
                        f"• 손절: 하이브리드 동적 ({self.futures_kf_sl_mult:.2f}배) | 익절: 동적 3-Sigma"
                    )
            else:
                self.futures_night_target_long  = night_open + self.futures_prev_range * self.futures_best_k
                self.futures_night_target_short = night_open - self.futures_prev_range * self.futures_best_k
                # 주간(4095행)과 같은 이유 — 진입 게이트가 참조하는 night_std_error를 채운다.
                if getattr(self, "futures_strategy_type", "") == "parabolic_sar":
                    self.update_kalman_targets(code, std_error_only=True)
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

            if getattr(self, "futures_atr_14", 2.0) == 2.0 and not getattr(self, "futures_atr_14_updated_at", None):
                print("[ERA 야간선물] ⚠️ ATR14가 초기값(2.0pt)에 머물러 있어 장 시작 전 재계산을 재시도합니다.")
                self.update_futures_dynamic_sl_tp()

        if self.futures_night_open == 0:
            return

        # ── 포지션 보유 중: 손절/익절 감시 ──
        if pos_key in self.futures_positions:
            if not self.futures_positions[pos_key].get('is_exiting', False):
                pos = self.futures_positions[pos_key]
                entry = self.futures_night_entry_price
                if entry > 0:
                    is_kalman = (getattr(self, "futures_strategy_type", "volatility_breakout") == "kalman")
                    is_chandelier = (getattr(self, "futures_strategy_type", "") == "chandelier")
                    if is_kalman:
                        # 진입 시점에 스냅샷해둔 std_error/ATR을 사용 (주간과 동일한 이유 — 무한루프 버그 수정)
                        c_std = getattr(self, "futures_night_entry_std_error", None) or getattr(self, "futures_night_std_error", 0.5)
                        # 변동성 비례 동적 손절 상한선 (Dynamic Cap) + 5분봉 표준편차 연동형 동적 손절 하한선 (Floor)
                        c_atr = getattr(self, "futures_night_entry_atr", None) or getattr(self, "futures_atr_14", 5.0)
                        sl_floor = max(1.5 * c_std, 2.0)
                        sl_limit = max(min(self.futures_kf_sl_mult * c_std, 1.2 * c_atr), sl_floor)
                        sl_limit = min(sl_limit, self._effective_sl_hard_cap(c_std))  # 절대적인 최대 손절폭 상한(고정 또는 동적 Hard Cap) 적용
                    elif is_chandelier:
                        # 샹들리에 청산은 진입 후 고점/저점 기준 자체 트레일링 폭으로 손익절을 통합 처리
                        sl_limit = float('inf')
                    else:
                        sl_limit = self.futures_night_stop_loss_pt
                        
                    if pos['type'] == 'LONG':
                        # 최고가 추적 및 갱신
                        if current_price > self.futures_night_peak:
                            self.futures_night_peak = current_price
                            # (2026-07-20 추가) 재시작 시 트레일링 기준점이 유실되지 않도록, 유리한
                            # 방향으로 갱신될 때마다 즉시 디스크에 체크포인트
                            self.save_futures_exit_state()

                        pnl_pt = current_price - entry
                        max_pnl_pt = self.futures_night_peak - entry
                        
                        # 1. 손절 검사
                        if pnl_pt <= -sl_limit:
                            print(f"[야간선물] 🚨 LONG 손절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 손익:{pnl_pt:+.2f}pt (SL:{sl_limit:.2f}pt)")
                            self.futures_night_consecutive_losses += 1
                            if self.futures_night_consecutive_losses >= self.futures_consecutive_loss_limit:
                                self.futures_night_trade_count = self.futures_max_trades_night
                                if notifier:
                                    notifier.send_message(f"🚨 <b>[야간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 야간 거래가 정지되었습니다.")
                            self.save_futures_exit_state()
                            self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                            self.futures_night_entry_price = 0.0
                            if notifier:
                                notifier.send_message(f"🚨 <b>[야간선물 손절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (SL:{sl_limit:.2f}pt)")
                            return
                        
                        # 2. 익절 검사
                        if is_kalman:
                            tp_price = getattr(self, 'futures_night_entry_tp_price', 0.0)
                            if tp_price > 0 and current_price >= tp_price:
                                print(f"[야간선물(칼만)] 🎯 LONG 3-Sigma 익절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 목표가:{tp_price:.2f}pt")
                                if pnl_pt > 0:
                                    self.futures_night_consecutive_losses = 0
                                else:
                                    self.futures_night_consecutive_losses += 1
                                self.save_futures_exit_state()
                                self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                self.futures_night_entry_price = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[야간선물(칼만) 3-Sigma 익절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (목표가:{tp_price:.2f}pt)")
                                return
                            # 트레일링 스탑 적용 (주간과 동일한 계단식 Lock-In, 2026-07-10 야간 이식)
                            elif max_pnl_pt >= max(self.futures_kf_ts_trigger_mult * c_std, self.futures_kf_ts_min_rr_ratio * sl_limit):
                                # (2026-07-15 수정) kf_ts_trigger_mult가 작으면 tier 발동폭이 잠금 하한(2.0/5.0pt)보다
                                # 작아져, 그 이익폭에 아직 도달하지 못한 상태에서 ts_price가 entry+lock으로 뛰어올라
                                # current_price와 자명하게 교차 -> 즉시 오청산되는 버그가 있었음. tier 발동폭 자체를
                                # 잠금폭 이상으로 하한을 둬서, lock이 걸릴 때는 항상 그 이익을 실제로 달성한 뒤이도록 보장.
                                ts_tier2 = max(4 * self.futures_kf_ts_trigger_mult * c_std, 5.0)
                                ts_tier1 = max(2 * self.futures_kf_ts_trigger_mult * c_std, 2.0)
                                active_cb_mult = self.futures_kf_ts_callback_mult
                                if max_pnl_pt >= ts_tier2:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.4  # 콜백 비율 60% 축소
                                elif max_pnl_pt >= ts_tier1:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.6  # 콜백 비율 40% 축소
                                # kf_ts_floor를 수수료/슬리피지 감안해 0.5pt로 하한 보장 (주간과 동일)
                                ts_price = self.futures_night_peak - max(active_cb_mult * c_std, max(self.futures_kf_ts_floor, 0.5))
                                # 수익 확정 보증 규칙(Lock-In): 되돌림 폭과 무관하게 절대 최소 보장선을 둔다
                                if max_pnl_pt >= ts_tier2:
                                    ts_price = max(ts_price, entry + 5.0)
                                elif max_pnl_pt >= ts_tier1:
                                    ts_price = max(ts_price, entry + 2.0)
                                if current_price <= ts_price:
                                    realized_pnl = current_price - entry
                                    peak_snapshot = self.futures_night_peak
                                    print(f"[야간선물(칼만)] 💎 LONG 트레일링 스탑 청산! 최고수익:{max_pnl_pt:.2f}pt (피크:{peak_snapshot:.2f}) ➡️ 현재가:{current_price:.2f} (이익보전:{realized_pnl:+.2f}pt)")
                                    if realized_pnl > 0:
                                        self.futures_night_consecutive_losses = 0
                                    else:
                                        self.futures_night_consecutive_losses += 1
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                    self.futures_night_entry_price = 0.0
                                    if notifier:
                                        notifier.send_message(f"💎 <b>[야간선물(칼만) 트레일링 익절]</b> {realized_pnl:+.2f}pt | 최고가:{peak_snapshot:.2f} ➡️ 현재가:{current_price:.2f} (TS 가동:{self.futures_kf_ts_trigger_mult}*std ➡️ 되돌림:max({self.futures_kf_ts_callback_mult}*std, {self.futures_kf_ts_floor}pt))")
                                    return
                        elif is_chandelier:
                            dist = min(self.futures_chandelier_mult * getattr(self, "futures_atr_14", 5.0), self.futures_chandelier_hard_cap)
                            stop_price = self.futures_night_peak - dist
                            if current_price <= stop_price:
                                realized_pnl = current_price - entry
                                peak_snapshot = self.futures_night_peak
                                if realized_pnl < 0:
                                    self.futures_night_consecutive_losses += 1
                                    if self.futures_night_consecutive_losses >= self.futures_consecutive_loss_limit:
                                        self.futures_night_trade_count = self.futures_max_trades_night
                                        if notifier:
                                            notifier.send_message(f"🚨 <b>[야간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 야간 거래가 정지되었습니다.")
                                else:
                                    self.futures_night_consecutive_losses = 0
                                print(f"[야간선물(샹들리에)] 💎 LONG 청산! 진입:{entry:.2f} 최고가:{peak_snapshot:.2f} 현재:{current_price:.2f} (되돌림:{dist:.2f}pt) 손익:{realized_pnl:+.2f}pt")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                self.futures_night_entry_price = 0.0
                                if notifier:
                                    notifier.send_message(f"💎 <b>[야간선물(샹들리에) LONG 청산]</b> {realized_pnl:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (최고가:{peak_snapshot:.2f}, 되돌림:{dist:.2f}pt)")
                                return
                        else:
                            if pnl_pt >= self.futures_night_take_profit_pt:
                                print(f"[야간선물] 🎯 LONG 익절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 손익:{pnl_pt:+.2f}pt")
                                self.futures_night_consecutive_losses = 0
                                self.save_futures_exit_state()
                                self._execute_futures_direct("LONG_EXIT", current_price, code, pos_key)
                                self.futures_night_entry_price = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[야간선물 익절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (고정)")
                                return

                    elif pos['type'] == 'SHORT':
                        # 최저가 추적 및 갱신
                        if current_price < self.futures_night_peak or self.futures_night_peak == 0:
                            self.futures_night_peak = current_price
                            # (2026-07-20 추가) 재시작 시 트레일링 기준점이 유실되지 않도록, 유리한
                            # 방향으로 갱신될 때마다 즉시 디스크에 체크포인트
                            self.save_futures_exit_state()

                        pnl_pt = entry - current_price
                        max_pnl_pt = entry - self.futures_night_peak
                        
                        # 1. 손절 검사
                        if pnl_pt <= -sl_limit:
                            print(f"[야간선물] 🚨 SHORT 손절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 손익:{pnl_pt:+.2f}pt (SL:{sl_limit:.2f}pt)")
                            self.futures_night_consecutive_losses += 1
                            if self.futures_night_consecutive_losses >= self.futures_consecutive_loss_limit:
                                self.futures_night_trade_count = self.futures_max_trades_night
                                if notifier:
                                    notifier.send_message(f"🚨 <b>[야간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 야간 거래가 정지되었습니다.")
                            self.save_futures_exit_state()
                            self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                            self.futures_night_entry_price = 0.0
                            if notifier:
                                notifier.send_message(f"🚨 <b>[야간선물 손절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (SL:{sl_limit:.2f}pt)")
                            return
                        
                        # 2. 익절 검사
                        if is_kalman:
                            tp_price = getattr(self, 'futures_night_entry_tp_price', 0.0)
                            if tp_price > 0 and current_price <= tp_price:
                                print(f"[야간선물(칼만)] 🎯 SHORT 3-Sigma 익절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 목표가:{tp_price:.2f}pt")
                                if pnl_pt > 0:
                                    self.futures_night_consecutive_losses = 0
                                else:
                                    self.futures_night_consecutive_losses += 1
                                self.save_futures_exit_state()
                                self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                self.futures_night_entry_price = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[야간선물(칼만) 3-Sigma 익절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (목표가:{tp_price:.2f}pt)")
                                return
                            # 트레일링 스탑 적용 (주간과 동일한 계단식 Lock-In, 2026-07-10 야간 이식)
                            elif max_pnl_pt >= max(self.futures_kf_ts_trigger_mult * c_std, self.futures_kf_ts_min_rr_ratio * sl_limit):
                                # (2026-07-15 수정) kf_ts_trigger_mult가 작으면 tier 발동폭이 잠금 하한(2.0/5.0pt)보다
                                # 작아져, 그 이익폭에 아직 도달하지 못한 상태에서 ts_price가 entry+lock으로 뛰어올라
                                # current_price와 자명하게 교차 -> 즉시 오청산되는 버그가 있었음. tier 발동폭 자체를
                                # 잠금폭 이상으로 하한을 둬서, lock이 걸릴 때는 항상 그 이익을 실제로 달성한 뒤이도록 보장.
                                ts_tier2 = max(4 * self.futures_kf_ts_trigger_mult * c_std, 5.0)
                                ts_tier1 = max(2 * self.futures_kf_ts_trigger_mult * c_std, 2.0)
                                active_cb_mult = self.futures_kf_ts_callback_mult
                                if max_pnl_pt >= ts_tier2:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.4  # 콜백 비율 60% 축소
                                elif max_pnl_pt >= ts_tier1:
                                    active_cb_mult = self.futures_kf_ts_callback_mult * 0.6  # 콜백 비율 40% 축소
                                # kf_ts_floor를 수수료/슬리피지 감안해 0.5pt로 하한 보장 (주간과 동일)
                                ts_price = self.futures_night_peak + max(active_cb_mult * c_std, max(self.futures_kf_ts_floor, 0.5))
                                # 수익 확정 보증 규칙(Lock-In): SHORT는 가격이 낮을수록 유리하므로 min()으로 더 낮은 쪽을 채택
                                if max_pnl_pt >= ts_tier2:
                                    ts_price = min(ts_price, entry - 5.0)
                                elif max_pnl_pt >= ts_tier1:
                                    ts_price = min(ts_price, entry - 2.0)
                                if current_price >= ts_price:
                                    realized_pnl = entry - current_price
                                    peak_snapshot = self.futures_night_peak
                                    print(f"[야간선물(칼만)] 💎 SHORT 트레일링 스탑 청산! 최고수익:{max_pnl_pt:.2f}pt (피크:{peak_snapshot:.2f}) ➡️ 현재가:{current_price:.2f} (이익보전:{realized_pnl:+.2f}pt)")
                                    if realized_pnl > 0:
                                        self.futures_night_consecutive_losses = 0
                                    else:
                                        self.futures_night_consecutive_losses += 1
                                    self.save_futures_exit_state()
                                    self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                    self.futures_night_entry_price = 0.0
                                    if notifier:
                                        notifier.send_message(f"💎 <b>[야간선물(칼만) 트레일링 익절]</b> {realized_pnl:+.2f}pt | 최저가:{peak_snapshot:.2f} ➡️ 현재가:{current_price:.2f} (TS 가동:{self.futures_kf_ts_trigger_mult}*std ➡️ 되돌림:max({self.futures_kf_ts_callback_mult}*std, {self.futures_kf_ts_floor}pt))")
                                    return
                        elif is_chandelier:
                            dist = min(self.futures_chandelier_mult * getattr(self, "futures_atr_14", 5.0), self.futures_chandelier_hard_cap)
                            stop_price = self.futures_night_peak + dist
                            if current_price >= stop_price:
                                realized_pnl = entry - current_price
                                peak_snapshot = self.futures_night_peak
                                if realized_pnl < 0:
                                    self.futures_night_consecutive_losses += 1
                                    if self.futures_night_consecutive_losses >= self.futures_consecutive_loss_limit:
                                        self.futures_night_trade_count = self.futures_max_trades_night
                                        if notifier:
                                            notifier.send_message(f"🚨 <b>[야간선물 거래정지]</b>\n{self.futures_consecutive_loss_limit}회 연속 손실 발생으로 인해 금일 야간 거래가 정지되었습니다.")
                                else:
                                    self.futures_night_consecutive_losses = 0
                                print(f"[야간선물(샹들리에)] 💎 SHORT 청산! 진입:{entry:.2f} 최저가:{peak_snapshot:.2f} 현재:{current_price:.2f} (되돌림:{dist:.2f}pt) 손익:{realized_pnl:+.2f}pt")
                                self.save_futures_exit_state()
                                self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                self.futures_night_entry_price = 0.0
                                if notifier:
                                    notifier.send_message(f"💎 <b>[야간선물(샹들리에) SHORT 청산]</b> {realized_pnl:+.2f}pt | 진입:{entry:.2f} → 현재:{current_price:.2f} (최저가:{peak_snapshot:.2f}, 되돌림:{dist:.2f}pt)")
                                return
                        else:
                            if pnl_pt >= self.futures_night_take_profit_pt:
                                print(f"[야간선물] 🎯 SHORT 익절 청산! 진입가:{entry:.2f} 현재가:{current_price:.2f} 손익:{pnl_pt:+.2f}pt")
                                self.futures_night_consecutive_losses = 0
                                self.save_futures_exit_state()
                                self._execute_futures_direct("SHORT_EXIT", current_price, code, pos_key)
                                self.futures_night_entry_price = 0.0
                                if notifier:
                                    notifier.send_message(f"🎯 <b>[야간선물 익절]</b> {pnl_pt:+.2f}pt | 진입가:{entry:.2f} ➡️ 현재가:{current_price:.2f} (고정)")
                                return
            return  # 포지션 보유 중일 때는 신규 진입 차단

        # ── 신규 진입 조건 (trade_futures_night=False면 신규 진입만 차단, 기존 포지션 손절/익절 감시는 항상 위에서 수행됨) ──
        if not getattr(self, "trade_futures_night", True):
            return
        if not self.futures_night_order_locked and not self.system_halted:
            # [서킷브레이커 최종 안전장치] 전략 종류와 무관하게 일일 총 거래횟수 하드캡 초과 시 신규 진입 전면 차단
            if self.futures_night_trade_count >= self.futures_night_max_trades_hard_cap:
                return
            # Parabolic SAR / Bollinger Band / Kalman 전략은 거래 횟수 제한 제외 (무제한)
            is_unlimited = getattr(self, "futures_strategy_type", "") in ["parabolic_sar", "bollinger_band", "kalman", "chandelier"]
            if not is_unlimited and self.futures_night_trade_count >= self.futures_max_trades_night:
                return
            # 칼만/샹들리에는 무제한 거래 중에도 연속 손실 N회 시 즉시 당일 거래 정지
            is_kalman = (getattr(self, "futures_strategy_type", "") == "kalman")
            is_chandelier = (getattr(self, "futures_strategy_type", "") == "chandelier")
            if (is_kalman or is_chandelier) and getattr(self, "futures_night_consecutive_losses", 0) >= self.futures_consecutive_loss_limit:
                return
            # [AMATS 최적화] 초저변동성 구간 진입 차단 필터링 (ATR Cutoff)
            atr_val = getattr(self, 'futures_atr_14', 2.0)
            if atr_val < self.futures_atr_cutoff:
                return
            # 저변동성(std_error) 진입 필터 (2026-07-09 백테스트 검증 도입, 기본 0.0이면 비활성)
            if getattr(self, 'futures_night_std_error', 0.5) < self.futures_min_std_error_entry:
                return

            # 장기 칼만 필터 추세 필터링 (Proposal 3) — 샹들리에도 백테스트와 동일하게 추세필터 적용
            is_kalman = (getattr(self, "futures_strategy_type", "volatility_breakout") == "kalman")
            is_chandelier = (getattr(self, "futures_strategy_type", "") == "chandelier")
            use_trend_filter = is_kalman or is_chandelier
            trend = getattr(self, "futures_night_trend_direction", "NEUTRAL") if use_trend_filter else "NEUTRAL"

            if current_price >= self.futures_night_target_long:
                # 장기 추세가 하락세(DOWN)일 때 LONG 진입 무시
                if use_trend_filter and trend == "DOWN":
                    return
                # 칼만 필터인 경우 이미 익절 타겟을 초과했으면 진입 금지 (무한 루프 방지)
                if is_kalman and self.futures_night_tp_price_long > 0 and current_price >= self.futures_night_tp_price_long:
                    return
                if not self.enable_reentry_filter or self._is_reentry_allowed("LONG", current_price, is_night=True):
                    self.futures_night_entry_price = current_price
                    self.futures_night_peak = current_price # 진입 즉시 초기화
                    # 진입 시점 스냅샷 — 주간과 동일한 이유(무한루프 버그 수정, 2026-07-01)
                    self.futures_night_entry_std_error = getattr(self, "futures_night_std_error", 0.5)
                    self.futures_night_entry_atr = getattr(self, "futures_atr_14", 5.0)
                    self.futures_night_entry_tp_price = getattr(self, "futures_night_tp_price_long", 0.0)
                    trend_tp_mult = getattr(self, "futures_trend_tp_sigma_mult", None)
                    if trend_tp_mult is not None and is_kalman and trend == "UP":
                        self.futures_night_entry_tp_price += (trend_tp_mult - self.futures_kf_tp_sigma_mult) * self.futures_night_entry_std_error
                    self.futures_night_trade_count += 1
                    if self.futures_night_trade_count >= self.futures_night_max_trades_hard_cap and notifier:
                        notifier.send_message(f"🚨 <b>[야간선물 거래정지 - 하드캡]</b>\n금일 총 거래횟수가 {self.futures_night_trade_count}회로 하드캡({self.futures_night_max_trades_hard_cap}회)에 도달하여 신규 진입이 정지되었습니다.\n(승패와 무관한 총 거래횟수 기준 최종 안전장치)")
                    self.save_futures_exit_state()
                    self._execute_futures_direct("LONG_ENTER", current_price, code, pos_key)
            elif current_price <= self.futures_night_target_short:
                # 장기 추세가 상승세(UP)일 때 SHORT 진입 무시
                if use_trend_filter and trend == "UP":
                    return
                # 칼만 필터인 경우 이미 익절 타겟을 초과했으면 진입 금지 (무한 루프 방지)
                if is_kalman and self.futures_night_tp_price_short > 0 and current_price <= self.futures_night_tp_price_short:
                    return
                if not self.enable_reentry_filter or self._is_reentry_allowed("SHORT", current_price, is_night=True):
                    self.futures_night_entry_price = current_price
                    self.futures_night_peak = current_price # 진입 즉시 초기화
                    # 진입 시점 스냅샷 — 주간과 동일한 이유(무한루프 버그 수정, 2026-07-01)
                    self.futures_night_entry_std_error = getattr(self, "futures_night_std_error", 0.5)
                    self.futures_night_entry_atr = getattr(self, "futures_atr_14", 5.0)
                    self.futures_night_entry_tp_price = getattr(self, "futures_night_tp_price_short", 0.0)
                    trend_tp_mult = getattr(self, "futures_trend_tp_sigma_mult", None)
                    if trend_tp_mult is not None and is_kalman and trend == "DOWN":
                        self.futures_night_entry_tp_price -= (trend_tp_mult - self.futures_kf_tp_sigma_mult) * self.futures_night_entry_std_error
                    self.futures_night_trade_count += 1
                    if self.futures_night_trade_count >= self.futures_night_max_trades_hard_cap and notifier:
                        notifier.send_message(f"🚨 <b>[야간선물 거래정지 - 하드캡]</b>\n금일 총 거래횟수가 {self.futures_night_trade_count}회로 하드캡({self.futures_night_max_trades_hard_cap}회)에 도달하여 신규 진입이 정지되었습니다.\n(승패와 무관한 총 거래횟수 기준 최종 안전장치)")
                    self.save_futures_exit_state()
                    self._execute_futures_direct("SHORT_ENTER", current_price, code, pos_key)

    def _calc_futures_margin_per_contract(self, price):
        """지수선물(코스피200/미니) 1계약 위탁증거금. _execute_futures_direct와
        _poll_futures_signals가 공용으로 쓴다 — 예전에 이 계산이 두 곳에 각각
        복붙돼 있어 요율 수정(0.10->요율 기반)이 한쪽에만 반영된 채 방치된 적이
        있어(2026-08-08) 통합했다.

        증거금 = 기준가격 × 승수 × 요율.
        (2026-08-04 1차) 하루치 실측만 보고 "계약당 고정액(10,360,560원)"으로
        판단해 고정값을 넣었으나 오판이었다. 여러 날을 보면 계약당 반환액이
        758만~1,214만원으로 움직인다. 하루 안에서 일정했던 건 기준가격이 그날
        안 바뀌었기 때문이고, 실제로는 20% × 기준가격 × 승수다
        (검증: 20% × 1036.28pt × 50,000 = 10,362,800원 vs 실측 10,360,560원, 오차 0.02%).
        공식 위탁증거금률은 19.8%(유지 13.2%, 2026-07-06 기준).

        기존 코드의 0.10은 실제(약 0.20)의 절반이라 계약수를 2.11배 과대 산정했다.
        max_contracts 상한에 가려 있지만, 상한을 올리거나 지수가 오르면 증거금
        부족으로 주문이 거부된다.

        margin_rate 미설정 시 0.10이 적용돼 기존 동작이 유지된다. margin_per_contract를
        지정하면 그 고정값이 요율보다 우선한다(정확한 값을 아는 경우에만 사용 —
        기준가격이 갱신되면 어긋나므로 권장하지 않는다)."""
        multiplier = 50000 if getattr(self, 'futures_prefix', '101') == '105' else 250000
        _mpc = getattr(self, 'futures_margin_per_contract', None)
        if _mpc is not None and _mpc > 0:
            return _mpc
        return price * multiplier * getattr(self, 'futures_margin_rate', 0.10)

    def _execute_futures_direct(self, signal_type, current_price, order_code, pos_key):
        """선물 주문 직접 집행 (주간/야간 공용 — DB 신호 우회)"""
        is_night = (pos_key == "KOSPI200_NIGHT")
        lock_attr = "futures_night_order_locked" if is_night else "futures_order_locked"

        if getattr(self, lock_attr):
            return
        setattr(self, lock_attr, True)

        # ord_kind: 1: 신규 (청산도 신규 주문종류로 전송해야 함), 2: 정정, 3: 취소
        # slby_tp: "1": 매도, "2": 매수
        direction_map = {
            "LONG_ENTER":  (1, "2", "LONG 진입 📈"),
            "SHORT_ENTER": (1, "1", "SHORT 진입 📉"),
            "LONG_EXIT":   (1, "1", "LONG 청산 📤"),
            "SHORT_EXIT":  (1, "2", "SHORT 청산 📤"),
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
                # 증거금 계산 근거·이력은 _calc_futures_margin_per_contract 참고
                margin_per = self._calc_futures_margin_per_contract(current_price)

                # [AMATS 최적화] active_strategy.json의 마진캡(최적화 적용값 50%)을 반영한 자본 대비 계약 수 계산
                margin_cap = getattr(self, 'futures_margin_cap_ratio', 0.30)
                qty = max(1, int((self.futures_available_balance * margin_cap) / margin_per)) if margin_per > 0 else 1
                # 최대 계약수 한도 — 과도한 레버리지 노출 제약.
                # (2026-08-04) 하드코딩 15를 config로 뺐다. 값 자체는 기본 15로 그대로여서
                # 설정하지 않으면 동작이 바뀌지 않는다. 실측 증거금(계약당 10,360,560원)
                # 기준으로 현재 잔고에서는 30계약이 산정되므로 이 상한이 실질 제약이며,
                # 조정하려면 코드를 고쳐야 했던 상태를 해소하는 것이 목적이다.
                # ※ 상한 인상은 백테스트로 판단하지 말 것 — 전체기간 MDD는 계좌가 1.3억이던
                #    2025-12-19에 찍힌 값이 그대로 남아 상한을 올려도 2.40%로 고정돼, 위험
                #    증가가 지표에 전혀 반영되지 않는다(2026-08-04 확인).
                qty = min(qty, getattr(self, 'futures_max_contracts', 15))

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
                # 청산 주문 전송 즉시 로컬 상태에 is_exiting=True 마킹하여 중복 주문 방지
                if pos_key in self.futures_positions:
                    self.futures_positions[pos_key]['is_exiting'] = True

                    # (2026-08-04 #1) 재진입 필터가 쓰는 최종 청산가/시각을 '청산 주문을 낼 때'
                    # 여기서 기록한다. 기존엔 체결콜백(chejan)에서만 기록돼, 모의서버가 콜백을
                    # 누락하면 청산가=0으로 남아 재진입 필터가 통과되고 즉시 재진입/플립이 났다.
                    # (체결콜백이 나중에 실체결가로 덮어써도 무방 — 시각/근사가가 먼저 잡히는 게 핵심)
                    # 아울러 방향무관 쿨다운(#2)용 '마지막 청산 시각'도 함께 찍는다.
                    if getattr(self, "futures_exit_record_on_send", True):
                        import time as _t_exit
                        _exit_epoch = _t_exit.time()
                        self.futures_last_any_exit_time = _exit_epoch
                        if signal_type == "LONG_EXIT":
                            if is_night:
                                self.futures_night_last_long_exit_price = current_price
                                self.futures_night_last_long_exit_time = _exit_epoch
                            else:
                                self.futures_last_long_exit_price = current_price
                                self.futures_last_long_exit_time = _exit_epoch
                        elif signal_type == "SHORT_EXIT":
                            if is_night:
                                self.futures_night_last_short_exit_price = current_price
                                self.futures_night_last_short_exit_time = _exit_epoch
                            else:
                                self.futures_last_short_exit_price = current_price
                                self.futures_last_short_exit_time = _exit_epoch

                    # 청산 주문 후 일정 시간 내 체결 미확인 시 재시도 (최대 3회 초과 시 로컬 포지션 강제 초기화)
                    # 모의투자 환경에서 체결 콜백이 오지 않아 is_exiting이 영구 해제 → 재주문 → 루프가 발생하는 것을 방지
                    #
                    # (2026-07-28 강화) 취소 요청을 보내도 브로커에 실제 반영되기까지는 시간이
                    # 걸릴 수 있다. 취소 요청 직후 바로 재주문/강제초기화를 진행하면, 취소가 아직
                    # 처리되기 전에 원주문이 뒤늦게 체결되면서 재주문과 이중 체결될 위험이 남는다.
                    # 그래서 취소 요청과 재시도 판단 사이에 CANCEL_GRACE_MS 유예를 두고, 유예가
                    # 끝난 시점에 포지션이 이미 사라졌으면(=원주문이 그 사이 실제로 체결되어
                    # 정상 정리됨) 재시도/강제초기화 자체를 하지 않는다.
                    #
                    # (2026-08-03 재설계) 위 구조에 두 가지 결함이 실측으로 드러나 함께 고친다.
                    #  (1) 재시도가 틱에 의존했다. 유예가 끝나면 is_exiting만 풀고, 실제 재주문은
                    #      _process_day_tick이 다시 호출되기를 기다렸다. 그런데 장마감 청산이
                    #      필요한 15:35~15:45는 종가 단일가 구간이라 틱이 거의 들어오지 않는다.
                    #      2026-08-03 15:42 LONG 15계약 청산 주문이 미확인으로 끝난 뒤 재시도가
                    #      단 한 번도 발동하지 못했고, 창(15:45)을 넘겨 포지션이 그대로 오버나잇으로
                    #      넘어갔다. → 이제 타이머가 _execute_futures_direct를 직접 재호출한다.
                    #  (2) 기본 사이클(15초 미확인 + 20초 유예 = 35초)이 마감 창 끝자락에서는
                    #      재시도 기회를 창 밖으로 밀어낸다. → 마감 창 안에서는 두 값을 줄여
                    #      최소 한 번의 재시도가 창 안에 들어오게 한다.
                    _now_exec = datetime.now()
                    _in_eod_window = (not is_night) and _now_exec.hour == 15 and 35 <= _now_exec.minute <= 45
                    UNCONFIRMED_MS = 6000 if _in_eod_window else 15000
                    CANCEL_GRACE_MS = 6000 if _in_eod_window else 20000

                    def _finalize_exit_after_cancel_grace(retry_count):
                        pos = self.futures_positions.get(pos_key)
                        if pos is None:
                            print(f"[{session_label}선물] ✅ 취소 유예 중 원주문 체결로 포지션 정리 확인됨 → 재시도 불필요")
                            return
                        if not pos.get('is_exiting'):
                            return  # 유예 도중 다른 경로(정상 체결 처리 등)로 이미 처리됨

                        if retry_count >= getattr(self, "futures_exit_retry_max", 3):
                            # (2026-08-04 #3) 로컬 강제삭제(브로커와의 불일치를 은폐)를 폐기한다.
                            # 포지션을 유지한 채 자동 재시도만 중단하고 크게 경고 — 08:45 안전청산과
                            # 수동청산(!전량매도)이 이 포지션을 계속 볼 수 있어 조용한 오버나잇 방치를
                            # 막는다. (#1 능동 재조회로 '실제 체결됐는데 콜백만 누락'된 경우는 이미
                            # 위 pos is None 분기에서 걸러지므로, 여기까지 왔다는 건 정말로 미청산일
                            # 가능성이 높다.)
                            print(f"[{session_label}선물] 🚨 청산 주문 {retry_count}회 체결 미확인 → 자동 재시도 중단, 포지션 유지 + 경고(로컬 삭제 안 함)")
                            pos['is_exiting'] = False
                            setattr(self, lock_attr, False)
                            if notifier:
                                notifier.send_message(
                                    f"🚨 <b>[{session_label}선물 청산 반복 실패 {retry_count}회]</b>\n"
                                    f"{pos.get('type')} {pos.get('qty')}계약 자동청산이 계속 미확인입니다.\n"
                                    f"⚠️ 포지션이 열린 채 유지 중 — <code>!전량매도</code> 또는 영웅문4에서 수동 청산 필요"
                                )
                        else:
                            print(f"[{session_label}선물] ⚠️ 청산 주문 {retry_count}회 체결 미확인 (취소 유예 종료) → 즉시 재주문")
                            pos['is_exiting'] = False
                            if notifier:
                                notifier.send_message(
                                    f"⚠️ <b>[{session_label}선물 청산 체결 미확인 {retry_count}/3]</b>\n"
                                    f"원주문 취소 후 청산을 재주문합니다."
                                )
                            # (2026-08-03) 틱을 기다리지 않고 여기서 직접 재주문한다.
                            # exit_retry_count는 pos에 남아 있으므로 3회 한도는 그대로 유지되고,
                            # 위에서 is_exiting을 풀었으므로 중복 주문 가드에도 걸리지 않는다.
                            #
                            # (2026-08-04) 가격은 클로저에 잡힌 값이 아니라 그 시점의 최신가를 쓴다.
                            # 시장가 주문("3"/가격 0)이라 체결 자체엔 영향이 없지만, 로그와 텔레그램에
                            # 수십 초 전 가격이 찍히면 사후 분석 때 오해를 부른다. pos['current_price']는
                            # 틱과 계좌 동기화 양쪽에서 갱신된다.
                            _retry_price = pos.get('current_price') or current_price
                            self._execute_futures_direct(signal_type, _retry_price, order_code, pos_key)

                    def _clear_exiting_if_no_fill():
                        pos = self.futures_positions.get(pos_key)
                        if pos is not None and pos.get('is_exiting'):
                            retry_count = pos.get('exit_retry_count', 0) + 1
                            pos['exit_retry_count'] = retry_count

                            # 재시도로 새 주문을 내기 전에, 앞서 낸 원주문이 아직 브로커에 살아
                            # 있을 수 있으므로 먼저 취소를 시도한다 (원주문번호 미확보 시 조용히
                            # 건너뛰고 기존 동작 그대로 진행 — 취소 실패가 재시도 자체를 막지 않음).
                            _org_order_no = getattr(self, "_futures_last_order_no", {}).get(order_code)
                            if _org_order_no:
                                try:
                                    self.kiwoom.dynamicCall(
                                        "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
                                        ["FuturesLive", "0200", self.futures_account, order_code, 3, slby_tp, "3", qty, "0", _org_order_no]
                                    )
                                    print(f"[{session_label}선물] 🚫 미체결 원주문({_org_order_no}) 취소 요청 전송 → {CANCEL_GRACE_MS // 1000}초 유예 후 재시도 판단")
                                except Exception as _cancel_err:
                                    print(f"[{session_label}선물] 원주문 취소 요청 실패: {_cancel_err}")
                            else:
                                # 원주문번호를 못 잡은 경우(키움이 접수 체잔을 안 보내준 경우).
                                # 2026-08-03 실측: 이 경로로 빠져 취소가 한 번도 전송되지 않았다.
                                # 유예 자체는 그대로 둔다 — 취소를 못 보냈다는 건 원주문이 아직
                                # 살아있을 가능성이 오히려 크다는 뜻이라, 뒤늦은 체결이 도착할
                                # 시간을 주는 편이 이중체결 방지에 유리하다(유예 종료 시점의
                                # 포지션 존재 확인이 그 판정을 한다).
                                print(f"[{session_label}선물] ⚠️ 원주문번호 미확보 — 취소 전송 불가, {CANCEL_GRACE_MS // 1000}초 유예 후 재시도 판단")
                                # (2026-08-04 #2) 취소를 못 보내는 경우일수록 원주문이 실제로 체결됐는지
                                # 능동 확인이 중요하다. 잔고 재조회를 걸어, 유예 종료 시점의 포지션
                                # 존재 판정이 5분 주기 동기화가 아니라 최신 상태로 이뤄지게 한다.
                                if getattr(self, "futures_exit_confirm_resync", True):
                                    self.sync_futures_positions_and_balance()

                            QTimer.singleShot(CANCEL_GRACE_MS, lambda rc=retry_count: _finalize_exit_after_cancel_grace(rc))
                    QTimer.singleShot(UNCONFIRMED_MS, _clear_exiting_if_no_fill)

                    # (2026-08-04 #1) 청산 주문 직후 계좌잔고를 능동 재조회 — 미확인/취소유예
                    # 판정이 5분 주기 동기화가 아니라 최신 포지션 상태로 이뤄지게 한다. 원주문이
                    # 실제로 체결됐으면 이 재조회로 포지션이 사라져 재시도/강제처리가 취소된다.
                    if getattr(self, "futures_exit_confirm_resync", True):
                        QTimer.singleShot(2500, self.sync_futures_positions_and_balance)
                        QTimer.singleShot(max(3000, UNCONFIRMED_MS - 500), self.sync_futures_positions_and_balance)

                    # (2026-08-03) 마감 창 초과 감시 — 창(15:45)이 지나도 주간 포지션이 남아
                    # 있으면 조용히 오버나잇으로 넘기지 않고 반드시 알린다. 2026-08-03에는
                    # 아무 경고 없이 포지션이 다음날까지 방치됐고, 사용자가 밤에 직접 발견했다.
                    # 재시도마다 중복 예약되면 알림이 여러 번 울리므로 당일 1회만 예약한다.
                    _eod_check_key = _now_exec.strftime("%Y%m%d")
                    if _in_eod_window and getattr(self, "_eod_overrun_check_date", None) != _eod_check_key:
                        self._eod_overrun_check_date = _eod_check_key

                        def _warn_if_still_open_after_eod():
                            _p = self.futures_positions.get(pos_key)
                            if not _p:
                                return
                            _msg = (f"🚨 <b>[주간선물 장마감 청산 실패]</b>\n"
                                    f"15:45 마감 창을 넘겼는데 포지션이 남아 있습니다.\n"
                                    f"• {_p.get('type')} {_p.get('qty')}계약 @ {_p.get('price', 0):.2f}pt\n"
                                    f"⚠️ 오버나잇 노출 상태 — 수동 청산 필요\n"
                                    f"(익일 08:45 안전청산이 있으나 그때까지 갭 위험에 노출됩니다)")
                            print(f"[주간선물] 🚨 마감 창 초과 — 포지션 잔존: {_p.get('type')} {_p.get('qty')}계약")
                            if notifier:
                                notifier.send_message(_msg)
                        # 15:46:30 시점에 확인 (창 종료 + 90초 여유)
                        _ms_until_check = max(
                            5000,
                            int((datetime.now().replace(hour=15, minute=46, second=30, microsecond=0)
                                 - datetime.now()).total_seconds() * 1000)
                        )
                        QTimer.singleShot(_ms_until_check, _warn_if_still_open_after_eod)
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
        """5분마다 intraday_ohlcv + top_volume_theme 기반 단타 진입 신호 생성

        진입 필터는 4개월/991건 백테스트로 검증된 조합(거래량 2배+과열캡4%+오전장한정)을 적용.
        기존 시가대비+2%·거래량1.5배·09~14시 조건은 -30.51%(MDD33.62%)였으나, 이 조합은
        +3.24%(PF1.08, MDD10.30%, 422건)로 전환됨 — 오후 진입과 이미 과열된(+4%초과) 종목
        추격매수가 손실의 주된 원인이었음.
        """
        now = datetime.now()
        if not (9 <= now.hour < 11):  # 09:00 ~ 11:00 오전장으로 제한 (오후 진입은 백테스트상 손실 요인)
            return
        # 09:00 개장 직후엔 주간선물전략이 먼저 활성화되게 최소 10초 대기 (2026-07-17: 개장 순간
        # 단타스캔·ISF체크·선물전략이 한꺼번에 몰려 키움 API에 순간 부하가 걸리는 것을 완화)
        if now.hour == 9 and now.minute == 0:
            activated_at = getattr(self, "_day_strategy_activated_at", None)
            if activated_at is None or (now - activated_at).total_seconds() < 10:
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
                # 거래량 서지 판정은 완결된 직전봉(candles[1]) 기준으로 — 스캔 주기(5분)와 3분봉
                # 경계가 어긋나 진행 중인 candles[0]가 몇 초치 데이터만 반영하면 거래량 조건을
                # 과소평가해 실제 돌파 신호를 그 주기에 놓칠 수 있었음
                current_volume = candles[1][1] if len(candles) > 1 else candles[0][1]
                avg_volume = sum(c[1] for c in candles[2:]) / (len(candles) - 2) if len(candles) > 2 else 1

                # 당일 진짜 시가는 LIMIT 20 윈도우와 별개로 직접 조회 — 09:00 첫 3분봉이 20개를
                # 넘어가면(10시 이후) candles[-1]은 "당일 시가"가 아니라 "그냥 60분 전 시가"가
                # 되어버려 돌파/과열 판정 기준선이 시간이 지날수록 계속 밀려가던 버그였음
                cursor.execute(
                    "SELECT open FROM intraday_ohlcv WHERE code = ? AND date LIKE ? ORDER BY date ASC LIMIT 1",
                    (code, today_prefix + "%")
                )
                open_row = cursor.fetchone()
                day_open = open_row[0] if open_row else 0

                if day_open <= 0 or current_price <= 0:
                    continue

                # [안전장치] 10,000원 이하 초저가 종목 단타 진입 차단 필터
                if current_price <= 10000:
                    continue

                is_breakout       = current_price >= day_open * 1.02       # 시가 대비 +2% 돌파
                is_not_overheated = current_price <= day_open * 1.04       # 과열캡: +4% 초과 추격매수 금지
                is_vol_surge      = avg_volume > 0 and current_volume >= avg_volume * 2.0
                change_pct = (current_price / day_open - 1) * 100

                if is_breakout and is_not_overheated and is_vol_surge:
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
            "stock_account": f"{self.stock_account_day}(단)/{self.stock_account_swing}(스)" if getattr(self, 'is_physical_separated', False) else self.stock_account_day,
            "futures_account": self.futures_account,
            "total_balance": self.stock_total_balance,
            "budget_day": self.budget_day,
            "budget_swing": self.budget_swing,
            "daily_realized_loss": self.stock_daily_loss,
            "stock_daily_halted": self.stock_daily_halted,
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
                # (2026-07-21 추가) TCA가 미니(105)/표준(101) 거래승수를 정확히 판별하도록 전달 —
                # futures_positions의 딕셔너리 키("KOSPI200"/"KOSPI200_NIGHT")는 세션 라벨이지
                # 종목코드가 아니라서 '105' 포함 여부로 판별이 불가능함(항상 표준 승수로 오판되어
                # 미니선물 실현손익이 5배로 부풀려 표시되던 버그 원인).
                "prefix": getattr(self, "futures_prefix", "105"),
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
                # (2026-08-04 수정) 승수를 futures_prefix로 판정한다.
                # 기존 코드는 `'105' in code`였는데, 이 루프의 code는 종목코드가 아니라
                # futures_positions의 키("KOSPI200" / "KOSPI200_NIGHT")다. 따라서 조건이
                # 항상 False가 되어 미니선물(50,000)도 표준선물(250,000)로 계산됐고,
                # daily_balance_history의 선물 평가손익이 5배로 기록돼 왔다.
                # (매매 로직과는 무관한 통계 테이블이라 체결에는 영향이 없었으나,
                #  수익률 추적이 왜곡됨. 같은 파일 4950/5832행은 이미 이 방식을 쓴다.)
                _multiplier = 50000 if getattr(self, 'futures_prefix', '101') == '105' else 250000
                for code, pos in self.futures_positions.items():
                    p_type = pos.get('type', 'LONG')
                    buy_price = pos.get('price', 0)
                    qty = pos.get('qty', 0)
                    current_price = pos.get('current_price', buy_price)
                    multiplier = _multiplier
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
            today_prefix = datetime.now().strftime("%Y%m%d")
            for code in day_codes:
                # 오늘 날짜 필터 없이 최근 20개를 가져오면 장 초반(캔들이 20개 미만 쌓인 시점)에
                # 전일 종가가 섞여 들어와 MA10/20이 왜곡될 수 있음
                cursor.execute(
                    "SELECT close FROM intraday_ohlcv WHERE code = ? AND date LIKE ? ORDER BY date DESC LIMIT 20",
                    (code, today_prefix + "%")
                )
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
                            ["[ERA_Day_Flat]", "0103", self.stock_account_day, 2, code, pos['qty'], 0, "03", ""]
                        )
            # [신설 - Fail-safe] 15:18 ~ 15:28 사이 미청산 단타 잔고 재차 청산 시도 (30초 주기)
            if now.hour == 15 and 18 <= now.minute < 28 and now.second % 30 == 0:
                for code, pos in list(self.portfolio.items()):
                    if pos.get('strategy') == 'DAY' and pos.get('qty', 0) > 0:
                        print(f"\n⚠️ [단타 미청산 감지] {pos['name']}({code}) - {pos['qty']}주 잔고 존재. 강제 재청산 주문 전송.")
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Day_Retry]", "0103", self.stock_account_day, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(
                                f"⚠️ <b>[주식 단타 미청산 비상 재청산] {pos['name']}</b>\n"
                                f"• 미청산 잔고({pos['qty']}주)가 감지되어 재청산 주문을 다시 전송합니다."
                            )

            # [신설 - Fail-safe] 스윙 최대보유일 강제청산 미체결 잔고 재차 청산 시도 (30초 주기)
            # 기존에는 SendOrder() 1회 호출 후 응답/체결 확인이 없어 주문이 조용히 실패하면
            # 포지션이 무기한 방치될 수 있었음 — max_hold_forced 플래그가 걸린 채 잔고가 남아있으면 반복 재시도
            if now.second % 30 == 0:
                for code, pos in list(self.portfolio.items()):
                    if pos.get('strategy') == 'SWING' and pos.get('max_hold_forced') and pos.get('qty', 0) > 0:
                        print(f"\n⚠️ [스윙 최대보유일 미청산 감지] {pos['name']}({code}) - {pos['qty']}주 잔고 존재. 강제 재청산 주문 전송.")
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Swing_MaxHold_Retry]", "0103", self.stock_account_swing, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(
                                f"⚠️ <b>[스윙 최대보유일 미청산 비상 재청산] {pos['name']}</b>\n"
                                f"• 미청산 잔고({pos['qty']}주)가 감지되어 재청산 주문을 다시 전송합니다."
                            )

            if now.hour == 15 and now.minute >= 14 and not self.today_5ma_checked:
                self.today_5ma_checked = True
                print("\n[⏰ ERA 종가 익절 감시] 15:14+ 스윙 종목 5MA 체크를 시작합니다.")

                # 최대 보유일수(swing_max_holding_days) 초과 종목은 5MA/10MA 추세 이탈 여부와
                # 무관하게 강제 청산 — 보유기간이 길어질수록 반전(reversal) 위험이 커지는 모멘텀
                # 전략 특성상 무기한 보유는 위험하므로 시간 기반 안전장치를 둠
                swing_codes = [c for c, p in self.portfolio.items() if p['strategy'] == 'SWING']
                expired_codes = []
                for code in swing_codes:
                    pos = self.portfolio[code]
                    try:
                        entry_dt = datetime.strptime(pos.get('entry_date', now.strftime('%Y-%m-%d')), '%Y-%m-%d')
                        # 달력일이 아닌 실제 거래일 기준으로 카운트 — 주말/공휴일이 낀 경우
                        # 명목상 15일보다 훨씬 이른 시점에 조기 강제청산되는 문제를 방지
                        held_days = 0
                        d = entry_dt.date()
                        while d < now.date():
                            d += timedelta(days=1)
                            if self._is_trading_day(datetime.combine(d, datetime.min.time())):
                                held_days += 1
                    except Exception:
                        held_days = 0
                    if held_days >= self.swing_max_holding_days:
                        expired_codes.append(code)
                        print(f"\n⏳ [스윙 최대보유일 초과 강제 청산] {pos['name']}({code}) - {held_days}일 보유 (한도 {self.swing_max_holding_days}일)")
                        pos['sell_ordered'] = True
                        pos['max_hold_forced'] = True  # 미체결 시 재시도 워치독(아래)이 잡아내도록 표시
                        self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Swing_MaxHold]", "0103", self.stock_account_swing, 2, code, pos['qty'], 0, "03", ""]
                        )
                        if notifier:
                            notifier.send_message(
                                f"⏳ <b>[스윙 최대보유일 초과 청산] {pos['name']}</b>\n"
                                f"• {held_days}일 보유 (한도 {self.swing_max_holding_days}일) — 추세 이탈 여부와 무관하게 시간 기반 강제 청산합니다."
                            )

                self.pending_5ma_checks = [c for c in swing_codes if c not in expired_codes]
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
        # system_halted(월간 킬스위치)는 신규 진입만 차단해야 한다. 예전에는 여기서 통째로
        # return해버려, TCA가 !매도/!전량매도/!선물청산으로 적재한 수동 청산 신호까지 무기한
        # PENDING으로 방치되면서도 TCA는 "청산 명령 전달 완료"라고 응답하는 상태 불일치가 있었음.
        exit_only = self.system_halted

        # 일일 손실 서킷브레이커는 주식 전용(stock_daily_loss 기준)이므로 선물 쪽 exit_only에는 섞지 않는다.
        stock_exit_only = exit_only or self.stock_daily_halted

        # 1. 주식 시그널 감시 (stock/both만)
        if self.trading_mode in ('stock', 'both') and os.path.exists(self.unified_db_path):
            self._poll_stock_signals(exit_only=stock_exit_only)

        # 2. 선물 시그널 감시 (futures/both만)
        if self.trading_mode in ('futures', 'both') and os.path.exists(self.futures_db_path):
            self._poll_futures_signals(exit_only=exit_only)

    def _poll_stock_signals(self, exit_only=False):
        # 예수금 조회 완료 전까지는 자금 기준이 없어 주문 불가 → skip
        if self.stock_total_balance == 0:
            return
        conn = sqlite3.connect(self.unified_db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        try:
            cursor.execute("""SELECT id, code, name, strategy_type, price, open_price,
                                     (julianday('now') - julianday(timestamp)) * 24 * 60
                              FROM signals WHERE status = 'PENDING' LIMIT 3""")
            rows = cursor.fetchall()

            for row in rows:
                signal_id, code, name, strategy_type, price, open_price, age_min = row
                print(f"\n[🚨 주식 신규 신호 감지] {name}({code}) | 유형: {strategy_type}")

                # PENDING 신호 만료(TTL): RSA 미평가 등으로 무기한 대기하다 수일 뒤
                # 스테일 가격으로 체결되는 것을 방지 (수동 매도는 예외 — 반드시 처리되어야 함)
                if strategy_type != 'MANUAL_SELL' and age_min is not None and age_min > 45:
                    print(f" => [만료] 신호 생성 후 {age_min:.0f}분 경과(기준 45분) → SKIPPED_EXPIRED")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_EXPIRED' WHERE id = ?", (signal_id,))
                    continue

                # system_halted(월간 킬스위치) 중에는 신규 진입 신호만 건너뛴다 (PENDING 유지 →
                # 다음 폴링에서 재검토, 오래 방치되면 위 TTL로 자동 만료됨). 수동 매도는 항상 통과.
                if exit_only and strategy_type != 'MANUAL_SELL':
                    continue

                # 비정상 가격 필터 (ZeroDivisionError 원천 방지)
                if price <= 0 and strategy_type != 'MANUAL_SELL':
                    print(f" => [거절] 비정상 신호 가격: {price}")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_INVALID_PRICE' WHERE id = ?", (signal_id,))
                    continue

                # [안전장치] 5,000원 이하 종목 진입 차단 필터 (수동 매도는 허용)
                if strategy_type != 'MANUAL_SELL' and price <= 5000:
                    print(f" => [거절] 최소 가격 제한 미달 (현재 {price:,}원 / 기준 5,000원 초과)")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_PRICE_TOO_LOW' WHERE id = ?", (signal_id,))
                    continue

                # 중복 진입 검사
                if code in self.portfolio or code in self.pending_orders:
                    print(" => [거절] 이미 포트폴리오에 있거나 매매 집행 중입니다.")
                    cursor.execute("UPDATE signals SET status = 'SKIPPED_DUPLICATE' WHERE id = ?", (signal_id,))
                    continue

                # 가상 자금 파티셔닝 제한 적용
                # 주의: self.portfolio는 체결 확인 후에만 갱신되므로, 슬롯 카운트는
                # 이번 배치(LIMIT 3)에서 이미 주문을 보낸 self.pending_orders도 함께 세야
                # 동일 배치 내 시그널들이 슬롯 한도를 넘겨 중복 진입하는 경쟁조건을 막을 수 있다.
                if strategy_type == 'DAY':
                    day_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] == 'DAY'])
                    day_pos_count += len([c for c, p in self.pending_orders.items() if p['strategy'] == 'DAY'])
                    if day_pos_count >= self.max_day_positions:
                        print(" => [거절] 단타 보유 슬롯 초과")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                        continue
                    budget_per_stock = self.budget_day // self.max_day_positions
                elif strategy_type == 'SWING':
                    swing_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] == 'SWING'])
                    swing_pos_count += len([c for c, p in self.pending_orders.items() if p['strategy'] == 'SWING'])
                    if swing_pos_count >= self.max_swing_positions:
                        print(" => [거절] 스윙 보유 슬롯 초과")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_MAX_POS' WHERE id = ?", (signal_id,))
                        continue
                    budget_per_stock = self.budget_swing // self.max_swing_positions
                elif strategy_type == 'DAY_CLOSE':
                    # closing_price_scanner.py의 종가베팅("신접갈거조재") 스윙 진입 시그널.
                    # 예전엔 이 strategy_type을 인식하지 못해 else 분기로 떨어져 항상 SKIPPED_UNKNOWN
                    # 처리되고 주문이 한 건도 나가지 않았음 — 스윙 예산/슬롯을 재사용하고, 포지션은
                    # 아래에서 'SWING'으로 태깅해 5MA/10MA·하드스탑·15일보유 청산 로직을 그대로 적용받게 한다.
                    swing_pos_count = len([c for c, p in self.portfolio.items() if p['strategy'] == 'SWING'])
                    swing_pos_count += len([c for c, p in self.pending_orders.items() if p['strategy'] == 'SWING'])
                    if swing_pos_count >= self.max_swing_positions:
                        print(" => [거절] 스윙 보유 슬롯 초과 (종가베팅)")
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
                        acc_to_use = self.stock_account_day if pos.get('strategy') == 'DAY' else self.stock_account_swing
                        res = self.kiwoom.dynamicCall(
                            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                            ["[ERA_Manual_Sell]", "0103", acc_to_use, 2, code, qty, 0, "03", ""]
                        )
                        if res == 0:
                            cursor.execute("UPDATE signals SET status = 'EXECUTED' WHERE id = ?", (signal_id,))

                            # 15초 내 체결 미확인 시 sell_ordered 해제 (자동매도 트리거와 동일한 안전장치)
                            def _clear_manual_sell_if_no_fill(_code=code, _name=name):
                                p = self.portfolio.get(_code)
                                if p is not None and p.get('sell_ordered'):
                                    print(f"[ERA] ⚠️ {_name} 수동 매도 주문 15초 체결 미확인 → sell_ordered 해제 (재시도 허용)")
                                    p['sell_ordered'] = False
                                    if notifier:
                                        notifier.send_message(
                                            f"⚠️ <b>[{_name} 수동 매도 체결 미확인]</b>\n"
                                            f"15초 내 체결이 확인되지 않아 재시도가 허용됩니다. <code>!매도</code>를 다시 실행해주세요."
                                        )
                            QTimer.singleShot(15000, _clear_manual_sell_if_no_fill)
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
                # 만약 research_reports 테이블이 존재하면 60점 미만 필터링
                # DAY_CLOSE(종가베팅)는 closing_price_scanner.py 자체 NSAA 게이트를 이미 통과했으므로
                # 여기서 재요구하면 top_volume_theme 밖 종목이라 research_reports 레코드가 없어
                # 영원히 PENDING에 빠진다 — 이 신호 유형만 2차 RSA 재검증을 생략한다.
                if strategy_type != 'DAY_CLOSE':
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
                        if existing is None or existing[0] < 60:
                            cursor.execute(
                                "INSERT INTO research_reports (code, name, strategy_type, score, timestamp) VALUES (?, ?, 'MOCK', 80, ?)",
                                (code, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                            )
                            print(f" => [모의투자 RSA 자동 통과] {name}({code}) 80점 삽입 (기존={existing[0] if existing else '없음'})")
                        has_table = True

                    if has_table:
                        # 신선도 필터: 오늘 재평가된 점수만 인정 (날짜 필터 없이 "가장 최근" 점수를
                        # 가져오면 수일~수주 전 스테일 점수로 오늘 게이트를 통과/거절해버릴 수 있음)
                        if getattr(self, "apply_rsa_in_mock", False):
                            cursor.execute("SELECT score FROM research_reports WHERE code = ? AND strategy_type != 'MOCK' AND date(timestamp) = date('now','localtime') ORDER BY id DESC LIMIT 1", (code,))
                        else:
                            cursor.execute("SELECT score FROM research_reports WHERE code = ? AND date(timestamp) = date('now','localtime') ORDER BY id DESC LIMIT 1", (code,))

                        rep = cursor.fetchone()
                        if rep is None:
                            # apply_rsa_in_mock는 이름과 달리 environment 체크가 없어, live에서
                            # 이 값이 True로 설정되면 동기 네트워크 호출(FAA/IRA/NSAA 크롤링+LLM)이
                            # poll_signals() QTimer 콜백 안에서 실행되어 1초 주기 긴급정지 감시를
                            # 포함한 전체 이벤트 루프를 블로킹할 위험이 있었다. mock 환경에서만 허용.
                            if getattr(self, "apply_rsa_in_mock", False) and self.environment != "live":
                                try:
                                    from rsa.rsa_coordinator import RSACoordinator
                                    coord = RSACoordinator()
                                    if getattr(self, "gemini_api_key", None):
                                        coord.nsaa.api_key = self.gemini_api_key
                                    print(f" => [모의투자 RSA 온디맨드 분석 기동] {name}({code})")
                                    coord.evaluate_stock(code, name, strategy_type)

                                    # 다시 조회 (오늘자 점수만)
                                    cursor.execute("SELECT score FROM research_reports WHERE code = ? AND strategy_type != 'MOCK' AND date(timestamp) = date('now','localtime') ORDER BY id DESC LIMIT 1", (code,))
                                    rep = cursor.fetchone()
                                except Exception as rsa_err:
                                    print(f" => [모의투자 RSA 온디맨드 분석 실패] {rsa_err} — 테스트 안전을 위해 임시 60점 폴백")
                                    try:
                                        cursor.execute(
                                            "INSERT INTO research_reports (code, name, strategy_type, score, timestamp) VALUES (?, ?, 'MOCK_FALLBACK', 60, ?)",
                                            (code, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                        )
                                        conn.commit()
                                        # 다시 조회 (오늘자 점수만)
                                        cursor.execute("SELECT score FROM research_reports WHERE code = ? AND date(timestamp) = date('now','localtime') ORDER BY id DESC LIMIT 1", (code,))
                                        rep = cursor.fetchone()
                                    except Exception as db_err:
                                        print(f" => [모의투자 RSA 폴백 적재 실패] {db_err}")

                            if rep is None:
                                print(f" => [보류] RSA 미평가 — PENDING 유지, 장전 RSA 분석 완료 후 자동 처리됨")
                                continue  # status 변경 없음 → 2초 후 재시도
                        if rep[0] < 60:
                            print(f" => [거절] RSA 종합 리서치 점수 부족 ({rep[0]}점 / 기준 60점)")
                            cursor.execute("UPDATE signals SET status = 'SKIPPED_RSA_SCORE_LOW' WHERE id = ?", (signal_id,))
                            continue

                print(f" => [실계좌 라우팅 승인] 할당금액: {budget_per_stock:,}원 | 수량: {qty}주")
                # DAY_CLOSE 신호는 스윙 예산/슬롯만 재사용할 뿐, 포지션 자체는 'SWING'으로 태깅해야
                # 5MA/10MA·하드스탑·15일보유 등 스윙 청산 로직(strategy=='SWING' 분기)이 정상 적용된다.
                effective_strategy = 'SWING' if strategy_type == 'DAY_CLOSE' else strategy_type
                self.pending_orders[code] = {'qty': qty, 'price': price, 'type': 'BUY', 'strategy': effective_strategy, 'open_price': open_price}
                
                clean_code = str(code).strip().zfill(6)
                acc_to_use = self.stock_account_day if strategy_type == 'DAY' else self.stock_account_swing
                res = self.kiwoom.dynamicCall(
                    "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                    ["[ERA_Stock_Buy]", "0101", acc_to_use, 1, clean_code, qty, 0, "03", ""]
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

    def _poll_futures_signals(self, exit_only=False):
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

                is_entry_signal = signal_type in ("LONG_ENTER", "SHORT_ENTER")

                # system_halted(월간 킬스위치) 중에는 신규 진입 신호만 보류하고 청산(EXIT)은 통과시킨다
                if exit_only and is_entry_signal:
                    conn.close()
                    return

                # !선물매수/!선물매도 등 TCA의 수동 진입 명령도 자동매매와 동일한 서킷브레이커
                # (3연속손실 정지/일일 거래 하드캡)를 통과해야 한다 — 예전에는 _poll_futures_signals가
                # 이 가드를 전혀 거치지 않아 자동매매가 정지된 상태에서도 수동 명령은 그대로 체결됐다.
                if is_entry_signal:
                    real_day = getattr(self, 'real_day_code', '10100000')
                    real_night = getattr(self, 'real_night_code', '10500000')
                    if real_day == real_night:
                        is_night = self._resolve_is_night_session(self.futures_positions)
                    else:
                        is_night = (code == real_night)

                    if is_night:
                        losses, trade_count, hard_cap = (self.futures_night_consecutive_losses,
                                                          self.futures_night_trade_count,
                                                          self.futures_night_max_trades_hard_cap)
                        label = "야간"
                    else:
                        losses, trade_count, hard_cap = (self.futures_day_consecutive_losses,
                                                          self.futures_day_trade_count,
                                                          self.futures_day_max_trades_hard_cap)
                        label = "주간"

                    if losses >= 3:
                        print(f"  => [거절] {label}선물 3연속 손실로 신규 진입 정지 중 (수동 명령 포함)")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_CIRCUIT_BREAKER' WHERE id = ?", (signal_id,))
                        conn.commit()
                        conn.close()
                        return
                    if trade_count >= hard_cap:
                        print(f"  => [거절] {label}선물 일일 거래 하드캡({hard_cap}회) 도달로 신규 진입 정지 중 (수동 명령 포함)")
                        cursor.execute("UPDATE signals SET status = 'SKIPPED_CIRCUIT_BREAKER' WHERE id = ?", (signal_id,))
                        conn.commit()
                        conn.close()
                        return

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
                    # 선물 1계약 위탁증거금 계산 (자동매매 경로와 동일한 공용 계산 —
                    # 2026-08-08 이전엔 여기만 요율 0.10 하드코딩이 남아있어 수동/TCA
                    # 주문에서 계약수가 2.11배 과대 산정되던 버그였다)
                    margin_per_contract = self._calc_futures_margin_per_contract(price)
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
                    # Kiwoom SendOrderFO: ord_kind (1:신규, 2:정정, 3:취소), slby_tp ("1":매도, "2":매수)
                    if signal_type == "LONG_ENTER":
                        ord_kind, slby_tp = 1, "2"   # 신규 매수 (롱 진입)
                    elif signal_type == "SHORT_ENTER":
                        ord_kind, slby_tp = 1, "1"   # 신규 매도 (숏 진입)
                    elif signal_type == "LONG_EXIT":
                        ord_kind, slby_tp = 1, "1"   # 신규 매도 (롱 청산)
                    elif signal_type == "SHORT_EXIT":
                        ord_kind, slby_tp = 1, "2"   # 신규 매수 (숏 청산)
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

                    ord_tp = "03" if self.environment == "live" else "3"
                    print(f"  => [선물 실계좌 전송] SendOrderFO 전송 (ord_kind:{ord_kind}, slby_tp:{slby_tp}, 수량:{qty}, 코드:{order_code})")
                    res = self.kiwoom.dynamicCall(
                        "SendOrderFO(QString, QString, QString, QString, int, QString, QString, int, QString, QString)",
                        ["FuturesOrder", "0101", self.futures_account, order_code, ord_kind, slby_tp, ord_tp, qty, "0", ""]
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
            
            # (2026-07-28 추가) 청산 주문이 15초 내 미체결로 재시도될 때 원주문을 취소하지 않고
            # 새 주문을 또 내보내던 문제가 실측 확인됨 (원주문이 실제로는 살아있다가 재시도 주문과
            # 함께 뒤늦게 둘 다 체결되어, SHORT 청산이 반대 방향 신규 LONG으로 뒤집힘). 취소 시
            # 원주문번호(OrgOrdNo)가 필요한데 기존엔 "체결" 상태에서만 주문번호를 읽었으므로,
            # 접수 단계에서도 코드별 최신 주문번호를 잡아둬서 재시도 시 취소에 사용한다.
            if status != "체결":
                _acc_order_no = self.kiwoom.dynamicCall("GetChejanData(int)", 9203).strip()
                _acc_code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().replace("A", "")
                if _acc_order_no:
                    if not hasattr(self, "_futures_last_order_no"):
                        self._futures_last_order_no = {}
                    self._futures_last_order_no[_acc_code] = _acc_order_no

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

                # 선물 체결 감지 (KOSPI200 주간/야간 코드 대조)
                _rd = getattr(self, 'real_day_code', '10100000').replace("A", "").strip()
                _rn = getattr(self, 'real_night_code', '10500000').replace("A", "").strip()
                if code == _rd or code == _rn:
                    if _rd == _rn:
                        is_night_fill = self._resolve_is_night_session(self.futures_positions)
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
                            self.futures_positions[pos_key] = {'type': 'LONG', 'qty': exec_qty, 'price': exec_price, 'code': code}
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
                                    import time
                                    if is_night_fill:
                                        self.futures_night_last_short_exit_price = exec_price
                                        self.futures_night_last_short_exit_time = time.time()
                                    else:
                                        self.futures_last_short_exit_price = exec_price
                                        self.futures_last_short_exit_time = time.time()
                                    self.save_futures_exit_state()
                            else:
                                self.futures_positions[pos_key]['qty'] += exec_qty
                        if notifier:
                            notifier.send_message(f"💰 <b>[{session_label}선물 매수 체결] 코스피200</b>\n• 체결가: {exec_price:,.2f}pt\n• 수량: {exec_qty}계약\n• 손절: -{self.futures_stop_loss_pt}pt | 익절: +{self.futures_take_profit_pt}pt")

                    elif "매도" in order_gubun or "전매" in order_gubun:
                        if pos_key not in self.futures_positions:
                            self.futures_positions[pos_key] = {'type': 'SHORT', 'qty': exec_qty, 'price': exec_price, 'code': code}
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
                                    import time
                                    if is_night_fill:
                                        self.futures_night_last_long_exit_price = exec_price
                                        self.futures_night_last_long_exit_time = time.time()
                                    else:
                                        self.futures_last_long_exit_price = exec_price
                                        self.futures_last_long_exit_time = time.time()
                                    self.save_futures_exit_state()
                            else:
                                self.futures_positions[pos_key]['qty'] += exec_qty
                        if notifier:
                            notifier.send_message(f"📉 <b>[{session_label}선물 매도 체결] 코스피200</b>\n• 체결가: {exec_price:,.2f}pt\n• 수량: {exec_qty}계약\n• 손절: -{self.futures_stop_loss_pt}pt | 익절: +{self.futures_take_profit_pt}pt")

                    self.export_status()
                    return

                # 주식 체결 처리
                print(f"[주식 실체결 확정] {name}({code}) | {exec_price:,.0f}원 | {exec_qty}주 | {order_gubun}")
                if "매수" in order_gubun:
                    pending = self.pending_orders.get(code, {})
                    if pending:
                        strat = pending.get('strategy', 'SWING')
                        open_p = pending.get('open_price', exec_price)
                    else:
                        # 30초 워치독이 지연 체결보다 먼저 pending_orders를 지운 케이스:
                        # SWING으로 기본값 처리하지 않고 signals 테이블의 최근 EXECUTED 신호에서 전략 태그를 복구
                        strat = 'SWING'
                        open_p = exec_price
                        try:
                            _conn = sqlite3.connect(self.unified_db_path, timeout=10)
                            _row = _conn.execute(
                                "SELECT strategy_type, open_price FROM signals WHERE code = ? AND status = 'EXECUTED' ORDER BY id DESC LIMIT 1",
                                (code,)
                            ).fetchone()
                            _conn.close()
                            if _row and _row[0] in ('DAY', 'SWING', 'DAY_CLOSE'):
                                # DAY_CLOSE(종가베팅)는 포지션 레벨에서는 항상 SWING으로 취급
                                strat = 'SWING' if _row[0] == 'DAY_CLOSE' else _row[0]
                                open_p = _row[1] if _row[1] else exec_price
                                print(f" => [ERA 주식] ⚠️ pending 소실 감지 → signals DB에서 전략 복구: {code}={strat}")
                        except Exception as _e:
                            print(f" => [ERA 주식] 전략 복구 쿼리 실패({_e}) → SWING 기본값 사용")

                    # 물리 분리 상태이고 pending에 기록이 없는 수동 진입 시, 계좌번호 문자열 매칭으로 전략 분기
                    if self.is_physical_separated and not pending:
                        chejan_acc = self.kiwoom.dynamicCall("GetChejanData(int)", 9201).strip()
                        if chejan_acc == self.stock_account_day:
                            strat = 'DAY'
                        elif chejan_acc == self.stock_account_swing:
                            strat = 'SWING'

                    # 대상 딕셔너리 선택
                    target_portfolio = self.portfolio_day if strat == 'DAY' else self.portfolio_swing

                    if code not in target_portfolio:
                        target_portfolio[code] = {
                            'name': name, 'strategy': strat, 'buy_price': exec_price, 'qty': 0,
                            'current_price': exec_price, 'max_price': exec_price, 'open_price': open_p,
                            'super_trend_mode': False, 'ma_10': 0, 'ma_20': 0,
                            'entry_date': datetime.now().strftime('%Y-%m-%d'),
                            'half_sold': False
                        }
                    else:
                        # 부분체결 평균단가 재계산
                        pos = target_portfolio[code]
                        prev_qty = pos['qty']
                        if prev_qty > 0:
                            pos['buy_price'] = (pos['buy_price'] * prev_qty + exec_price * exec_qty) / (prev_qty + exec_qty)

                    target_portfolio[code]['qty'] += exec_qty
                    self.portfolio[code] = target_portfolio[code] # 통합 딕셔너리 동기화

                    self.kiwoom.dynamicCall("SetRealReg(QString, QString, QString, QString)", "0102", code, "10", "1")
                    self.persist_positions()
                    self.export_status()

                    if notifier:
                        strat_name = "단타" if strat == 'DAY' else "스윙"
                        acc_lbl = self.stock_account_day if strat == 'DAY' else self.stock_account_swing
                        notifier.send_message(f"💰 <b>[{strat_name} 매수 체결] {name}</b>\n• 체결가: {exec_price:,.0f}원\n• 수량: {exec_qty}주\n• 계좌: {acc_lbl}")
                        
                elif "매도" in order_gubun:
                    if code in self.portfolio:
                        pos = self.portfolio[code]
                        strat = pos['strategy']
                        target_portfolio = self.portfolio_day if strat == 'DAY' else self.portfolio_swing
                        
                        # 해당 딕셔너리 수량 차감
                        if code in target_portfolio:
                            target_portfolio[code]['qty'] -= exec_qty
                            pos['qty'] = target_portfolio[code]['qty'] # 통합 딕셔너리 동기화
                        else:
                            pos['qty'] -= exec_qty
                        
                        profit = (exec_price - pos['buy_price']) * exec_qty
                        profit_pct = ((exec_price - pos['buy_price']) / pos['buy_price']) * 100
                        
                        # 해당 전략 계좌의 예수금 실시간 재조회
                        acc_to_query = self.stock_account_day if strat == 'DAY' else self.stock_account_swing
                        rq_name_to_use = "주식예수금조회_단타" if strat == 'DAY' else "주식예수금조회_스윙"
                        
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", acc_to_query)
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
                        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
                        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)", rq_name_to_use, "opw00001", 0, "0201")
                        
                        if profit < 0:
                            loss_amt = abs(profit)
                            self.stock_daily_loss += loss_amt
                            self.stock_monthly_loss += loss_amt
                            icon = "✂️"
                            # 일일 손실 서킷브레이커: 월초 기준잔고 대비 stock_daily_loss_limit_pct 초과 시
                            # 당일 신규 진입만 중단(청산은 정상 동작), 다음 거래일 08:40 리셋에서 자동 해제
                            if self.stock_monthly_initial > 0 and not self.stock_daily_halted:
                                daily_loss_ratio = self.stock_daily_loss / self.stock_monthly_initial
                                if daily_loss_ratio >= self.stock_daily_loss_limit_pct:
                                    self.stock_daily_halted = True
                                    print(f"[ERA 일일 손실 서킷브레이커] 일일 손실 {daily_loss_ratio:.1%} 초과(한도 {self.stock_daily_loss_limit_pct:.0%}) — 당일 신규 진입 중단!")
                                    if notifier:
                                        notifier.send_message(
                                            f"🛑 <b>[일일 손실 서킷브레이커]</b>\n"
                                            f"당일 손실: {daily_loss_ratio:.1%} (한도 {self.stock_daily_loss_limit_pct:.0%})\n"
                                            f"신규 진입이 당일 중단됩니다. 청산은 정상 동작하며, 다음 거래일 자동 해제됩니다."
                                        )
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
                            strat_name = "단타" if strat == 'DAY' else "스윙"
                            notifier.send_message(f"{icon} <b>[{strat_name} 매도 완료] {name}</b>\n• 체결가: {exec_price:,.0f}원\n• 손익률: {profit_pct:+.2f}%\n• 실현손익: {profit:+,}원\n🔄 가용 실예수금: {self.stock_total_balance:,}원")
                            
                        if pos['qty'] <= 0:
                            if code in target_portfolio:
                                del target_portfolio[code]
                            if code in self.portfolio:
                                del self.portfolio[code]
                            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "0102", code)
                            self.persist_positions()
                        self.export_status()

    def _on_receive_real_data(self, code, real_type, real_data):
        # OnReceiveRealData는 모든 실시간 틱마다 호출되는 콜백이라 예외가 그대로 전파되면
        # COM/OCX 콜스택을 타고 프로세스 전체가 네이티브 크래시(0xc0000409)로 죽을 수 있음.
        # OnReceiveTrData/OnReceiveChejanData와 동일하게 wrapper + _impl 구조로 흡수한다.
        try:
            self._handle_real_data(code, real_type, real_data)
        except Exception as e:
            import traceback
            print(f"[ERA 실시간 시세 콜백 오류] {e}\n{traceback.format_exc()}")

    def _handle_real_data(self, code, real_type, real_data):
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
            raw = self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 10).strip()
            if not raw:
                return
            current_price = abs(int(raw))

            # 테마 대장주 실시간 OHLCV 갱신 (포트폴리오 편입 전 모니터링)
            if code in self.theme_stocks and code not in self.portfolio:
                try:
                    tick_vol = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 11).strip() or 0))
                except (ValueError, AttributeError):
                    tick_vol = 0
                self._update_intraday_ohlcv(code, current_price, tick_vol)
                return
            elif code in self.portfolio and self.portfolio[code].get('strategy') == 'DAY':
                # DAY 포지션 보유 중에도 3분봉 갱신을 멈추지 않아야 MA10/20 트레일링
                # (super_trend_mode)이 매수 시점 값에 고정되지 않고 실제로 작동한다.
                # (return하지 않고 아래 청산 로직까지 계속 진행)
                try:
                    tick_vol = abs(int(self.kiwoom.dynamicCall("GetCommRealData(QString, int)", code, 11).strip() or 0))
                except (ValueError, AttributeError):
                    tick_vol = 0
                self._update_intraday_ohlcv(code, current_price, tick_vol)

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

                    # 최고 도달 수익률 추적 (본전 보장 손절 기준)
                    if profit_ratio > pos.get('max_profit_ratio', 0.0):
                        pos['max_profit_ratio'] = profit_ratio
                    max_profit_ratio = pos.get('max_profit_ratio', 0.0)

                    if profit_ratio <= -0.02:
                        sell_reason = "단타 고정 손절선(-2%) 도달"
                    elif max_profit_ratio >= 0.01 and profit_ratio <= 0.002:
                        sell_reason = "단타 본전 보장 손절(BE Stop) 도달"
                    else:
                        if super_trend_mode:
                            if current_price < ma_20 and ma_20 > 0:
                                sell_reason = "단타 20MA 하향 돌파 (Trailing Stop 종료)"
                            elif profit_ratio <= 0.01:
                                sell_reason = "+1.0% 최소 수익 보장선 이탈"
                        else:
                            if profit_ratio >= 0.02:
                                if ma_10 > 0 and ma_10_is_up and current_price >= ma_10:
                                    if not super_trend_mode:
                                        print(f"🌟 [{pos['name']}] 단타 수익 극대화 모드 진입!")
                                        pos['super_trend_mode'] = True
                                else:
                                    sell_reason = "단타 +2% 목표가 도달 (MA 하향)"
                                    
                # --- 스윙 로직 (가상 격리) ---
                elif strat == 'SWING':
                    # 장대양봉 시가 이탈 시 즉시 기계적 손절 (하드 스탑) — 보유기간 전체에 적용.
                    # 과거엔 진입 당일에만 적용되어, D+1일 이후 시가가 붕괴해도 5일선/10일선에
                    # 닿기 전까지는 무방비로 노출되는 안전장치 공백이 있었음
                    if pos['open_price'] and current_price < pos['open_price']:
                        sell_reason = f"스윙 기준봉 시가({pos['open_price']:,}원) 하향 이탈 (하드스탑)"
                        
                if sell_reason and not pos.get('sell_ordered'):
                    print(f"\n[🛡️ ERA 자동 청산 발동] {pos['name']} - {sell_reason}")
                    pos['sell_ordered'] = True
                    acc_to_use = self.stock_account_day if strat == 'DAY' else self.stock_account_swing
                    self.kiwoom.dynamicCall(
                        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                        ["[ERA_Auto_Sell]", "0103", acc_to_use, 2, code, pos['qty'], 0, "03", ""]
                    )

                    # 15초 내 체결 미확인 시 sell_ordered 해제 — 영구 잠김(매도 영원히 재시도 안되는 현상) 방지
                    def _clear_sell_ordered_if_no_fill(_code=code, _name=pos['name']):
                        p = self.portfolio.get(_code)
                        if p is not None and p.get('sell_ordered'):
                            print(f"[ERA] ⚠️ {_name} 매도 주문 15초 체결 미확인 → sell_ordered 해제 (재시도 허용)")
                            p['sell_ordered'] = False
                            if notifier:
                                notifier.send_message(
                                    f"⚠️ <b>[{_name} 매도 체결 미확인]</b>\n"
                                    f"15초 내 체결이 확인되지 않아 재시도가 허용됩니다."
                                )
                    QTimer.singleShot(15000, _clear_sell_ordered_if_no_fill)

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
                
                # 정기 점검 시간대에는 자동 재부팅을 유보 (점검 중에는 로그인 실패 루프 방지)
                now = datetime.now()
                hour_min = now.strftime("%H:%M")
                is_maintenance = False
                
                # 1) 매일 새벽 점검 (03:00 ~ 05:00)
                if "03:00" <= hour_min <= "05:00":
                    is_maintenance = True
                # 2) 평일 06:50 ~ 06:55 점검 (안전마진 포함 06:49 ~ 06:57)
                elif now.weekday() < 5 and "06:49" <= hour_min <= "06:57":
                    is_maintenance = True
                # 3) 일요일 05:00 ~ 05:15 점검 (일요일에는 _is_trading_day가 False이지만 안전용)
                elif now.weekday() == 6 and "04:59" <= hour_min <= "05:17":
                    is_maintenance = True
                
                if is_maintenance:
                    print(f"[ERA] 현재 키움 점검 시간대({hour_min})이므로 자동 재연결을 시도하지 않고 대기합니다.")
                    if notifier:
                        notifier.send_message(f"ℹ️ <b>[ERA]</b> 키움 점검 시간대({hour_min})로 감지되어 자동 재연결을 대기합니다.")
                    return
                
                if notifier:
                    notifier.send_message("⚠️ <b>[ERA]</b> 키움 연결 끊김 감지됨 → 시스템 자동 재기동 스크립트를 실행합니다.")
                
                # reconnect_kiwoom.bat 실행 (비동기 백그라운드 호출)
                reconnect_script = os.path.join(self.workspace_root, "reconnect_kiwoom.bat")
                import subprocess
                subprocess.Popen(f'cmd.exe /c "{reconnect_script}"', shell=True)
        except Exception as e:
            print(f"[ERA] keepalive 오류: {e}")

    def _check_kill_flag(self):
        """긴급정지 플래그 감시 — TCA가 생성한 emergency_kill.flag 감지 시 전량 청산 후 종료"""
        # 키움 통신만 재연결 플래그 감시
        reconnect_flag = os.path.join(self.workspace_root, "reconnect_kiwoom.flag")
        if os.path.exists(reconnect_flag):
            print("\n🔄 [ERA] 키움증권 통신 재연결 플래그 감지!")
            try:
                os.remove(reconnect_flag)
            except:
                pass
            try:
                state = self.kiwoom.dynamicCall("GetConnectState()")
                if state == 0:
                    if notifier:
                        notifier.send_message("🔄 <b>[ERA]</b> 키움증권 통신 재연결(CommConnect)을 시도합니다. (엔진 재부팅 없음)")
                    self.kiwoom.dynamicCall("CommConnect()")
                else:
                    if notifier:
                        notifier.send_message("ℹ️ <b>[ERA]</b> 키움증권 서버에 이미 정상 연결되어 있습니다. (연결 상태: 연결됨)")
            except Exception as e:
                print(f"[ERA] 통신 재연결 시도 중 에러: {e}")
                if notifier:
                    notifier.send_message(f"❌ <b>[ERA]</b> 키움증권 통신 재연결 시도 실패: {e}")

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
                    acc_to_use = self.stock_account_day if pos.get('strategy') == 'DAY' else self.stock_account_swing
                    self.kiwoom.dynamicCall(
                        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                        ["[ERA_KILL]", "0103", acc_to_use, 2, code, pos['qty'], 0, "03", ""]
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
