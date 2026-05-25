import requests
import time
import subprocess
import os
import sys
import json
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
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

            self.bot_token = config.get("telegram", {}).get("bot_token", "")
            self.allowed_chat_id = config.get("telegram", {}).get("allowed_chat_id", 0)
            self.trading_mode = config.get("trading_mode", "both")

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
        """모든 상태 파일을 통합하여 반환 (stock + futures + 단일)"""
        stock_data = self._load_status_file("system_status_stock.json")
        futures_data = self._load_status_file("system_status_futures.json")
        single_data = self._load_status_file("system_status.json")
        return stock_data, futures_data, single_data

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
        """PID 파일을 이용해 ERA 프로세스만 정확히 종료"""
        if os.path.exists(self.era_pid_file):
            try:
                with open(self.era_pid_file, "r") as f:
                    pid = f.read().strip()
                subprocess.run(f"taskkill /f /pid {pid}", shell=True)
                try:
                    os.remove(self.era_pid_file)
                except OSError:
                    pass
                return True
            except Exception as e:
                print(f"[TCA] PID 기반 종료 실패: {e}")
        return False

    def check_process_status(self):
        try:
            # wmic 대신 modern PowerShell을 사용하여 실행 중인 python.exe의 CommandLine 조회
            cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | Select-Object -ExpandProperty CommandLine"]
            output = subprocess.check_output(cmd, text=True, errors='ignore')
            
            era_status = "🔴 <b>꺼짐</b>"
            tca_status = "🟢 <b>정상 가동 중</b>"
            
            if "era_order_manager.py" in output:
                era_status = "🟢 <b>정상 가동 중</b>"
                
            msg = (
                "📊 <b>[AMATS 통합 시스템 가동 상태]</b>\n\n"
                f"💼 ERA 주문/리스크 엔진: {era_status}\n"
                f"📱 TCA 중앙 관제 컨트롤러: {tca_status}\n\n"
                "<i>(명령어: !주식현황, !선물현황, !최적화결과)</i>"
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
            
            msg = f"📉 <b>[국내 선물 계좌 현황]</b>\n"
            msg += f"💸 주문가능 현금: {avail_balance:,}원\n"
            msg += f"🛡️ 위탁증거금 30% 캡 가용액: {int(avail_balance * 0.3):,}원\n\n"
            
            if not positions:
                msg += "텅~ (현재 선물 진입 포지션이 없습니다)\n"
            else:
                for code, pos in positions.items():
                    p_type = "📈 LONG (매수)" if pos.get('type') == 'LONG' else "📉 SHORT (매도)"
                    buy_price = pos.get('price', 0)
                    qty = pos.get('qty', 0)
                    
                    msg += f"🎯 <b>{code}</b>\n"
                    msg += f"  • 방향: {p_type}\n"
                    msg += f"  • 진입단가: {buy_price:,.2f}pt\n"
                    msg += f"  • 보유수량: {qty}계약\n"
                    
            msg += f"\n🕒 업데이트: {data.get('last_updated', '')}"
            return msg
        except Exception as e:
            return f"🚨 선물 현황 분석 실패: {e}"

    def get_account_status(self):
        """현재 감지된 계좌 및 잔고 현황"""
        try:
            stock_data, futures_data, single_data = self._load_all_status()
            
            # 단일 PC 모드 또는 분리 모드에서 데이터 추출
            s_data = stock_data or single_data or {}
            f_data = futures_data or single_data or {}
            
            if not s_data and not f_data:
                return "🚨 ERA 상태 데이터 없음. ERA가 실행 중인지 확인하세요."

            stock_acc   = s_data.get("stock_account", "") or "비활성"
            futures_acc = f_data.get("futures_account", "") or "비활성"
            stock_bal   = s_data.get("total_balance", 0)
            fut_bal     = f_data.get("futures_balance", 0)

            msg  = f"🔑 <b>[AMATS 통합 계좌 확인]</b>\n\n"
            msg += f"📈 <b>주식 계좌 (Stock Account)</b>\n"
            msg += f"   계좌: <code>{stock_acc}</code>\n"
            msg += f"   예수금: {stock_bal:,}원\n\n"
            msg += f"📉 <b>선물 계좌 (Futures Account)</b>\n"
            msg += f"   계좌: <code>{futures_acc}</code>\n"
            msg += f"   예수금: {fut_bal:,}원\n\n"
            msg += f"🕒 업데이트: {s_data.get('last_updated', '-')}"
            return msg
        except Exception as e:
            return f"🚨 계좌 확인 실패: {e}"

    def execute_command(self, cmd_text, current_offset=None):
        if cmd_text == "!상태":
            msg = self.check_process_status()
            self.send_message(msg)

        elif cmd_text == "!계좌확인":
            msg = self.get_account_status()
            self.send_message(msg)
            
        elif cmd_text == "!주식현황":
            msg = self.get_stock_status()
            self.send_message(msg)
            
        elif cmd_text == "!선물현황":
            msg = self.get_futures_status()
            self.send_message(msg)
            
        elif cmd_text == "!주식시작" or cmd_text == "!선물시작" or cmd_text == "!시스템시작":
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
            self.send_message("✅ AMATS 통합 주문/리스크 엔진 가동 시작 명령이 전달되었습니다.")
            
        elif cmd_text == "!주식종료" or cmd_text == "!선물종료" or cmd_text == "!시스템종료":
            self.send_message("⏳ AMATS 통합 트레이딩 엔진 종료 중...")
            if self._kill_era_process():
                self.send_message("✅ AMATS 통합 트레이딩 엔진이 정상 종료되었습니다.")
            else:
                self.send_message("⚠️ ERA PID 파일을 찾을 수 없습니다. ERA가 실행 중이지 않거나 이미 종료되었습니다.")
            
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
                        conn = sqlite3.connect(self.db_path)
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
                    conn = sqlite3.connect(self.db_path)
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
            rsa_script = os.path.join(workspace_root, 'rsa', 'rsa_coordinator.py')
            py32_path = os.path.join(self.venv32_path, "Scripts", "python.exe")
            python_cmd = py32_path if os.path.exists(py32_path) else "python"
            subprocess.Popen([python_cmd, rsa_script], shell=False)
            self.send_message(
                "🔬 <b>[RSA 분석 기동]</b>\n"
                "단타/스윙 후보 종목에 대한 FAA·IRA·NSAA 정밀 리서치를 시작합니다.\n"
                "완료 시 자동으로 결과를 알려드립니다."
            )

        elif cmd_text == "!백테스트시작":
            self.send_message("🧪 <b>[BQA]</b> 선물 최적화(K값 스위핑) 알고리즘을 즉시 강제 기동합니다...")
            bqa_script = os.path.join(workspace_root, "bqa", "batch_optimizer.py")
            subprocess.Popen(f"python {bqa_script}", shell=True)
            self.send_message("✅ K값 최적화 엔진 기동 시작.")

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
                    msg += "💡 위 전략 중 Top 1을 실전에 적용하려면 <code>!전략승인</code>을 입력하세요."
                self.send_message(msg)
            except Exception as e:
                self.send_message(f"📊 최적화 결과 로드 실패: {e}")

        elif cmd_text == "!전략승인":
            try:
                if not os.path.exists(self.active_strategy_file):
                    self.send_message("⚠️ 적용할 최적화 결과 파일이 존재하지 않습니다.")
                    return
                with open(self.active_strategy_file, "r", encoding="utf-8") as f:
                    res_data = json.load(f)
                    
                best_k = res_data.get('best_k', 0.5)
                res_data['approved_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                with open(self.active_strategy_file, "w", encoding="utf-8") as f:
                    json.dump(res_data, f, ensure_ascii=False, indent=4)
                    
                self.send_message(f"✅ <b>전략 핫-리로드 완료!</b>\n\n실전 선물 변동성 돌파 K값 매개변수(K={best_k})가 즉시 적용 승인되었습니다. ERA가 다음 사이클에 즉시 자동 로드합니다.")
            except Exception as e:
                self.send_message(f"❌ 전략 승인 중 오류 발생: {e}")

        elif cmd_text == "/start" or cmd_text == "!도움말":
            help_msg = (
                "🤖 <b>AMATS AI 원격 제어 작동 시작</b>\n\n"
                "<b>[실시간 관제]</b>\n"
                "• <code>!상태</code> : 시스템 가동 여부 점검\n"
                "• <code>!계좌확인</code> : 감지된 주식/선물 계좌 및 예수금 확인\n"
                "• <code>!주식현황</code> : 가상 파티셔닝(단타/스윙) 자금 및 수익률 브리핑\n"
                "• <code>!선물현황</code> : KOSPI200 선물 포지션 현황 브리핑\n\n"
                "<b>[수동 제어]</b>\n"
                "• <code>!매도 삼성전자</code> : 특정 종목 즉시 전량 청산\n"
                "• <code>!전량매도</code> : 보유 중인 전 주식 시장가 청산\n"
                "• <code>!시스템시작</code> / <code>!시스템종료</code> : 32비트 API 엔진 강제 온/오프\n\n"
                "<b>[🚨 긴급 제어]</b>\n"
                "• <code>!긴급정지</code> : 모든 포지션 청산 후 봇 완전 킬\n\n"
                "<b>[🔬 RSA AI 리서치]</b>\n"
                "• <code>!RSA분석</code> : 테마 단타/스윙 후보 종목 AI 정밀 분석 (FAA·IRA·NSAA)\n\n"
                "<b>[🧪 BQA 퀀트 최적화]</b>\n"
                "• <code>!백테스트시작</code> : K값 스위핑 백테스트 강제 구동\n"
                "• <code>!최적화결과</code> : 최적화 완료된 상위 CAGR 매개변수 브리핑\n"
                "• <code>!전략승인</code> : 최적 K값 파라미터 실전 즉시 적용 승인\n\n"
                "<b>[🔁 시스템 코드 업데이트]</b>\n"
                "• <code>!버전확인</code> : 현재 코드 버전 및 최근 커밋 확인\n"
                "• <code>!코드업데이트</code> : GitHub 최신 코드를 시스템에 즉시 적용 (git pull)"
            )
            self.send_message(help_msg)

    def run_controller(self):
        print("==================================================")
        print("   TCA Central Controller (Waiting for commands)")
        print("==================================================")
        
        self.send_message("📡 <b>AMATS 중앙 관제 에이전트(TCA) 온라인.</b>\n(도움말: `!도움말`)")
        
        offset = None
        fail_count = 0
        
        while True:
            try:
                url = f"{self.base_url}/getUpdates"
                params = {'timeout': 30}
                if offset:
                    params['offset'] = offset
                    
                response = requests.get(url, params=params, timeout=40)
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
                            if time_diff > 300:
                                continue
                                
                            print(f"명령어 수신: {text}")
                            self.execute_command(text, current_offset=offset)
                            
            except Exception as e:
                print(f"Exception: {e}")
                fail_count += 1
                if fail_count >= 5:
                    print(f"[TCA 에러] 5회 연속 통신 실패. 재연결 대기.")
                    time.sleep(10)
                time.sleep(5)

if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        print("[TCA] 윈도우 절전 방지 활성화 완료.")
    except Exception as e:
        print(f"[TCA] 절전 방지 실패: {e}")

    controller = TCAController()
    controller.run_controller()
