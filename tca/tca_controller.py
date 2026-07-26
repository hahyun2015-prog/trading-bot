import socket
# Force IPv4 globally to prevent IPv6 DNS resolution hangs and connection timeouts on Windows
orig_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo

import requests
import time
import subprocess
import os
import sys
import json
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(current_dir, "tca_controller.log")

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
workspace_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(workspace_root)

try:
    import notifier
except ImportError:
    notifier = None

class TCAController:
    def __init__(self):
        self.workspace_root = workspace_root
        self.config_path = os.path.join(workspace_root, "config", "config.json")
        self.status_file = os.path.join(workspace_root, "tca", "system_status.json")
        self.active_strategy_file = os.path.join(workspace_root, "config", "active_strategy.json")
        self.db_path = os.path.join(workspace_root, "unified_data.db")
        self.era_pid_file = os.path.join(workspace_root, "era", "era.pid")

        self.load_config()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.scheduler_state_path = os.path.join(workspace_root, "tca", "scheduler_state.json")
        self._bqa_check_timer = 0     # BQA 검사 주기 조절용

        # RSA/BQA 무인 스케줄러 중복 가동 방지용 마지막 실행일 — 메모리 변수만으로는 TCA가
        # 재시작(크래시/업데이트 등)되면 초기화되어 같은 날 다시 트리거될 수 있어서, 디스크에
        # 영속화된 상태 파일로 복원한다.
        today_str = datetime.now().strftime("%Y-%m-%d")
        state = self._load_scheduler_state()
        self._last_bqa_run_date = state.get("last_bqa_run_date", "")
        self._last_rsa_run_date = state.get("last_rsa_run_date", "")
        self._last_sta_run_date = state.get("last_sta_run_date", "")

        # 금일 RSA 실행 여부 추가 감지 (상태 파일이 없거나 유실된 경우의 2차 방어 — DB 실적 기준)
        if self._last_rsa_run_date != today_str:
            try:
                import sqlite3
                conn = sqlite3.connect(self.db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_reports'")
                if cursor.fetchone():
                    cursor.execute("SELECT COUNT(1) FROM research_reports WHERE date(timestamp) = ?", (today_str,))
                    if cursor.fetchone()[0] > 0:
                        self._last_rsa_run_date = today_str
                conn.close()
            except Exception as e:
                print(f"[TCA] 금일 RSA 이력 확인 실패: {e}")

    def _load_scheduler_state(self):
        """RSA/BQA 마지막 실행일을 디스크에서 복원 (TCA 재시작 시 중복 트리거 방지)"""
        try:
            if os.path.exists(self.scheduler_state_path):
                with open(self.scheduler_state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[TCA] 스케줄러 상태 파일 로드 실패: {e}")
        return {}

    def _save_scheduler_state(self):
        """RSA/BQA/STA 마지막 실행일을 디스크에 영속화"""
        try:
            with open(self.scheduler_state_path, "w", encoding="utf-8") as f:
                json.dump({
                    "last_rsa_run_date": self._last_rsa_run_date,
                    "last_bqa_run_date": self._last_bqa_run_date,
                    "last_sta_run_date": self._last_sta_run_date,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TCA] 스케줄러 상태 파일 저장 실패: {e}")

    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            # config_local.json 로컬 오버라이드 (동기화 제외 파일)
            local_config_path = os.path.join(workspace_root, "config", "config_local.json")
            if os.path.exists(local_config_path):
                with open(local_config_path, "r", encoding="utf-8") as f:
                    local_overrides = json.load(f)
                for key, val in local_overrides.items():
                    if isinstance(val, dict) and isinstance(config.get(key), dict):
                        config[key].update(val)
                    else:
                        config[key] = val
                print(f"[TCA] config_local.json 로컬 오버라이드 적용: {list(local_overrides.keys())}")

            telegram_cfg = config.get("telegram", {})
            # 모의투자 PC에 dev_bot_token이 있으면 그것을 우선 사용 (2대 동시 가동 시 충돌 방지)
            dev_token = telegram_cfg.get("dev_bot_token", "")
            env = config.get("environment", "mock")
            self.bot_token = dev_token if (dev_token and env != "live") else telegram_cfg.get("bot_token", "")
            self.allowed_chat_id = telegram_cfg.get("allowed_chat_id", 0)
            self.trading_mode = config.get("trading_mode", "both")
            self.features = config.get("features", {})
            self.futures_settings = config.get("futures_settings", {})
            self.isf_configs = config.get("individual_stock_futures", [])

            # venv32 경로
            venv32_rel = config.get("paths", {}).get("venv32_path", "venv32")
            if os.path.isabs(venv32_rel):
                self.venv32_path = venv32_rel
            else:
                self.venv32_path = os.path.join(workspace_root, venv32_rel)

            # 하이브리드 동기화: SMB → Google Drive → 로컬 자동 전환
            sync = config.get("sync", {})
            self.smb_path = sync.get("smb_path", "")
            self.gdrive_path = sync.get("gdrive_path", "")
            self.sync_fallback = sync.get("auto_fallback", True)

            # 레거시: network 섹션 호환 유지
            self.network_role = config.get("network", {}).get("role", "standalone")
            shared = config.get("network", {}).get("shared_db_path", "")
            if shared and not self.smb_path:
                self.smb_path = shared

            print(f"[TCA] trading_mode={self.trading_mode}, sync=[SMB:{bool(self.smb_path)}, GDrive:{bool(self.gdrive_path)}]")

        except Exception as e:
            print(f"[TCA Config Error] {e}")
            self.bot_token = ""
            self.allowed_chat_id = 0
            self.trading_mode = "both"
            self.features = {}
            self.isf_configs = []
            self.venv32_path = os.path.join(workspace_root, "venv32")
            self.smb_path = ""
            self.gdrive_path = ""
            self.sync_fallback = True
            self.network_role = "standalone"

    def _get_remote_root(self):
        """하이브리드 동기화: SMB → Google Drive → 로컬 자동 전환"""
        for path in [self.smb_path, self.gdrive_path]:
            if path and os.path.exists(os.path.join(path, "tca")):
                return path
        return workspace_root

    def _load_status_file(self, filename):
        """원격 또는 로컬에서 상태 파일 로드 (하이브리드 동기화 적용)"""
        # 1. 로컬에서 먼저 시도
        local_path = os.path.join(workspace_root, "tca", filename)
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                return json.load(f)
        # 2. 원격 경로에서 시도 (SMB → GDrive)
        remote_root = self._get_remote_root()
        if remote_root != workspace_root:
            remote_path = os.path.join(remote_root, "tca", filename)
            if os.path.exists(remote_path):
                with open(remote_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def _load_all_status(self):
        """모든 상태 파일을 통합하여 최신 활성 데이터 세트를 스마트하게 판별 후 반환"""
        stock_data = self._load_status_file("system_status_stock.json")
        futures_data = self._load_status_file("system_status_futures.json")
        single_data = self._load_status_file("system_status.json")
        
        files = []
        if stock_data:
            files.append(('stock', stock_data))
        if futures_data:
            files.append(('futures', futures_data))
        if single_data:
            files.append(('both', single_data))
            
        if not files:
            return None, None, None
            
        # last_updated 문자열 기준 내림차순 정렬하여 가장 최근 파일을 최우선 데이터로 지정
        files.sort(key=lambda x: x[1].get('last_updated', ''), reverse=True)
        newest_mode, _ = files[0]
        
        # 최신 파일이 both(system_status.json)일 경우, 주식/선물 데이터를 모두 system_status.json으로 단일화
        if newest_mode == 'both':
            return single_data, single_data, single_data
            
        active_stock = stock_data if newest_mode == 'stock' else (single_data if single_data else stock_data)
        active_futures = futures_data if newest_mode == 'futures' else (single_data if single_data else futures_data)
        
        return active_stock, active_futures, single_data

    def _open_positions_warning(self):
        """종료/재부팅류 명령 실행 전 보유 포지션 유무를 확인해 경고 문구를 만든다.
        손절/트레일링/익절이 브로커 상주 스탑주문이 아니라 ERA 프로세스 내부 폴링으로만
        동작하므로, ERA가 죽어있는 동안 보유 포지션은 완전히 무방비 상태가 된다."""
        try:
            stock_data, futures_data, single_data = self._load_all_status()
            s_data = stock_data or single_data or {}
            f_data = futures_data or single_data or {}
            stock_cnt = len(s_data.get("portfolio", {}) or {})
            futures_cnt = len(f_data.get("futures_positions", {}) or {})
            if stock_cnt == 0 and futures_cnt == 0:
                return ""
            return (
                f"\n\n⚠️ <b>[보유 포지션 주의]</b> 현재 주식 {stock_cnt}종목 / 선물 {futures_cnt}건 보유 중입니다.\n"
                f"ERA가 꺼져 있는 동안에는 손절/익절/트레일링이 전혀 동작하지 않으니 유의하세요."
            )
        except Exception as e:
            return f"\n\n⚠️ 보유 포지션 확인 실패({e}) — 확인 전 상태이니 주의하세요."

    def _atomic_write_json(self, path, data):
        """config_local.json을 임시파일에 쓴 뒤 os.replace로 원자적 치환한다.
        era_order_manager.py의 apply_to_config()도 같은 파일을 주기적으로
        read-modify-write하므로, 직접 write하면 두 프로세스가 겹치는 타이밍에
        한쪽 변경사항이 조용히 유실될 수 있었다."""
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, path)

    def send_message(self, text):
        if notifier:
            notifier.send_message(text)
        elif self.bot_token:
            url = f"{self.base_url}/sendMessage"
            payload = {"chat_id": self.allowed_chat_id, "text": text, "parse_mode": "HTML"}
            try:
                requests.post(url, json=payload, timeout=5)
            except Exception as e:
                print(f"[TCA Send Message Error] {e}")

    def _kill_era_process(self):
        """PID 파일을 이용해 ERA 프로세스를 종료하고, PID 파일이 유실/불일치해도
        cmdline 광범위 스캔으로 잔존 프로세스를 잡아내는 폴백을 항상 병행한다."""
        killed_any = False
        if os.path.exists(self.era_pid_file):
            try:
                with open(self.era_pid_file, "r") as f:
                    pid = f.read().strip()
                if pid:
                    pid_int = int(pid)

                    import psutil
                    try:
                        if psutil.pid_exists(pid_int):
                            proc = psutil.Process(pid_int)
                            cmdline = proc.cmdline()
                            is_era = any("era_order_manager" in arg for arg in cmdline)
                            if is_era:
                                proc.kill()
                                proc.wait(timeout=3)
                                killed_any = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as pe:
                        print(f"[TCA] Process kill exception: {pe}")

                    # 프로세스가 실제로 종료되었는지 검증
                    if psutil.pid_exists(pid_int):
                        print(f"[TCA] ERA 프로세스(PID: {pid}) 종료 실패 (권한 부족 등)")

                try:
                    os.remove(self.era_pid_file)
                except OSError:
                    pass
            except Exception as e:
                print(f"[TCA] PID 기반 종료 실패: {e}")

        # 2차 폴백: PID 파일이 없거나 이미 어긋난 상태에서도, cmdline에
        # era_order_manager가 포함된 모든 프로세스를 광범위 검색해 종료한다.
        # (check_process_status의 콘솔 타이틀 기반 2차 폴백과 견고성을 대칭화)
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline') or []
                    if any("era_order_manager" in arg for arg in cmdline):
                        proc.kill()
                        proc.wait(timeout=3)
                        killed_any = True
                        print(f"[TCA] 광범위 스캔으로 잔존 ERA 프로세스(PID: {proc.pid}) 종료")
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                    continue
        except Exception as e:
            print(f"[TCA] 광범위 ERA 프로세스 스캔 실패: {e}")

        return killed_any

    def check_process_status(self):
        try:
            era_status = "🔴 <b>꺼짐</b>"
            tca_status = "🟢 <b>정상 가동 중</b>"
            
            # 1. PID 파일을 이용해 실제 프로세스가 존재하는지 검증 (최강의 경량 안전성)
            if os.path.exists(self.era_pid_file):
                try:
                    with open(self.era_pid_file, "r") as f:
                        pid = f.read().strip()
                    if pid:
                        # tasklist를 활용해 해당 PID가 실제 살아있는지 검증
                        # wmic나 Get-CimInstance보다 100배 가볍고 신뢰도 높은 검증 방식
                        check_cmd = f"tasklist /fi \"PID eq {pid}\" /nh"
                        proc_out = subprocess.check_output(check_cmd, shell=True, text=True, errors='ignore')
                        if pid in proc_out and "python" in proc_out.lower():
                            era_status = "🟢 <b>정상 가동 중</b>"
                except Exception as e:
                    print(f"[TCA] PID 검증 오류: {e}")
                    
            # 2. 2차 방어 폴백: PID 파일이 유실되었어도 콘솔 창 제목으로 ERA를 특정해서 스캔
            # (startup.bat이 `start "AMATS | ERA" ...`로 띄우므로 그 콘솔 타이틀이 그대로 보존됨.
            #  과거에는 단순히 python.exe 프로세스 수 >= 2로 판단해서, 이 점검 스크립트처럼 무관한
            #  파이썬 프로세스가 떠 있기만 해도 ERA가 켜진 것으로 오탐했음)
            if era_status == "🔴 <b>꺼짐</b>":
                try:
                    proc_out = subprocess.check_output(
                        "tasklist /v /fi \"imagename eq python.exe\" /fo csv", shell=True, text=True, errors='ignore'
                    )
                    if "AMATS | ERA" in proc_out or "AMATS|ERA" in proc_out:
                        era_status = "🟢 <b>정상 가동 중 (창 제목 기반 추정)</b>"
                except Exception:
                    pass
                
            cmds = []
            if self.trading_mode in ('stock', 'both'):
                cmds.append("!주식현황")
            if self.trading_mode in ('futures', 'both'):
                cmds.append("!선물현황")
            if self.trading_mode in ('futures', 'both') and self.isf_configs:
                cmds.append("!ISF상태")
            cmds.append("!최적화결과")
            cmd_str = ", ".join(cmds)

            msg = (
                "📊 <b>[AMATS 통합 시스템 가동 상태]</b>\n\n"
                f"💼 ERA 주문/리스크 엔진: {era_status}\n"
                f"📱 TCA 중앙 관제 컨트롤러: {tca_status}\n\n"
                f"<i>(명령어: {cmd_str})</i>"
            )
            return msg
        except Exception as e:
            return f"상태 확인 중 오류 발생: {e}"

    def get_stock_status(self):
        try:
            stock_data, _, single_data = self._load_all_status()
            data = stock_data or single_data
            if not data:
                return "🚨 <b>주식 상태 데이터를 읽을 수 없습니다.</b>\nERA(주식) 엔진이 아직 구동되지 않았거나 데이터를 내보내지 않았습니다."
                
            total_balance = data.get("total_balance", 0)
            daily_loss = data.get("daily_realized_loss", 0)
            portfolio = data.get("portfolio", {})
            
            msg = f"📈 <b>[주식 가상 파티셔닝 현황]</b>\n"
            msg += f"💰 총 실예수금: {total_balance:,}원\n"
            msg += f"✂️ 당일 누적 손절한도 차감: -{daily_loss:,}원\n\n"
            
            if not portfolio:
                msg += "텅~ (현재 보유 중인 주식 포지션이 없습니다)\n"
            else:
                msg += "───────────────\n"
                total_invested = 0
                total_profit_amt = 0
                
                for code, pos in portfolio.items():
                    name = pos.get('name', code)
                    strat = "단타" if pos.get('strategy') == 'DAY' else "스윙"
                    buy_price = pos.get('buy_price', 1)
                    current_price = pos.get('current_price', buy_price)
                    qty = pos.get('qty', 0)
                    
                    profit_pct = ((current_price - buy_price) / buy_price) * 100
                    profit_amt = (current_price - buy_price) * qty
                    icon = "🔥" if profit_amt > 0 else ("💧" if profit_amt < 0 else "➖")
                    
                    total_invested += (buy_price * qty)
                    total_profit_amt += profit_amt
                    
                    msg += f"{icon} <b>[{strat}] {name}</b> ({qty}주)\n"
                    msg += f"  • 평단: {buy_price:,}원 ➡️ 현재: {current_price:,}원\n"
                    msg += f"  • 수익: <b>{profit_amt:+,}원</b> ({profit_pct:+.2f}%)\n\n"
                    
                total_profit_pct = (total_profit_amt / total_invested * 100) if total_invested > 0 else 0
                t_icon = "🔥" if total_profit_amt > 0 else ("💧" if total_profit_amt < 0 else "➖")
                
                msg += f"───────────────\n"
                msg += f"📊 <b>[주식 포트폴리오 총합]</b>\n"
                msg += f"  • 총 매입금액: {total_invested:,}원\n"
                msg += f"  • 총 평가수익: {t_icon} <b>{total_profit_amt:+,}원</b> ({total_profit_pct:+.2f}%)\n"
                msg += "───────────────\n"
                    
            msg += f"🕒 업데이트: {data.get('last_updated', '')}"
            return msg
        except Exception as e:
            return f"🚨 주식 현황 분석 실패: {e}"

    def get_futures_status(self):
        try:
            _, futures_data, single_data = self._load_all_status()
            data = futures_data or single_data
            if not data:
                return "🚨 <b>선물 상태 데이터를 읽을 수 없습니다.</b>\nERA(선물) 엔진이 아직 구동되지 않았거나 데이터를 내보내지 않았습니다."
                
            avail_balance = data.get("futures_balance", 0)
            positions = data.get("futures_positions", {})
            
            strat_info = data.get("futures_strategy", {})
            k_val = strat_info.get("K")
            prev_range = strat_info.get("prev_range")
            strat_type = strat_info.get("strategy_type", "volatility_breakout")

            msg = f"📉 <b>[국내 선물 계좌 현황]</b>\n"
            msg += f"💸 주문가능 현금: {avail_balance:,}원\n"
            msg += f"🛡️ 위탁증거금 30% 캡 가용액: {int(avail_balance * 0.3):,}원\n"
            if strat_type == "kalman":
                trend = strat_info.get("trend_direction", "NEUTRAL")
                night_trend = strat_info.get("night_trend_direction", "NEUTRAL")
                std_error = strat_info.get("std_error", 0.5)
                kf_sl_mult = strat_info.get("kf_sl_mult", 5.0)
                msg += f"🧠 전략: <b>칼만 필터(Kalman)</b>\n"
                msg += f"📐 주간 추세: <b>{trend}</b> | 야간 추세: <b>{night_trend}</b>\n"
                msg += f"📊 잔차 표준편차: {std_error:.2f} | 손절 배수: {kf_sl_mult:.1f}배 (하이브리드 동적) | 익절: 동적 3-Sigma\n"
            elif k_val is not None:
                msg += f"🎯 현재 적용 K값: <b>{k_val:.2f}</b>"
                if prev_range is not None:
                    msg += f" | 전일 Range: <b>{prev_range:.2f} pt</b>"
                msg += "\n"
            msg += "\n"

            
            if not positions:
                msg += "텅~ (현재 선물 진입 포지션이 없습니다)\n"
            else:
                for code, pos in positions.items():
                    p_type = pos.get('type', 'LONG')
                    p_label = "📈 LONG (매수)" if p_type == 'LONG' else "📉 SHORT (매도)"
                    buy_price = pos.get('price', 0)
                    qty = pos.get('qty', 0)
                    current_price = pos.get('current_price', buy_price)

                    # 국내선물 거래승수 (표준 250,000원, 미니 50,000원 판별)
                    # (2026-07-21 수정) code는 딕셔너리 키인 "KOSPI200"/"KOSPI200_NIGHT" 세션
                    # 라벨이라 '105' 포함 여부로 판별이 항상 실패해(표준 승수로 고정) 미니선물
                    # 실현손익이 5배로 부풀려 표시되던 버그. era가 export_status에 실어보내는
                    # 전역 상품접두사(futures_prefix)로 판별한다.
                    multiplier = 50000 if strat_info.get('prefix') == '105' else 250000
                    if p_type == 'LONG':
                        pnl = (current_price - buy_price) * qty * multiplier
                    else:
                        pnl = (buy_price - current_price) * qty * multiplier
                    
                    p_icon = "🔥" if pnl > 0 else ("💧" if pnl < 0 else "➖")
                    
                    msg += f"🎯 <b>{code}</b>\n"
                    msg += f"  • 방향: {p_label}\n"
                    msg += f"  • 진입단가: {buy_price:,.2f}pt ➡️ 현재가: {current_price:,.2f}pt\n"
                    msg += f"  • 보유수량: {qty}계약\n"
                    msg += f"  • 평가손익: {p_icon} <b>{int(pnl):+,}원</b>\n\n"
                    
            msg += f"🕒 업데이트: {data.get('last_updated', '')}"
            return msg
        except Exception as e:
            return f"🚨 선물 현황 분석 실패: {e}"

    def get_account_status(self):
        """현재 감지된 계좌 및 잔고 현황 (주식/선물 예수금, 투자금, 수익금 통합)"""
        try:
            stock_data, futures_data, single_data = self._load_all_status()
            
            # 단일 PC 모드 또는 분리 모드에서 데이터 추출
            s_data = stock_data or single_data or {}
            f_data = futures_data or single_data or {}
            
            if not s_data and not f_data:
                return "🚨 ERA 상태 데이터 없음. ERA가 실행 중인지 확인하세요."

            stock_acc   = s_data.get("stock_account", "") or "비활성"
            futures_acc = f_data.get("futures_account", "") or "비활성"
            
            # 주식 부문 잔고 및 투자금/수익금 계산
            stock_bal   = s_data.get("total_balance", 0)
            portfolio   = s_data.get("portfolio", {})
            stock_invested = 0
            stock_profit = 0
            for code, pos in portfolio.items():
                buy_price = pos.get('buy_price', 0)
                qty = pos.get('qty', 0)
                current_price = pos.get('current_price', buy_price)
                stock_invested += buy_price * qty
                stock_profit += (current_price - buy_price) * qty
            
            stock_profit_pct = (stock_profit / stock_invested * 100) if stock_invested > 0 else 0
            stock_total_val = stock_bal + stock_invested + stock_profit
            
            # 선물 부문 잔고 및 평가손익 계산
            fut_bal     = f_data.get("futures_balance", 0)
            futures_positions = f_data.get("futures_positions", {})
            futures_profit = 0
            for code, pos in futures_positions.items():
                p_type = pos.get('type', 'LONG')
                buy_price = pos.get('price', 0)
                qty = pos.get('qty', 0)
                current_price = pos.get('current_price', buy_price)

                # 국내선물 거래승수 (표준 250,000원, 미니 50,000원 판별)
                # (2026-07-21 수정) code는 "KOSPI200"/"KOSPI200_NIGHT" 세션 라벨이라 '105' 포함
                # 여부 판별이 항상 실패하던 버그 — era가 export_status에 실어보내는 전역
                # 상품접두사(futures_prefix)로 판별한다.
                fut_prefix = f_data.get("futures_strategy", {}).get("prefix")
                multiplier = 50000 if fut_prefix == '105' else 250000
                if p_type == 'LONG':
                    pnl = (current_price - buy_price) * qty * multiplier
                else:
                    pnl = (buy_price - current_price) * qty * multiplier
                futures_profit += pnl
                
            fut_total_val = fut_bal + futures_profit
            
            # 통합 계산
            total_cash = stock_bal + fut_bal
            total_invested = stock_invested
            total_profit = stock_profit + futures_profit
            total_assets = stock_total_val + fut_total_val
            
            # 통합 투자 원금 대비 전체 수익률 계산
            total_principal = total_cash + total_invested
            total_profit_pct = (total_profit / total_principal * 100) if total_principal > 0 else 0
            
            # 아이콘 결정
            s_icon = "🔥" if stock_profit > 0 else ("💧" if stock_profit < 0 else "➖")
            f_icon = "🔥" if futures_profit > 0 else ("💧" if futures_profit < 0 else "➖")
            t_icon = "🔥" if total_profit > 0 else ("💧" if total_profit < 0 else "➖")

            msg  = f"🔑 <b>[AMATS 통합 자산 및 계좌 현황]</b>\n\n"
            
            msg += f"📈 <b>주식 부문 (Stock Account)</b>\n"
            msg += f"  • 계좌번호: <code>{stock_acc}</code>\n"
            msg += f"  • 예수금(현금): {stock_bal:,}원\n"
            msg += f"  • 투자금액(매입): {stock_invested:,}원\n"
            msg += f"  • 평가손익(수익): {s_icon} <b>{stock_profit:+,}원</b> ({stock_profit_pct:+.2f}%)\n"
            msg += f"  • 주식자산평가액: {stock_total_val:,}원\n\n"
            
            msg += f"📉 <b>선물 부문 (Futures Account)</b>\n"
            msg += f"  • 계좌번호: <code>{futures_acc}</code>\n"
            msg += f"  • 예수금(현금): {fut_bal:,}원\n"
            msg += f"  • 평가손익(수익): {f_icon} <b>{int(futures_profit):+,}원</b>\n"
            msg += f"  • 선물자산평가액: {int(fut_total_val):,}원\n\n"
            
            msg += f"───────────────\n"
            msg += f"📊 <b>통합 자산 총합 (Combined Assets)</b>\n"
            msg += f"  • 총 현금자산: {total_cash:,}원\n"
            msg += f"  • 총 주식투자: {total_invested:,}원\n"
            msg += f"  • 총 평가손익: {t_icon} <b>{int(total_profit):+,}원</b> ({total_profit_pct:+.2f}%)\n"
            msg += f"  • <b>총 평가자산 (Net Worth): {int(total_assets):,}원</b>\n"
            msg += f"───────────────\n"
            msg += f"🕒 업데이트: {s_data.get('last_updated', '-')}"
            return msg
        except Exception as e:
            return f"🚨 계좌 확인 실패: {e}"

    def get_yield_status(self):
        """1주, 1달, 1분기, 1년 수익률 조회 및 분석"""
        try:
            import sqlite3
            from datetime import datetime, timedelta
            
            db_path = os.path.join(self.workspace_root, "unified_data.db")
            if not os.path.exists(db_path):
                return "🚨 <b>데이터베이스 오류</b>\n통합 데이터베이스를 찾을 수 없습니다."
                
            conn = sqlite3.connect(db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            cursor = conn.cursor()
            
            # 테이블 체크
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_balance_history'")
            if not cursor.fetchone():
                conn.close()
                return "🚨 <b>수익률 기록이 준비되지 않았습니다.</b>\nERA 주문 엔진이 구동되어 첫 기록을 남길 때까지 대기해주세요."
                
            # 최신 기록 조회
            cursor.execute("SELECT date, stock_total, futures_total, combined_total FROM daily_balance_history ORDER BY date DESC LIMIT 1")
            latest = cursor.fetchone()
            if not latest:
                conn.close()
                return "🚨 <b>기록된 자산 데이터가 없습니다.</b>"
                
            latest_date_str, latest_stock, latest_futures, latest_combined = latest
            latest_date = datetime.strptime(latest_date_str, "%Y-%m-%d")
            
            # 각 기간별 기준일 계산
            periods = {
                "1주 (7일 전)": (latest_date - timedelta(days=7)).strftime("%Y-%m-%d"),
                "1달 (30일 전)": (latest_date - timedelta(days=30)).strftime("%Y-%m-%d"),
                "1분기 (90일 전)": (latest_date - timedelta(days=90)).strftime("%Y-%m-%d"),
                "1년 (365일 전)": (latest_date - timedelta(days=365)).strftime("%Y-%m-%d"),
            }
            
            msg = f"📊 <b>[AMATS 기간별 투자 수익률 현황]</b>\n"
            msg += f"🕒 기준일자: <b>{latest_date_str}</b>\n"
            msg += f"💰 현재 총 자산: <b>{int(latest_combined):,}원</b>\n"
            msg += f"  • 주식자산: {int(latest_stock):,}원 | 선물자산: {int(latest_futures):,}원\n"
            msg += "───────────────────\n\n"
            
            for label, target_date_str in periods.items():
                # target_date_str보다 작거나 같은 날짜 중 가장 최근(가장 target_date에 근접한) 기록 찾기
                cursor.execute("""
                SELECT date, combined_total 
                FROM daily_balance_history 
                WHERE date <= ? 
                ORDER BY date DESC LIMIT 1
                """, (target_date_str,))
                past = cursor.fetchone()
                
                if past:
                    past_date_str, past_val = past
                    profit = latest_combined - past_val
                    profit_pct = (profit / past_val * 100) if past_val > 0 else 0
                    icon = "🔥" if profit > 0 else ("💧" if profit < 0 else "➖")
                    
                    msg += f"📈 <b>{label}</b> (기준: {past_date_str})\n"
                    msg += f"  • {icon} <b>수익률: {profit_pct:+.2f}%</b>\n"
                    msg += f"  • 평가손익: <b>{int(profit):+,}원</b>\n"
                    msg += f"  • 당시자산: {int(past_val):,}원\n\n"
                else:
                    msg += f"📈 <b>{label}</b>\n"
                    msg += "  • <i>(해당 기간의 과거 데이터가 아직 부족합니다)</i>\n\n"
                    
            conn.close()
            msg += "───────────────────\n"
            msg += "<i>※ 매일 장 마감 시점의 자산 총액을 기준으로 계산됩니다.</i>"
            return msg
        except Exception as e:
            return f"🚨 수익률 분석 실패: {e}"

    def execute_command(self, cmd_text, current_offset=None):
        if cmd_text == "!상태":
            msg = self.check_process_status()
            self.send_message(msg)

        elif cmd_text.startswith("!로그확인"):
            try:
                parts = cmd_text.split()
                lines_to_read = 30
                if len(parts) > 1:
                    lines_to_read = int(parts[1])
                
                log_path = os.path.join(self.workspace_root, "era", "era_order_manager.log")
                if not os.path.exists(log_path):
                    log_path = os.path.join(self.workspace_root, "era_test.log")
                if not os.path.exists(log_path):
                    log_path = os.path.join(self.workspace_root, "era", "era_crash.log")
                
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                    last_lines = lines[-lines_to_read:]
                    log_text = "".join(last_lines)
                    
                    if len(log_text) > 3000:
                        log_text = log_text[-3000:]
                        
                    self.send_message(
                        f"📋 <b>[ERA 최근 {len(last_lines)}줄 로그 브리핑]</b>\n"
                        f"<pre>{log_text}</pre>"
                    )
                else:
                    self.send_message("⚠️ ERA 로그 파일(era_order_manager.log)을 찾을 수 없습니다. 아직 매매 봇이 시작되지 않았거나 출력이 발생하지 않았을 수 있습니다.")
            except Exception as e:
                self.send_message(f"❌ 로그 조회 중 오류 발생: {e}")

        elif cmd_text.startswith("!TCA로그"):
            try:
                parts = cmd_text.split()
                lines_to_read = 30
                if len(parts) > 1:
                    lines_to_read = int(parts[1])
                
                log_path = os.path.join(self.workspace_root, "tca", "tca_controller.log")
                
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                    last_lines = lines[-lines_to_read:]
                    log_text = "".join(last_lines)
                    
                    if len(log_text) > 3000:
                        log_text = log_text[-3000:]
                        
                    self.send_message(
                        f"📋 <b>[TCA 최근 {len(last_lines)}줄 로그 브리핑]</b>\n"
                        f"<pre>{log_text}</pre>"
                    )
                else:
                    self.send_message("⚠️ TCA 로그 파일(tca_controller.log)을 찾을 수 없습니다.")
            except Exception as e:
                self.send_message(f"❌ TCA 로그 조회 중 오류 발생: {e}")

        elif cmd_text.startswith("!AI점검"):
            try:
                parts = cmd_text.split(" ", 1)
                if len(parts) < 2 or not parts[1].strip():
                    self.send_message(
                        "🤖 <b>[AI 브릿지 사용법]</b>\n\n"
                        "<code>!AI점검 [원하는 지시사항]</code> 형태로 보내주세요.\n"
                        "예: <code>!AI점검 선물 매니저 오류 분석하고 futures_order_manager.py 고쳐줘</code>"
                    )
                    return

                user_instruction = parts[1].strip()
                
                # 큐 파일 경로 계산 및 기록
                queue_path = os.path.join(self.workspace_root, "tca", "ai_bridge", "ai_task_queue.json")
                queue_dir = os.path.dirname(queue_path)
                if not os.path.exists(queue_dir):
                    os.makedirs(queue_dir, exist_ok=True)
                
                # 기존 큐 파일 로드
                if os.path.exists(queue_path):
                    with open(queue_path, "r", encoding="utf-8") as f:
                        queue_data = json.load(f)
                else:
                    queue_data = {"tasks": []}
                
                # 새로운 태스크 추가 (PENDING 상태)
                task_id = f"task_{int(time.time())}"
                new_task = {
                    "task_id": task_id,
                    "request": user_instruction,
                    "status": "PENDING",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                queue_data["tasks"].append(new_task)
                
                with open(queue_path, "w", encoding="utf-8") as f:
                    json.dump(queue_data, f, ensure_ascii=False, indent=4)
                
                self.send_message(
                    f"🔄 <b>[AI 원격 디버그 수신 완료]</b>\n\n"
                    f"👤 <b>지시:</b> <i>{user_instruction}</i>\n\n"
                    f"⏳ 백그라운드에서 AI 브릿지 워커(`ai_bridge_worker.py`)를 기동합니다. 분석 및 패치 완료 시 결과 알림이 전송됩니다. (약 30초 소요)"
                )
                
                # 백그라운드 프로세스로 ai_bridge_worker.py 비동기 기동
                worker_script = os.path.join(self.workspace_root, "tca", "ai_bridge", "ai_bridge_worker.py")
                py_exec = os.path.join(self.venv32_path, "Scripts", "python.exe")
                if not os.path.exists(py_exec):
                    py_exec = "python"
                
                log_dir = os.path.join(self.workspace_root, "tca", "ai_bridge")
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, "ai_bridge_worker.log")
                log_f = open(log_path, "ab")
                subprocess.Popen(
                    [py_exec, worker_script],
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    cwd=os.path.join(self.workspace_root, "tca"),
                    stdout=log_f,
                    stderr=log_f
                )
                log_f.close()
                print(f"[TCA] AI 워커 기동 완료 (Task: {task_id})")
                
            except Exception as e:
                self.send_message(f"❌ AI 디버그 수신기 구동 에러: {e}")

        elif cmd_text == "!계좌확인":
            msg = self.get_account_status()
            self.send_message(msg)

        elif cmd_text in ["!수익률", "!수익"]:
            msg = self.get_yield_status()
            self.send_message(msg)

            
        elif cmd_text == "!주식현황":
            msg = self.get_stock_status()
            self.send_message(msg)
            
        elif cmd_text == "!선물현황":
            msg = self.get_futures_status()
            self.send_message(msg)
            
        elif cmd_text.startswith("!계약수량"):
            try:
                parts = cmd_text.split(" ", 1)
                if len(parts) > 1:
                    qty_param = parts[1].strip()
                    
                    local_config_path = os.path.join(self.workspace_root, "config", "config_local.json")
                    if os.path.exists(local_config_path):
                        with open(local_config_path, "r", encoding="utf-8") as f:
                            local_config = json.load(f)
                    else:
                        local_config = {}
                    
                    if "futures_settings" not in local_config:
                        local_config["futures_settings"] = {}
                    
                    if qty_param in ("자동", "0", "auto"):
                        if "fixed_qty" in local_config["futures_settings"]:
                            del local_config["futures_settings"]["fixed_qty"]
                        msg = "✅ <b>선물 계약수량이 [자동 비례 계산]으로 전환되었습니다.</b>\n(예수금 한도 내 최대 계약으로 비례 배정)"
                    else:
                        qty_num = int(qty_param)
                        if qty_num <= 0:
                            raise ValueError("계약수량은 1 이상이어야 합니다.")
                        local_config["futures_settings"]["fixed_qty"] = qty_num
                        msg = f"✅ <b>선물 계약수량이 고정 [{qty_num}계약]으로 설정되었습니다.</b>"
                    
                    self._atomic_write_json(local_config_path, local_config)

                    # 하이브리드 동기화 경로(SMB/GDrive)로 백업 복사 — 예전 계산식은
                    # workspace_root와 동일 경로로 정규화되어 매번 SameFileError로 조용히
                    # 실패하는 죽은 코드였음. 실제 별도 경로(sync.smb_path/gdrive_path)가
                    # 설정된 경우에만 복사한다.
                    for remote_root in (self.smb_path, self.gdrive_path):
                        if not remote_root:
                            continue
                        try:
                            remote_config_dir = os.path.join(remote_root, "config")
                            if os.path.isdir(remote_config_dir):
                                backup_config_path = os.path.join(remote_config_dir, "config_local.json")
                                if os.path.abspath(backup_config_path) != os.path.abspath(local_config_path):
                                    import shutil
                                    shutil.copy2(local_config_path, backup_config_path)
                                    print(f"[TCA] config_local.json 백업 복사 완료: {backup_config_path}")
                        except Exception as ex:
                            print(f"[TCA] 백업 복사 실패({remote_root}): {ex}")
                    
                    self.load_config()
                    self.send_message(
                        f"{msg}\n"
                        f"⚠️ 변경된 수량은 ERA 주문 엔진이 다음 사이클에 자동으로 로드하여 즉시 적용합니다."
                    )
                else:
                    local_config_path = os.path.join(self.workspace_root, "config", "config_local.json")
                    fixed_qty = None
                    if os.path.exists(local_config_path):
                        with open(local_config_path, "r", encoding="utf-8") as f:
                            cfg = json.load(f)
                            fixed_qty = cfg.get("futures_settings", {}).get("fixed_qty", None)
                    
                    status_lbl = f"고정 <b>{fixed_qty}계약</b>" if fixed_qty is not None else "예수금 비례 <b>[자동 계산]</b>"
                    self.send_message(
                        f"📊 <b>[현재 선물 계약 수량 설정]</b>\n"
                        f"• 현재 수량: {status_lbl}\n\n"
                        f"💡 <b>수량 변경 방법:</b>\n"
                        f"• <code>!계약수량 1</code> : 1계약으로 고정 설정\n"
                        f"• <code>!계약수량 자동</code> : 잔고 비례 자동 계산으로 복원"
                    )
            except Exception as e:
                self.send_message(f"❌ 계약수량 설정 오류: {e}\n(예: `!계약수량 1` 또는 `!계약수량 자동`으로 입력해 주세요.)")
            
        elif cmd_text == "!주식시작" or cmd_text == "!선물시작" or cmd_text == "!시스템시작":
            try:
                self.send_message("⏳ AMATS 통합 주문/리스크 엔진을 구동합니다...")
                # Kiwoom 버전 업데이트(opstarter) 충돌 방지: KOA Studio 선제 종료
                subprocess.run("taskkill /f /im KOA_STARTER.exe 2>nul", shell=True)
                # 기존 ERA 프로세스가 좀비로 남아있을 경우 정리
                self._kill_era_process()
                # config.json의 venv32 경로에서 32비트 Python 실행
                py32_path = os.path.join(self.venv32_path, "Scripts", "python.exe")
                if not os.path.exists(py32_path):
                    self.send_message(
                        f"⚠️ <b>[ERA 구동 실패]</b>\n32비트 Python을 찾을 수 없습니다.\n"
                        f"경로: <code>{py32_path}</code>\n"
                        f"<code>setup_env.bat</code>을 실행해 venv32를 먼저 생성하세요."
                    )
                    return
                era_script = os.path.join(workspace_root, "era", "era_order_manager.py")
                subprocess.Popen(f'start cmd /k "{py32_path} {era_script}"', shell=True)
                self.send_message("✅ AMATS 통합 주문/리스크 엔진 가동 시작 명령이 전달되었습니다. (기동 확인까지 최대 20초 소요)")

                # 신규 프로세스가 실제로 떠 있는지 지연 검증 — 포트 9991 싱글턴 락으로 신규
                # 프로세스가 조용히 종료돼도 무조건 성공 메시지만 나가던 상태 불일치를 방지
                import threading
                def _verify_era_started():
                    time.sleep(20)
                    try:
                        proc_out = subprocess.check_output(
                            "tasklist /v /fi \"imagename eq python.exe\" /fo csv", shell=True, text=True, errors='ignore'
                        )
                        if "AMATS | ERA" in proc_out or "AMATS|ERA" in proc_out:
                            self.send_message("✅ <b>ERA 기동 확인 완료</b> — 프로세스가 정상적으로 실행 중입니다.")
                        else:
                            self.send_message(
                                "⚠️ <b>[ERA 기동 확인 실패]</b>\n20초 후에도 ERA 프로세스가 감지되지 않습니다.\n"
                                "포트 9991 싱글턴 락(구 인스턴스 잔존) 또는 키움 로그인 실패 가능성이 있습니다. 콘솔 창을 직접 확인해주세요."
                            )
                    except Exception as e:
                        print(f"[TCA] ERA 기동 검증 실패: {e}")
                threading.Thread(target=_verify_era_started, daemon=True).start()
            except Exception as e:
                self.send_message(f"❌ ERA 구동 명령 처리 중 오류: {e}")

        elif cmd_text == "!주식종료" or cmd_text == "!선물종료" or cmd_text == "!시스템종료":
            self.send_message("⏳ AMATS 통합 트레이딩 엔진 종료 중..." + self._open_positions_warning())
            if self._kill_era_process():
                self.send_message("✅ AMATS 통합 트레이딩 엔진이 정상 종료되었습니다.")
            else:
                self.send_message("⚠️ ERA PID 파일을 찾을 수 없습니다. ERA가 실행 중이지 않거나 이미 종료되었습니다.")

        elif cmd_text in ["!재연동", "!시스템재시작"]:
            try:
                self.send_message(
                    "🔄 <b>시스템 재연동 시퀀스 가동!</b>\n\n"
                    "1. 윈도우 작업 스케줄러 자동 감시를 일시정지합니다...\n"
                    "2. 기존 모든 ERA 및 키움증권 프로세스 강제 종료 중...\n"
                    "3. 키움증권 서버 세션 및 소켓 쿨타임(60초) 대기 후 안전하게 자동매매 창을 새로 엽니다."
                    + self._open_positions_warning()
                )
                # 윈도우 작업 스케줄러 임시 비활성화하여 쿨다운 기간 동안 가로채기 구동을 방지
                try:
                    subprocess.run('schtasks /change /tn "AMATS AutoStart" /disable', shell=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    print("[TCA] AMATS AutoStart 스케줄러 임시 비활성화 완료.")
                except Exception as e:
                    print(f"[TCA] 스케줄러 비활성화 실패: {e}")

                # 작업 스케줄러(RunLevel=Highest)에 등록된 태스크를 트리거 — UAC 동의창 없이 조용히
                # 승격되어 auto_reconnect_era.bat(60초 대기+재기동)을 실행함. 직접 .bat을 띄우면 그 안의
                # UAC 자체승격이 인터랙티브 데스크톱 없이는 pause에서 영원히 멈추는 문제가 있었음.
                subprocess.Popen('schtasks /run /tn "AMATS ERA Reconnect"', shell=True)
            except Exception as e:
                self.send_message(f"❌ 재연동 시퀀스 처리 중 오류: {e}")
        elif cmd_text == "!컴퓨터재부팅":
            self.send_message("🚨 <b>[원격 재부팅 명령 수신]</b>\n\n5초 후 Windows 시스템을 강제로 재부팅합니다. 재부팅 완료 후 자동 로그인 설정을 통해 시스템이 순차적으로 자동 재기동됩니다." + self._open_positions_warning())
            import time
            time.sleep(2)
            os.system("shutdown /r /t 5 /f")
        elif cmd_text == "!컴퓨터종료":
            self.send_message("🔌 <b>[원격 일반 종료 명령 수신]</b>\n\n윈도우 시작 메뉴의 시스템 종료와 동일한 방식으로 컴퓨터를 안전하게 종료합니다. (저장되지 않은 파일이 있으면 대기하거나 취소될 수 있습니다.)" + self._open_positions_warning())
            import time
            time.sleep(2)
            os.system("shutdown /s /t 5")
        elif cmd_text == "!컴퓨터강제종료":
            self.send_message("🚨 <b>[원격 강제 종료 명령 수신]</b>\n\n열려 있는 모든 프로그램을 강제로 닫고 즉시 컴퓨터를 종료합니다." + self._open_positions_warning())
            import time
            time.sleep(2)
            os.system("shutdown /s /t 5 /f")
            
        elif cmd_text.startswith("!매도"):
            try:
                parts = cmd_text.split(" ", 1)
                if len(parts) > 1:
                    target_name = parts[1].strip()
                    if not os.path.exists(self.status_file):
                        self.send_message("⚠️ 보유 정보를 찾을 수 없습니다. ERA 상태 json이 준비되지 않았습니다.")
                        return
                    with open(self.status_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    portfolio = data.get("portfolio", {})
                    target_code = None
                    for code, info in portfolio.items():
                        if info['name'] == target_name:
                            target_code = code
                            break
                    
                    if target_code:
                        import sqlite3
                        conn = sqlite3.connect(self.db_path, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL;")
                        cursor = conn.cursor()
                        cursor.execute('''INSERT INTO signals (code, name, strategy_type, price, status, open_price)
                                          VALUES (?, ?, 'MANUAL_SELL', 0, 'PENDING', 0)''', 
                                       (target_code, target_name))
                        conn.commit()
                        conn.close()
                        self.send_message(f"✅ <b>[{target_name}]</b> 수동 매도 신호가 DB에 적재되었습니다. (ERA가 즉시 시장가 전량 청산)")
                    else:
                        self.send_message(f"⚠️ <b>[{target_name}]</b> 종목이 현재 보유 주식 목록에 없습니다.")
            except Exception as e:
                self.send_message(f"❌ 매도 명령 처리 중 오류: {e}")
                
        elif cmd_text == "!전량매도":
            try:
                if not os.path.exists(self.status_file):
                    self.send_message("⚠️ 보유 주식이 없습니다.")
                    return
                with open(self.status_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                portfolio = data.get("portfolio", {})
                if portfolio:
                    import sqlite3
                    conn = sqlite3.connect(self.db_path, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL;")
                    cursor = conn.cursor()
                    for target_code, info in portfolio.items():
                        target_name = info['name']
                        cursor.execute('''INSERT INTO signals (code, name, strategy_type, price, status, open_price)
                                          VALUES (?, ?, 'MANUAL_SELL', 0, 'PENDING', 0)''', 
                                       (target_code, target_name))
                    conn.commit()
                    conn.close()
                    self.send_message(f"✅ 보유 중인 총 {len(portfolio)}개 주식 종목에 대한 수동 전량 청산 명령이 전달되었습니다.")
                else:
                    self.send_message(f"⚠️ 현재 보유 중인 주식 종목이 없습니다.")
            except Exception as e:
                self.send_message(f"❌ 전량 매도 명령 처리 중 오류: {e}")

        elif cmd_text in ["!선물매수", "!선물매도", "!선물청산"]:
            try:
                import sqlite3
                from datetime import datetime
                db_path = os.path.join(self.workspace_root, "futures_data.db")
                conn = sqlite3.connect(db_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL;")
                cursor = conn.cursor()

                # 1. 현재 시간에 따라 주간(10100000) / 야간(10500000) 코드 결정
                now_hour = datetime.now().hour
                active_code = "10500000" if (now_hour >= 17 or now_hour < 6) else "10100000"
                session_label = "야간" if active_code == "10500000" else "주간"

                # 2. DB에서 가장 최근 수신된 종목 가격 조회
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS futures_ohlcv (
                        code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                        UNIQUE(code, date)
                    )
                """)
                cursor.execute("SELECT close FROM futures_ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1", (active_code,))
                row = cursor.fetchone()
                current_price = row[0] if row else 400.0

                if cmd_text == "!선물매수":
                    cursor.execute("""
                        INSERT INTO signals (code, signal_type, price, status)
                        VALUES (?, 'LONG_ENTER', ?, 'PENDING')
                    """, (active_code, current_price))
                    conn.commit()
                    self.send_message(f"✅ <b>[{session_label}선물]</b> 수동 매수(LONG) 진입 명령이 전달되었습니다. (기준가: {current_price:.2f}pt)")

                elif cmd_text == "!선물매도":
                    cursor.execute("""
                        INSERT INTO signals (code, signal_type, price, status)
                        VALUES (?, 'SHORT_ENTER', ?, 'PENDING')
                    """, (active_code, current_price))
                    conn.commit()
                    self.send_message(f"✅ <b>[{session_label}선물]</b> 수동 매도(SHORT) 진입 명령이 전달되었습니다. (기준가: {current_price:.2f}pt)")

                elif cmd_text == "!선물청산":
                    _, futures_data, single_data = self._load_all_status()
                    data = futures_data or single_data or {}
                    positions = data.get("futures_positions", {})
                    pos_found = False
                    for pos_key, pos_info in positions.items():
                        p_type = pos_info.get("type")
                        qty = pos_info.get("qty", 0)
                        if qty > 0 and p_type in ["LONG", "SHORT"]:
                            exit_signal = "LONG_EXIT" if p_type == "LONG" else "SHORT_EXIT"
                            cursor.execute("""
                                INSERT INTO signals (code, signal_type, price, status)
                                VALUES (?, ?, ?, 'PENDING')
                            """, (active_code, exit_signal, current_price))
                            conn.commit()
                            pos_found = True
                            self.send_message(f"✅ <b>[{session_label}선물]</b> 수동 청산({exit_signal}) 명령이 전달되었습니다. (보유: {p_type} {qty}계약, 기준가: {current_price:.2f}pt)")
                            break
                    if not pos_found:
                        self.send_message("⚠️ 현재 보유 중인 선물 포지션이 없습니다. (상태 파일에 포지션 없음)")

                conn.close()
            except Exception as e:
                self.send_message(f"❌ 선물 수동 명령 처리 중 오류: {e}")

        elif cmd_text == "긴급정지" or cmd_text == "!긴급정지":
            self.send_message("🚨 <b>긴급 정지 시퀀스 가동!</b> 🚨\n\n1. ERA 주문 엔진에 긴급정지 플래그 전송 중...")

            # emergency_kill.flag 생성 (로컬 + 원격 모두)
            flag_targets = [workspace_root]
            remote_root = self._get_remote_root()
            if remote_root != workspace_root:
                flag_targets.append(remote_root)
            # SMB와 GDrive 모두 시도 (분리된 PC에 확실히 전달)
            for path in [self.smb_path, self.gdrive_path]:
                if path and path not in flag_targets and os.path.exists(path):
                    flag_targets.append(path)
            for target in flag_targets:
                try:
                    flag_path = os.path.join(target, "emergency_kill.flag")
                    with open(flag_path, "w") as f:
                        f.write(datetime.now().isoformat())
                    print(f"[TCA] 긴급정지 플래그 생성: {flag_path}")
                except Exception as e:
                    print(f"[TCA] 플래그 생성 실패 ({target}): {e}")

            self.send_message("✅ 긴급정지 플래그가 전송되었습니다.\nERA가 1초 이내에 자동 감지하여 전량 청산 후 종료합니다.")

            # ERA 플래그 감지 후 10초 내 자체 종료 → 비동기 PID 정리 (루프 블로킹 방지)
            import threading
            def _cleanup_pid():
                time.sleep(10)
                if self._kill_era_process():
                    self.send_message("✅ <b>긴급 정지 완료:</b> 로컬 ERA 강제 종료 확인.")
                else:
                    self.send_message("✅ <b>긴급 정지 완료:</b> ERA 플래그 수신 후 자체 종료.")
            threading.Thread(target=_cleanup_pid, daemon=True).start()

        elif cmd_text == "!버전확인":
            try:
                log = subprocess.check_output(
                    ['git', '-C', workspace_root, 'log', '--oneline', '-5'],
                    text=True, stderr=subprocess.STDOUT, timeout=10
                ).strip()
                branch = subprocess.check_output(
                    ['git', '-C', workspace_root, 'rev-parse', '--abbrev-ref', 'HEAD'],
                    text=True, timeout=5
                ).strip()
                mode_label = {'stock': '주식 전용', 'futures': '선물 전용', 'both': '주식+선물'}.get(self.trading_mode, self.trading_mode)
                self.send_message(
                    f"📋 <b>[AMATS 버전 정보]</b>\n\n"
                    f"🖥 모드: {mode_label} | 브랜치: {branch}\n\n"
                    f"<b>최근 커밋 5개:</b>\n<code>{log}</code>\n\n"
                    f"💡 <code>!코드업데이트</code> 를 실행하면 최신 버전으로 즉시 패치됩니다."
                )
            except Exception as e:
                self.send_message(f"⚠️ 버전 확인 실패: {e}")

        elif cmd_text == "!코드업데이트":
            self.send_message(
                "🔄 <b>[코드 업데이트 시작]</b>\n"
                "GitHub origin/main 에서 최신 코드를 가져옵니다...\n"
                "<i>(ERA 실행 중이면 업데이트 후 수동 재시작 필요)</i>"
            )
            try:
                result = subprocess.check_output(
                    ['git', '-C', workspace_root, 'pull', 'origin', 'main'],
                    text=True, stderr=subprocess.STDOUT, timeout=30
                ).strip()
                commit_info = subprocess.check_output(
                    ['git', '-C', workspace_root, 'log', '--oneline', '-1'],
                    text=True, timeout=5
                ).strip()
                if 'Already up to date' in result or 'already up to date' in result.lower():
                    self.send_message(
                        f"✅ <b>[코드 업데이트]</b> 이미 최신 버전입니다.\n"
                        f"<code>{commit_info}</code>"
                    )
                else:
                    self.send_message(
                        f"✅ <b>[코드 업데이트 완료]</b>\n\n"
                        f"<code>{result}</code>\n\n"
                        f"현재 버전: <code>{commit_info}</code>\n"
                        f"⚠️ 변경사항 적용을 위해 ERA/TCA 를 재시작하세요.\n"
                        f"(<code>!시스템종료</code> → <code>!시스템시작</code>)"
                    )
            except subprocess.TimeoutExpired:
                self.send_message("⚠️ git pull 타임아웃 (30초). 네트워크 상태를 확인하세요.")
            except Exception as e:
                self.send_message(f"❌ 코드 업데이트 실패: {e}")

        elif cmd_text == "!RSA분석":
            if self._run_rsa_analysis():
                # 자동 스케줄러(08~15시, 금일 미실행 시 자동 트리거)와 동일하게 실행 이력을 갱신하지
                # 않으면, 수동 실행 직후 다음 폴링 주기에서 스케줄러가 조건을 여전히 참으로 보고
                # rsa_coordinator.py를 중복으로 재기동시켜 API 호출량이 두 배로 소모될 수 있었음.
                self._last_rsa_run_date = datetime.now().strftime("%Y-%m-%d")
                self._save_scheduler_state()
                self.send_message(
                    "🔬 <b>[RSA 분석 기동]</b>\n"
                    "단타/스윙 후보 종목에 대한 FAA·IRA·NSAA 정밀 리서치를 시작합니다.\n"
                    "완료 시 자동으로 결과를 알려드립니다."
                )
            else:
                self.send_message("❌ RSA 분석 실행에 실패했습니다.")

        elif cmd_text == "!백테스트시작":
            try:
                self.send_message("🧪 <b>[BQA]</b> 선물 최적화(K값 스위핑) 알고리즘을 즉시 강제 기동합니다...")
                bqa_script = os.path.join(workspace_root, "bqa", "batch_optimizer.py")
                log_dir = os.path.join(workspace_root, "bqa")
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, "bqa_optimizer.log")
                log_f = open(log_path, "ab")
                subprocess.Popen(f"python {bqa_script}", shell=True, stdout=log_f, stderr=log_f)
                log_f.close()
                self.send_message("✅ K값 최적화 엔진 기동 시작.")
            except Exception as e:
                self.send_message(f"❌ 백테스트 기동 실패: {e}")

        elif cmd_text == "!최적화결과":
            try:
                if not os.path.exists(self.active_strategy_file):
                    self.send_message("📊 최적화 결과가 아직 생성되지 않았습니다. !백테스트시작 명령으로 먼저 실행해 주세요.")
                    return
                with open(self.active_strategy_file, "r", encoding="utf-8") as f:
                    res_data = json.load(f)
                
                top_strats = res_data.get('top_strategies', [])
                msg = f"📊 <b>[BQA AI 퀀트 최적화 결과]</b>\n🕒 업데이트: {res_data.get('last_updated', '')}\n\n"
                
                if not top_strats:
                    msg += f"현재 최적화 K값: {res_data.get('best_k', 0.5)}\n"
                else:
                    for i, st in enumerate(top_strats):
                        msg += f"🏆 <b>Top {i+1}</b>\n"
                        msg += f"  • 파라미터 (K값): {st.get('K')}\n"
                        msg += f"  • 연환산수익률(CAGR): {st.get('cagr')}%\n"
                        msg += f"  • 승률: {st.get('win_rate')}%\n\n"
                    msg += (
                        "💡 Top 1(K="
                        + str(top_strats[0].get('K', res_data.get('best_k', 0.5)))
                        + ")은 기존 대비 개선된 경우 BQA가 이미 자동 적용했습니다(무인 운영 설계).\n"
                        + "<code>!전략승인</code>은 현재 파일을 재승인 스탬프(재확인용)하는 명령입니다."
                    )
                self.send_message(msg)
            except Exception as e:
                self.send_message(f"📊 최적화 결과 로드 실패: {e}")

        elif cmd_text == "!로드맵":
            try:
                import sqlite3
                
                # 1. KOSPI200 선물 5분봉 카운트
                futures_db = os.path.join(self.workspace_root, "futures_data.db")
                futures_count = 55328 # 디폴트
                if os.path.exists(futures_db):
                    try:
                        conn = sqlite3.connect(futures_db, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL;")
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM futures_ohlcv WHERE code='10500000'")
                        futures_count = cursor.fetchone()[0]
                        conn.close()
                    except Exception:
                        pass
                        
                # 2. 주식 5분봉 카운트
                kiwoom_db = os.path.join(self.workspace_root, "kiwoom_data.db")
                stock_count = 8478 # 디폴트
                if os.path.exists(kiwoom_db):
                    try:
                        conn = sqlite3.connect(kiwoom_db, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL;")
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM stock_ohlcv")
                        stock_count = cursor.fetchone()[0]
                        conn.close()
                    except Exception:
                        pass
                        
                # 3. RSA 분석 결과 카운트
                unified_db = os.path.join(self.workspace_root, "unified_data.db")
                rsa_count = 0
                if os.path.exists(unified_db):
                    try:
                        conn = sqlite3.connect(unified_db, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL;")
                        cursor = conn.cursor()
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_reports'")
                        if cursor.fetchone():
                            cursor.execute("SELECT COUNT(*) FROM research_reports")
                            rsa_count = cursor.fetchone()[0]
                        conn.close()
                    except Exception:
                        pass
                
                msg = (
                    "🗺️ <b>[AMATS 통합 시스템 상태 및 향후 고도화 로드맵]</b>\n"
                    f"📅 기준일자: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "📦 <b>실시간 데이터 자산 현황</b>\n"
                    f"• KOSPI200 선물 5분봉: <code>{futures_count:,}개</code> 누적\n"
                    f"• 국내주식 5분봉: <code>{stock_count:,}개</code> 누적\n"
                    f"• RSA AI 분석 결과: <code>{rsa_count:,}건</code> 적재 완료\n\n"
                    "🚀 <b>향후 60일 실행 계획 (Action Plan)</b>\n"
                    "• <b>Phase 1 (즉시)</b>: ERA 재시작을 통한 BQA 최적 파라미터 적용 및 오늘 이식된 하이브리드 동적 예산 배분/스윙 10MA 동적 청산/단타 고점 추적 스탑 운영 기동\n"
                    "• <b>Phase 2 (D+5)</b>: 신규 월물 교체에 따른 ISF 선물 코드 정밀 확인 및 업데이트\n"
                    "• <b>Phase 3 (D+30)</b>: 누적된 30일간의 NSAA 실데이터와 가격 변동 간의 상관관계 재검증\n"
                    "• <b>Phase 4 (D+60)</b>: NSAA 가중치 고도화를 반영한 ISF 백테스트 재실행 및 전략 패치\n\n"
                    "🛡️ <i>본 시스템은 백테스트 결과에 근거하여 통계적 우위를 확보하고 있으며, 시장 환경 변화에 따라 파라미터를 지속적으로 튜닝하여 운용할 예정입니다.</i>"
                )
                self.send_message(msg)
            except Exception as e:
                self.send_message(f"🚨 로드맵 생성 실패: {e}")

        elif cmd_text == "!전략승인":
            try:
                if not os.path.exists(self.active_strategy_file):
                    self.send_message("⚠️ 적용할 최적화 결과 파일이 존재하지 않습니다.")
                    return
                with open(self.active_strategy_file, "r", encoding="utf-8") as f:
                    res_data = json.load(f)
                    
                best_k = res_data.get('best_k', 0.5)
                res_data['approved_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                self._atomic_write_json(self.active_strategy_file, res_data)

                # ERA의 load_config()/_load_futures_k()는 이제 approved_at이 없는
                # active_strategy.json은 적용을 건너뛰므로, 이 명령은 (BQA 자동승인 여부와
                # 무관하게) approved_at을 다시 찍어 현재 파일이 확실히 로드되도록 보증한다.
                strat_type = res_data.get('futures_strategy_type', self.futures_settings.get('futures_strategy_type', 'volatility_breakout'))
                if strat_type == "kalman":
                    self.send_message(f"✅ <b>전략 승인 완료!</b>\n\n실전 선물 칼만 필터(Kalman) 전략 매개변수가 승인(approved_at 갱신)되었습니다. ERA가 다음 사이클에 로드합니다.\n(참고: 기존 대비 개선된 최적화 결과는 BQA가 매주 자동으로도 승인합니다 — 이 명령은 수동 재확인용입니다.)")
                else:
                    self.send_message(f"✅ <b>전략 승인 완료!</b>\n\n실전 선물 변동성 돌파 K값 매개변수(K={best_k})가 승인(approved_at 갱신)되었습니다. ERA가 다음 사이클에 로드합니다.\n(참고: 기존 대비 개선된 최적화 결과는 BQA가 매주 자동으로도 승인합니다 — 이 명령은 수동 재확인용입니다.)")
            except Exception as e:
                self.send_message(f"❌ 전략 승인 중 오류 발생: {e}")

        elif cmd_text == "!연구개시":
            try:
                self.send_message(
                    "🔬 <b>[AI 퀀트 연구원 가동]</b>\n"
                    "지난 30일간의 누적 데이터 및 성과 검토를 즉시 시작합니다. 잠시 후 결과 알림이 전송됩니다."
                )
                research_script = os.path.join(self.workspace_root, "bqa", "auto_quant_researcher.py")
                py32_path = os.path.join(self.venv32_path, "Scripts", "python.exe")
                python_cmd = py32_path if os.path.exists(py32_path) else "python"
                
                log_dir = os.path.join(self.workspace_root, "bqa")
                os.makedirs(log_dir, exist_ok=True)
                log_path = os.path.join(log_dir, "auto_quant_researcher.log")
                log_f = open(log_path, "ab")
                subprocess.Popen(
                    [python_cmd, research_script, "--manual"],
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    cwd=os.path.join(self.workspace_root, "bqa"),
                    stdout=log_f,
                    stderr=log_f
                )
                log_f.close()
            except Exception as e:
                self.send_message(f"❌ AI 연구원 기동 실패: {e}")

        # ── 개별주식선물(ISF) 명령어 ──────────────────────────────────────────

        elif cmd_text.startswith("!ISF코드"):
            # 사용법: !ISF코드 005930 2B005930202506
            # config_local.json의 futures_code를 텔레그램으로 직접 입력/수정
            try:
                parts = cmd_text.split()
                if len(parts) < 3:
                    self.send_message(
                        "📋 <b>사용법:</b> <code>!ISF코드 종목코드 선물코드</code>\n\n"
                        "예시:\n"
                        "• <code>!ISF코드 005930 2B005930202506</code>\n"
                        "• <code>!ISF코드 000660 2B000660202506</code>\n\n"
                        "선물 코드는 키움 HTS → 선물옵션 → 종목검색에서 확인"
                    )
                else:
                    stock_code = parts[1].strip().zfill(6)
                    futures_code = parts[2].strip()

                    local_cfg_path = os.path.join(self.workspace_root, "config", "config_local.json")
                    with open(local_cfg_path, "r", encoding="utf-8") as f:
                        lcfg = json.load(f)

                    isf_list = lcfg.get("individual_stock_futures", [])
                    updated = False
                    target_name = stock_code
                    for item in isf_list:
                        if item.get("stock_code", "") == stock_code:
                            item["futures_code"] = futures_code
                            target_name = item.get("name", stock_code)
                            updated = True
                            break

                    if not updated:
                        # 목록에 없으면 새로 추가
                        isf_list.append({
                            "stock_code": stock_code,
                            "name": stock_code,
                            "futures_code": futures_code,
                            "best_k": 0.35,
                            "stop_loss_pct": 1.5,
                            "take_profit_pct": 4.0,
                            "nsaa_long_min": 72,
                            "nsaa_short_max": 35
                        })
                        lcfg["individual_stock_futures"] = isf_list

                    self._atomic_write_json(local_cfg_path, lcfg)

                    self.send_message(
                        f"✅ <b>[ISF 코드 업데이트 완료]</b>\n\n"
                        f"• 종목: <b>{target_name}</b> ({stock_code})\n"
                        f"• 선물 코드: <code>{futures_code}</code>\n\n"
                        f"⚠️ ERA를 재시작해야 적용됩니다.\n"
                        f"(<code>!시스템재시작</code> 명령 실행)"
                    )
            except Exception as e:
                self.send_message(f"❌ ISF 코드 업데이트 오류: {e}")

        elif cmd_text == "!ISF상태":
            # ISF 설정, 오늘 방향, 현재 포지션 조회
            try:
                local_cfg_path = os.path.join(self.workspace_root, "config", "config_local.json")
                with open(local_cfg_path, "r", encoding="utf-8") as f:
                    lcfg = json.load(f)
                isf_list = lcfg.get("individual_stock_futures", [])

                if not isf_list:
                    self.send_message("⚠️ ISF 설정된 종목이 없습니다.\nconfig_local.json에 individual_stock_futures 항목을 추가하세요.")
                else:
                    # 오늘 방향 파일 읽기
                    dir_path = os.path.join(self.workspace_root, "config", "isf_direction.json")
                    directions = {}
                    if os.path.exists(dir_path):
                        with open(dir_path, "r", encoding="utf-8") as f:
                            directions = json.load(f)

                    # 현재 포지션 (system_status.json)
                    isf_positions = {}
                    try:
                        with open(self.status_file, "r", encoding="utf-8") as f:
                            status = json.load(f)
                        isf_positions = status.get("isf_positions", {})
                    except Exception:
                        pass

                    msg = "📊 <b>[ISF 개별주식선물 현황]</b>\n\n"
                    for item in isf_list:
                        sc = item.get("stock_code", "")
                        name = item.get("name", sc)
                        fc = item.get("futures_code", "")
                        k = item.get("best_k", 0.35)
                        sl = item.get("stop_loss_pct", 1.5)
                        tp = item.get("take_profit_pct", 4.0)

                        d_info = directions.get(sc, {})
                        direction = d_info.get("direction", "미확인")
                        nsaa = d_info.get("nsaa_score", "?")
                        dir_date = directions.get("_date", "?")

                        pos = isf_positions.get(sc)

                        dir_icon = {"LONG": "📈", "SHORT": "📉", "NEUTRAL": "⏸️"}.get(direction, "❓")
                        code_status = f"<code>{fc}</code>" if fc else "⚠️ <b>미입력</b>"

                        msg += (
                            f"<b>{name} ({sc})</b>\n"
                            f"  • 선물코드: {code_status}\n"
                            f"  • 전략: K={k} | 손절-{sl}% | 익절+{tp}%\n"
                            f"  • 오늘 방향({dir_date}): {dir_icon} {direction} (NSAA={nsaa}점)\n"
                        )
                        if pos:
                            pos_type = pos.get("type", "?")
                            pos_qty = pos.get("qty", 0)
                            pos_price = pos.get("price", 0)
                            msg += f"  • 현재 포지션: {pos_type} {pos_qty}계약 @ {pos_price:,}원\n"
                        else:
                            msg += f"  • 현재 포지션: 없음\n"
                        msg += "\n"

                    msg += f"💡 코드 미입력 종목: <code>!ISF코드 종목코드 선물코드</code>"
                    self.send_message(msg)
            except Exception as e:
                self.send_message(f"❌ ISF 상태 조회 오류: {e}")

        elif cmd_text == "!ISF방향":
            # research_reports에서 현재 NSAA 점수 조회 → 방향 표시
            try:
                import sqlite3
                today = datetime.now().strftime("%Y-%m-%d")
                local_cfg_path = os.path.join(self.workspace_root, "config", "config_local.json")
                with open(local_cfg_path, "r", encoding="utf-8") as f:
                    lcfg = json.load(f)
                isf_list = lcfg.get("individual_stock_futures", [])

                if not isf_list:
                    self.send_message("⚠️ ISF 설정 없음")
                else:
                    db_path = os.path.join(self.workspace_root, "unified_data.db")
                    conn = sqlite3.connect(db_path, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL;")
                    cursor = conn.cursor()
                    msg = f"🔍 <b>[ISF 오늘 NSAA 방향 점검]</b> ({today})\n\n"
                    for item in isf_list:
                        sc = item.get("stock_code", "")
                        name = item.get("name", sc)
                        long_min = item.get("nsaa_long_min", 72)
                        short_max = item.get("nsaa_short_max", 35)
                        long_only = item.get("long_only", False)
                        cursor.execute(
                            "SELECT nsaa_score, score FROM research_reports WHERE code=? AND date(timestamp)=? ORDER BY id DESC LIMIT 1",
                            (sc, today)
                        )
                        row = cursor.fetchone()
                        if row:
                            nsaa, total = row
                            if nsaa >= long_min:
                                d, icon = "LONG", "📈"
                            elif nsaa <= short_max and not long_only:
                                d, icon = "SHORT", "📉"
                            elif nsaa <= short_max and long_only:
                                # era_order_manager.py의 실거래 판정(_check_isf_direction)과 동일하게
                                # long_only 종목은 SHORT를 NEUTRAL로 강제 변환 — 조회 화면이 실거래
                                # 동작과 어긋나던 문제 수정
                                d, icon = "NEUTRAL (long_only)", "⏸️"
                            else:
                                d, icon = "NEUTRAL", "⏸️"
                            msg += (
                                f"{icon} <b>{name}</b>\n"
                                f"  NSAA={nsaa}점 / 종합={total}점 → <b>{d}</b>\n"
                                f"  (LONG≥{long_min} / SHORT≤{short_max})\n\n"
                            )
                        else:
                            msg += (
                                f"❓ <b>{name}</b>\n"
                                f"  오늘 RSA 분석 없음 → NEUTRAL\n"
                                f"  (<code>!RSA분석</code> 실행 후 재확인)\n\n"
                            )
                    conn.close()
                    self.send_message(msg)
            except Exception as e:
                self.send_message(f"❌ ISF 방향 조회 오류: {e}")

        elif cmd_text == "/start" or cmd_text == "!도움말":
            monitoring_items = [
                "• <code>!상태</code> : 시스템 가동 여부 점검",
                "• <code>!계좌확인</code> : 감지된 주식/선물 계좌 및 예수금 확인",
                "• <code>!수익률</code> : 1주, 1달, 1분기, 1년 기간별 투자 수익률 분석"
            ]
            
            show_stock = self.trading_mode in ('stock', 'both')
            if show_stock:
                monitoring_items.append("• <code>!주식현황</code> : 가상 파티셔닝(단타/스윙) 자금 및 수익률 브리핑")
                
            show_futures = self.trading_mode in ('futures', 'both')
            if show_futures:
                monitoring_items.append("• <code>!선물현황</code> : KOSPI200 선물 포지션 현황 브리핑")
                
            monitoring_items.extend([
                "• <code>!로그확인 [줄수]</code> : 실시간 매매 로그 확인 (기본 30줄)",
                "• <code>!TCA로그 [줄수]</code> : 실시간 관제 로그 확인 (기본 30줄)"
            ])
            
            # 2. 수동 제어 파트 구성
            control_items = []
            if show_stock:
                control_items.append("• <code>!매도 삼성전자</code> : 특정 종목 즉시 전량 청산")
                control_items.append("• <code>!전량매도</code> : 보유 중인 전 주식 시장가 청산")
            if show_futures:
                control_items.append("• <code>!선물매수</code> / <code>!선물매도</code> / <code>!선물청산</code> : 수동 선물 진입 및 청산")
                control_items.append("• <code>!계약수량 1</code> : 선물 계약 수량 수동 제어 (숫자/자동)")
                
            control_items.extend([
                "• <code>!시스템시작</code> / <code>!시스템종료</code> : 32비트 API 엔진 강제 온/오프",
                "• <code>!재연동</code> / <code>!시스템재시작</code> : 시스템 통합 프로세스 정리 후 안전 재기동 (추천)"
            ])
            
            # 3. 🚨 긴급 제어
            emergency_items = [
                "• <code>!긴급정지</code> : 모든 포지션 청산 후 봇 완전 킬"
            ]
            
            # 4. 🖥️ PC 원격 제어
            pc_control_text = (
                "\n<b>[🖥️ PC 원격 제어]</b>\n"
                "• <code>!컴퓨터종료</code> : Windows 시스템 안전 종료\n"
                "• <code>!컴퓨터강제종료</code> : 실행 중인 프로그램 강제 종료 후 시스템 즉시 종료\n"
                "• <code>!컴퓨터재부팅</code> : Windows 시스템 강제 재부팅\n"
            )
            
            # 5. 시스템 코드 업데이트 및 AI 자율 디버깅
            system_items = (
                "\n<b>[🔁 시스템 코드 업데이트]</b>\n"
                "• <code>!버전확인</code> : 현재 코드 버전 및 최근 커밋 확인\n"
                "• <code>!코드업데이트</code> : GitHub 최신 코드를 시스템에 즉시 적용 (git pull)\n\n"
                "<b>[🤖 AI 자율 디버깅]</b>\n"
                "• <code>!AI점검 [원하는 지시]</code> : 자연어로 원격 코드 복구 및 에러 점검\n"
                "  (예: <code>!AI점검 선물 매니저 오류 고쳐줘</code>)\n"
            )
            
            # RDP 주의사항
            rdp_warning = (
                "\n<b>[⚠️ RDP 연결 해제 주의사항]</b>\n"
                "• 원격 데스크톱(RDP) 세션 종료 시 일반 [X] 버튼으로 닫으면 GUI 기반 Kiwoom API가 차단될 수 있습니다.\n"
                "• 반드시 바탕화면 또는 시스템의 <code>disconnect_rdp_keep_alive.bat</code> 배치 파일을 실행하여 세션을 해제해주세요."
            )
            
            help_msg = (
                "🤖 <b>AMATS AI 원격 제어 작동 시작</b>\n\n"
                "<b>[실시간 관제]</b>\n"
                + "\n".join(monitoring_items) + "\n\n"
                + "<b>[수동 제어]</b>\n"
                + "\n".join(control_items) + "\n\n"
                + "<b>[🚨 긴급 제어]</b>\n"
                + "\n".join(emergency_items) + "\n"
                + pc_control_text
                + system_items
                + rdp_warning
            )
            self.send_message(help_msg)

    def _run_sta_screening(self):
        """STA 테마/스마트머니 종목선정(theme_tracker.py)을 백그라운드로 기동
        — 기존엔 run_sta.bat의 대화형 메뉴를 사람이 직접 실행해야만 동작해서, 안 돌리면
        ERA가 조용히 더 단순한 폴백(테마크롤링만/거래금액순위)으로 넘어가는 문제가 있었음.
        theme_tracker.py 자체는 입력 대기 없이 끝까지 자동 실행되므로 직접 기동 가능."""
        sta_script = os.path.join(self.workspace_root, 'sta', 'theme_tracker.py')
        py32_path = os.path.join(self.venv32_path, "Scripts", "python.exe")
        python_cmd = py32_path if os.path.exists(py32_path) else "python"
        log_dir = os.path.join(self.workspace_root, "sta")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "theme_tracker.log")
        try:
            log_f = open(log_path, "ab")
            subprocess.Popen(
                [python_cmd, sta_script],
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                stdout=log_f,
                stderr=log_f
            )
            log_f.close()
            return True
        except Exception as e:
            print(f"[TCA] STA 종목선정 실행 실패: {e}")
            return False

    def _run_rsa_analysis(self):
        """RSA 분석 (FAA·IRA·NSAA 정밀 리서치)을 백그라운드로 기동"""
        rsa_script = os.path.join(self.workspace_root, 'rsa', 'rsa_coordinator.py')
        py32_path = os.path.join(self.venv32_path, "Scripts", "python.exe")
        python_cmd = py32_path if os.path.exists(py32_path) else "python"
        log_dir = os.path.join(self.workspace_root, "rsa")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "rsa_coordinator.log")
        try:
            log_f = open(log_path, "ab")
            subprocess.Popen(
                [python_cmd, rsa_script],
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                stdout=log_f,
                stderr=log_f
            )
            log_f.close()
            return True
        except Exception as e:
            print(f"[TCA] RSA 분석 실행 실패: {e}")
            return False

    def _create_session(self):
        """TCP 연결 재사용을 위한 requests.Session 생성"""
        session = requests.Session()
        # keep-alive 연결 풀링으로 Read Timeout 대폭 감소
        adapter = requests.adapters.HTTPAdapter(
            max_retries=requests.packages.urllib3.util.retry.Retry(
                total=2,
                backoff_factor=1,
                status_forcelist=[502, 503, 504]
            ),
            pool_connections=1,
            pool_maxsize=2
        )
        session.mount('https://', adapter)
        return session

    def run_controller(self):
        print("==================================================")
        print("   TCA Central Controller (Waiting for commands)")
        print("==================================================")
        
        self.send_message("📡 <b>AMATS 중앙 관제 에이전트(TCA) 온라인.</b>\n(도움말: `!도움말`)")
        
        startup_time = time.time()
        offset = None
        fail_count = 0
        first_run = True
        session = self._create_session()
        
        while True:
            # ── Daily RSA 분석 자율 스케줄러 감시 ──
            try:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                # 매 영업일(월~금요일) 오전 08:00 ~ 15:30 사이이고, 금일 실행 이력이 없을 때 자동 트리거
                if now.weekday() < 5 and (8 <= now.hour <= 15) and self._last_rsa_run_date != today_str:
                    self._last_rsa_run_date = today_str
                    self._save_scheduler_state()
                    self.send_message(
                        "🔬 <b>[Daily RSA 자동 분석 개시]</b>\n"
                        "금일 단타/스윙 후보 종목에 대한 FAA·IRA·NSAA 정밀 리서치를 자동으로 기동합니다."
                    )
                    self._run_rsa_analysis()
            except Exception as e:
                print(f"[TCA RSA Scheduler Error] {e}")

            # ── STA 단타 종목선정(테마+스마트머니 수급필터) 자율 스케줄러 감시 ──
            # 기존엔 run_sta.bat의 대화형 메뉴를 사람이 직접 실행해야만 동작했음 — 자동매매
            # 파이프라인에 연동돼 있지 않아서, 실행을 빠뜨리면 ERA가 조용히 더 단순한 폴백
            # (테마크롤링만/거래금액순위)으로 넘어가는 구조적 문제가 있었음.
            # 09:01~09:10 사이로 잡은 이유: 너무 일찍(장 시작 전) 돌리면 외국인/기관 당일
            # 순매수(opt10059)가 사실상 0이라 스마트머니 필터가 무의미해지고, 너무 늦으면
            # ERA의 09:00~ 5분 주기 단타 스캔이 빈 후보로 먼저 돌게 됨.
            try:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                if (now.weekday() < 5 and now.hour == 9 and 1 <= now.minute <= 10
                        and self._last_sta_run_date != today_str):
                    self._last_sta_run_date = today_str
                    self._save_scheduler_state()
                    self.send_message(
                        "🎯 <b>[STA 단타 종목선정 자동 개시]</b>\n"
                        "테마 크롤링 + 외국인/기관 수급 필터를 자동으로 기동합니다."
                    )
                    self._run_sta_screening()
            except Exception as e:
                print(f"[TCA STA Scheduler Error] {e}")

            # ── BQA 주말 자율 최적화 스케줄러 감시 (매 getUpdates 주기마다 가볍게 시간 대조) ──
            try:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                # 매주 토요일 오전 05:00 ~ 05:59 사이이고, 오늘 실행된 적이 없을 때 자동 트리거
                # (토요일 05:00는 금요일 야간선물 세션이 04:45에 완벽히 마감 청산된 안전 직후 시점입니다!)
                if now.weekday() == 5 and now.hour == 5 and self._last_bqa_run_date != today_str:
                    self._last_bqa_run_date = today_str
                    self._save_scheduler_state()
                    self.send_message(
                        "🧪 <b>[BQA 주말 자율 최적화 개시]</b>\n"
                        "한 주간의 모든 거래가 마감되었습니다.\n"
                        "축적된 고해상도 DW 데이터를 바탕으로 자율 파라미터 최적화(K값 스위핑)를 시작합니다."
                    )
                    bqa_script = os.path.join(self.workspace_root, "bqa", "batch_optimizer.py")
                    # optimizer.py 등 BQA 메인 파일 감지
                    if not os.path.exists(bqa_script):
                        bqa_script = os.path.join(self.workspace_root, "bqa", "optimizer.py")
                    
                    if os.path.exists(bqa_script):
                        log_dir = os.path.join(self.workspace_root, "bqa")
                        os.makedirs(log_dir, exist_ok=True)
                        log_path = os.path.join(log_dir, "bqa_optimizer.log")
                        log_f = open(log_path, "ab")
                        # 백그라운드로 화면 없이 조용히 기동
                        subprocess.Popen(
                            [sys.executable, bqa_script],
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                            cwd=os.path.join(self.workspace_root, "bqa"),
                            stdout=log_f,
                            stderr=log_f
                        )
                        log_f.close()
                    else:
                        self.send_message("⚠️ BQA 최적화 스크립트 파일을 찾을 수 없습니다.")
            except Exception as e:
                print(f"[TCA BQA Scheduler Error] {e}")

            # ── 30일 주기 AI 퀀트 연구원 자율 스케줄러 감시 ──
            try:
                # 6시간에 한 번만 디스크 IO를 수행하도록 감시 주기 조절 (getUpdates는 30초 대기하므로 약 720 루프마다)
                self._bqa_check_timer += 1
                if self._bqa_check_timer >= 720 or self._bqa_check_timer == 1:
                    if self._bqa_check_timer >= 720:
                        self._bqa_check_timer = 2  # 1은 첫 실행 체크용이므로 2로 리셋
                    
                    cache_file = os.path.join(self.workspace_root, "config", "research_cache.json")
                    run_needed = False
                    last_run_dt = None
                    
                    if os.path.exists(cache_file):
                        with open(cache_file, "r", encoding="utf-8") as f:
                            cache = json.load(f)
                        last_run_str = cache.get("last_research_run")
                        if last_run_str:
                            try:
                                last_run_dt = datetime.strptime(last_run_str, "%Y-%m-%d")
                                if (datetime.now() - last_run_dt).days >= 30:
                                    run_needed = True
                            except Exception:
                                run_needed = True
                        else:
                            run_needed = True
                    else:
                        # 캐시 파일이 없으면 첫 실행으로 간주하고 연구를 실행합니다.
                        run_needed = True
                        
                    if run_needed:
                        self.send_message(
                            "🔬 <b>[AI 퀀트 연구원 정기 가동]</b>\n"
                            "마지막 분석 후 30일이 경과하여 자율 시스템 진단 및 업그레이드 연구를 시작합니다."
                        )
                        research_script = os.path.join(self.workspace_root, "bqa", "auto_quant_researcher.py")
                        py32_path = os.path.join(self.venv32_path, "Scripts", "python.exe")
                        python_cmd = py32_path if os.path.exists(py32_path) else "python"
                        
                        log_dir = os.path.join(self.workspace_root, "bqa")
                        os.makedirs(log_dir, exist_ok=True)
                        log_path = os.path.join(log_dir, "auto_quant_researcher.log")
                        log_f = open(log_path, "ab")
                        subprocess.Popen(
                            [python_cmd, research_script],
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                            cwd=os.path.join(self.workspace_root, "bqa"),
                            stdout=log_f,
                            stderr=log_f
                        )
                        log_f.close()
            except Exception as e:
                print(f"[TCA Auto Research Scheduler Error] {e}")

            if first_run:
                print("[TCA] 최초 기동: 이전 백로그 청소 중...")
                try:
                    response = session.get(f"{self.base_url}/getUpdates", params={'timeout': 1}, timeout=5)
                    data = response.json()
                    if data.get("ok") and data["result"]:
                        max_update_id = max(r["update_id"] for r in data["result"])
                        offset = max_update_id + 1
                        print(f"[TCA] 이전 백로그 {len(data['result'])}개 청소 완료. Next Offset: {offset}")
                    elif not data.get("ok") and data.get("error_code") == 409:
                        # 409 Conflict: 다른 봇 인스턴스가 getUpdates를 호출 중
                        print(f"[TCA 경고] 409 Conflict 감지 - 다른 봇 인스턴스와 충돌. 3초 후 재시도...")
                        time.sleep(3)
                except Exception as ex:
                    print(f"[TCA] 백로그 청소 실패: {ex}")
                first_run = False
                continue

            try:
                url = f"{self.base_url}/getUpdates"
                params = {'timeout': 30}
                if offset:
                    params['offset'] = offset
                    
                response = session.get(url, params=params, timeout=40)
                data = response.json()
                fail_count = 0
                
                if data.get("ok"):
                    for result in data["result"]:
                        offset = result["update_id"] + 1
                        
                        if "message" in result and "text" in result["message"]:
                            chat_id = result["message"]["chat"]["id"]
                            text = result["message"]["text"].strip()
                            msg_date = result["message"]["date"]
                            
                            if str(chat_id) != str(self.allowed_chat_id):
                                print(f"[보안 차단] 미인가 접근 시도! (ID: {chat_id}, Msg: {text})")
                                continue
                                
                            time_diff = time.time() - msg_date
                            
                            # 봇 기동 10초 이전의 완전히 오래된 과거 백로그는 skip
                            if msg_date < startup_time - 10:
                                print(f"[TCA 백로그 무시] 봇 기동 이전의 메시지 (지연: {time_diff:.1f}초): {text}")
                                continue
                                
                            # 만약 PC 시간이 5분 이상 빠르거나 늦어 서버와 시각 오차가 크게 날 경우 경고만 하고 명령어는 실행
                            if abs(time_diff) > 300:
                                print(f"[TCA 경고] PC 시간과 텔레그램 서버 시각이 {time_diff:.1f}초만큼 어긋나 있습니다.")
                                self.send_message(
                                    f"⚠️ <b>[시간 동기화 경고]</b>\n"
                                    f"NUCBOX 서버 시각과 텔레그램 서버 시각이 <b>{abs(time_diff):.1f}초</b> 어긋나 있습니다.\n"
                                    f"데이터 유실이나 매매 기록 왜곡을 방지하기 위해 윈도우 시간 동기화(<code>w32tm /resync</code>)를 실행해 주세요.\n"
                                    f"(이 명령어는 정상적으로 처리됩니다.)"
                                )
                                
                            print(f"명령어 수신: {text}")
                            self.execute_command(text, current_offset=offset)
                else:
                    error_desc = data.get("description", "Unknown error")
                    err_code = data.get("error_code", "Unknown code")
                    print(f"[TCA ERROR] Telegram API returned ok=False. Code: {err_code}, Description: {error_desc}")
                    
                    # 409 Conflict: 다른 봇 인스턴스의 getUpdates와 충돌
                    if err_code == 409:
                        print("[TCA] 409 Conflict 자동 복구: 세션 재생성 및 백로그 재청소 후 재시도합니다.")
                        session.close()
                        session = self._create_session()
                        time.sleep(3)
                        # 백로그 재청소로 offset 갱신
                        try:
                            resp = session.get(f"{self.base_url}/getUpdates", params={'timeout': 1}, timeout=5)
                            rdata = resp.json()
                            if rdata.get("ok") and rdata["result"]:
                                offset = max(r["update_id"] for r in rdata["result"]) + 1
                                print(f"[TCA] 409 복구 완료. New Offset: {offset}")
                        except Exception as ex:
                            print(f"[TCA] 409 복구 중 백로그 청소 실패: {ex}")
                    else:
                        time.sleep(10)
                            
            except Exception as e:
                print(f"Exception: {e}")
                fail_count += 1
                if fail_count >= 5:
                    print(f"[TCA] 5회 연속 통신 실패. HTTP 세션을 재생성합니다...")
                    session.close()
                    session = self._create_session()
                    fail_count = 0
                    time.sleep(10)
                else:
                    time.sleep(5)

if __name__ == "__main__":
    # ── Singleton 보장: 파일 잠금 (msvcrt exclusive lock) ──
    # 소켓 바인딩보다 더 신뢰할 수 있는 Windows 파일 잠금 사용
    import msvcrt
    _lock_file_path = os.path.join(current_dir, "tca.lock")
    try:
        _lock_fh = open(_lock_file_path, "w")
        msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
    except (IOError, OSError):
        print("[TCA] 이미 다른 TCA 컨트롤러가 실행 중입니다 (File Lock). 중복 실행을 방지하고 종료합니다.")
        sys.exit(0)

    tca_pid_file = os.path.join(current_dir, "tca.pid")
    try:
        with open(tca_pid_file, "w") as _f:
            _f.write(str(os.getpid()))
        print(f"[TCA] PID {os.getpid()} 기록 완료 ({tca_pid_file})")
    except Exception as e:
        print(f"[TCA] PID 기록 실패: {e}")

    # TCA가 떴다는 건 (startup.bat/수동실행/!시스템시작 등 경로 불문) 시스템을 다시 가동하겠다는
    # 뜻이므로, AMATS Watchdog가 "의도적 종료 상태"로 오인해 감시를 계속 건너뛰지 않도록 해제
    try:
        _stopped_flag = os.path.join(workspace_root, "system_stopped.flag")
        if os.path.exists(_stopped_flag):
            os.remove(_stopped_flag)
    except Exception:
        pass

    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        print("[TCA] 윈도우 절전 방지 활성화 완료.")
    except Exception as e:
        print(f"[TCA] 절전 방지 실패: {e}")

    # Note: schtasks /enable은 여기서 호출하지 않음 (startup.bat 재트리거로 인한 중복 실행 방지)
    # !재연동 명령에서 disable/enable 쌍이 필요한 경우에만 명시적으로 처리합니다.

    try:
        controller = TCAController()
        controller.run_controller()
    finally:
        if 'tca_pid_file' in locals() and os.path.exists(tca_pid_file):
            try:
                os.remove(tca_pid_file)
            except:
                pass
