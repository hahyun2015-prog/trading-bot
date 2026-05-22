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
            self.bot_token = config.get("telegram", {}).get("bot_token", "")
            self.allowed_chat_id = config.get("telegram", {}).get("allowed_chat_id", 0)

            # venv32 경로: config 기준 상대경로 → 절대경로 변환
            venv32_rel = config.get("paths", {}).get("venv32_path", "venv32")
            if os.path.isabs(venv32_rel):
                self.venv32_path = venv32_rel
            else:
                self.venv32_path = os.path.join(workspace_root, venv32_rel)

            # 2대PC 분산 운용 시 shared DB 경로
            self.network_role = config.get("network", {}).get("role", "standalone")
            shared = config.get("network", {}).get("shared_db_path", "")
            self.shared_db_root = shared if shared else workspace_root

        except Exception as e:
            print(f"[TCA Config Error] {e}")
            self.bot_token = ""
            self.allowed_chat_id = 0
            self.venv32_path = os.path.join(workspace_root, "venv32")
            self.network_role = "standalone"
            self.shared_db_root = workspace_root

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
            if not os.path.exists(self.status_file):
                return "🚨 <b>상태 데이터를 읽을 수 없습니다.</b>\nERA 엔진이 아직 구동되지 않았거나 데이터를 내보내지 않았습니다."
                
            with open(self.status_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
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
            if not os.path.exists(self.status_file):
                return "🚨 <b>상태 데이터를 읽을 수 없습니다.</b>\nERA 엔진이 아직 구동되지 않았거나 데이터를 내보내지 않았습니다."
                
            with open(self.status_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
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
            if not os.path.exists(self.status_file):
                return "🚨 ERA 상태 데이터 없음. ERA가 실행 중인지 확인하세요."
            with open(self.status_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            stock_acc   = data.get("stock_account", "미감지")
            futures_acc = data.get("futures_account", "미감지")
            env         = data.get("environment", "unknown")
            stock_bal   = data.get("total_balance", 0)
            fut_bal     = data.get("futures_balance", 0)

            msg  = f"🔑 <b>[계좌 확인 / {env}]</b>\n\n"
            msg += f"📈 주식 계좌: <code>{stock_acc}</code>\n"
            msg += f"   예수금: {stock_bal:,}원\n\n"
            msg += f"📉 선물 계좌: <code>{futures_acc}</code>\n"
            msg += f"   예수금: {fut_bal:,}원\n\n"

            if stock_acc == "미감지" or not stock_acc:
                msg += "⚠️ 주식 계좌 미감지 → config.json accounts.stock_account 에 직접 입력\n"
            if futures_acc == "미감지" or not futures_acc:
                msg += "⚠️ 선물 계좌 미감지 → 선물 모의투자 미신청이거나 accounts.futures_account 에 직접 입력\n"

            msg += f"\n💡 잘못된 경우: config.json → accounts 항목 수동 입력 후 ERA 재시작"
            msg += f"\n🕒 {data.get('last_updated', '')}"
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
            self.send_message("⏳ ERA 주문/리스크 관리 엔진을 구동합니다...")
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
            self.send_message("✅ ERA 주문/리스크 엔진 가동 시작 명령이 하달되었습니다.")
            
        elif cmd_text == "!주식종료" or cmd_text == "!선물종료" or cmd_text == "!시스템종료":
            self.send_message("⏳ ERA 트레이딩 엔진 종료 중...")
            if self._kill_era_process():
                self.send_message("✅ ERA 트레이딩 엔진이 정상 종료되었습니다.")
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

        elif cmd_text == "!긴급정지":
            self.send_message("🚨 <b>긴급 정지 시퀀스 가동!</b> 🚨\n\n1. 주식 전량 시장가 청산 명령 하달 중...")
            self.execute_command("!전량매도")

            # 2. 선물 포지션 반대매매 청산 신호 주입
            try:
                if os.path.exists(self.status_file):
                    with open(self.status_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    futures_positions = data.get("futures_positions", {})
                    if futures_positions:
                        import sqlite3
                        futures_db = os.path.join(self.workspace_root, "futures_data.db")
                        conn = sqlite3.connect(futures_db, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL;")
                        cursor = conn.cursor()
                        for code, pos in futures_positions.items():
                            if pos.get('type') == 'LONG':
                                signal_type = 'LONG_EXIT'
                            else:
                                signal_type = 'SHORT_EXIT'
                            cursor.execute(
                                "INSERT INTO signals (code, signal_type, price, status) VALUES (?, ?, 0, 'PENDING')",
                                (code, signal_type))
                        conn.commit()
                        conn.close()
                        self.send_message(f"📉 선물 포지션 {len(futures_positions)}건 반대매매 청산 신호가 DB에 전송되었습니다.")
                    else:
                        self.send_message("📉 현재 선물 포지션 없음 — 선물 청산 불필요.")
            except Exception as e:
                self.send_message(f"⚠️ 선물 청산 신호 전송 중 오류: {e}")

            self.send_message("⏳ 체결 확인을 위해 30초간 대기 후 시스템을 강제 정지합니다...")
            time.sleep(30)

            self.send_message("3. ERA 트레이딩 엔진 강제 종료 중...")
            if self._kill_era_process():
                self.send_message("✅ <b>긴급 정지 완료:</b> ERA 트레이딩 엔진이 완전히 차단되었습니다.")
            else:
                self.send_message("⚠️ ERA PID 파일 없음 - ERA가 이미 종료되었거나 실행 중이 아닙니다.")

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
                "• <code>!전략승인</code> : 최적 K값 파라미터 실전 즉시 적용 승인"
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
