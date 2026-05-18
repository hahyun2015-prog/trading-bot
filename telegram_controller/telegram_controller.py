import requests
import time
import subprocess
import os
import json

BOT_TOKEN = "8710417841:AAGm1AZxo-u9RTQX_MeRRDpz_ggvS4mvexk"
ALLOWED_CHAT_ID = 8578720404
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# 각 봇의 경로
UNIFIED_DIR = r"c:\antigravity\노트븍활용\unified_trader"
FUTURES_DIR = r"c:\antigravity\노트븍활용\futures_trader"
CONTROLLER_DIR = r"c:\antigravity\노트븍활용\telegram_controller"

def send_message(text):
    url = f"{BASE_URL}/sendMessage"
    payload = {
        "chat_id": ALLOWED_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

def check_process_status():
    try:
        # WMIC를 이용해 실행 중인 파이썬 프로세스 목록을 가져옴
        output = subprocess.check_output('wmic process where "name=\'python.exe\'" get commandline', shell=True, text=True, errors='ignore')
        
        unified_status = "🔴 <b>꺼짐</b>"
        futures_status = "🔴 <b>꺼짐</b>"
        
        if "unified_order_manager.py" in output or "unified_auto_loop.py" in output:
            unified_status = "🟢 <b>정상 가동 중</b>"
            
        if "futures_order_manager.py" in output or "futures_auto_loop.py" in output:
            futures_status = "🟢 <b>정상 가동 중</b>"
            
        msg = (
            "📊 <b>[현재 봇 가동 상태]</b>\n\n"
            f"📈 주식 봇(통합): {unified_status}\n"
            f"📉 선물 봇(주/야 통합): {futures_status}\n\n"
            "<i>(명령어: !주식시작, !주식종료, !주식재연결)</i>"
        )
        return msg
    except Exception as e:
        return f"상태 확인 중 오류 발생: {e}"

def get_unified_status():
    try:
        with open(os.path.join(CONTROLLER_DIR, "unified_status.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
            
        total_balance = data.get("total_balance", 0)
        daily_loss = data.get("daily_realized_loss", 0)
        portfolio = data.get("portfolio", {})
        
        msg = f"📈 <b>[주식 통합 봇 실시간 현황]</b>\n"
        msg += f"💰 총 자본금: {total_balance:,}원\n"
        msg += f"✂️ 당일 누적 손절: -{daily_loss:,}원\n\n"
        
        if not portfolio:
            msg += "텅~ (현재 보유 중인 종목이 없습니다)\n"
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
                msg += f"  • 매입: {buy_price:,}원 ➡️ 현재: {current_price:,}원\n"
                msg += f"  • 수익: <b>{profit_amt:+,}원</b> ({profit_pct:+.2f}%)\n\n"
                
            total_profit_pct = (total_profit_amt / total_invested * 100) if total_invested > 0 else 0
            t_icon = "🔥" if total_profit_amt > 0 else ("💧" if total_profit_amt < 0 else "➖")
            
            msg += f"───────────────\n"
            msg += f"📊 <b>[현재 포트폴리오 총합]</b>\n"
            msg += f"  • 총 매입금액: {total_invested:,}원\n"
            msg += f"  • 총 평가수익: {t_icon} <b>{total_profit_amt:+,}원</b> ({total_profit_pct:+.2f}%)\n"
            msg += "───────────────\n"
                
        msg += f"🕒 업데이트: {data.get('last_updated', '')}"
        return msg
    except Exception as e:
        return f"🚨 주식 봇 상태 파일을 읽을 수 없습니다.\n(봇이 꺼져 있거나 아직 데이터를 저장하지 않았습니다.)"

def get_futures_status():
    try:
        with open(os.path.join(CONTROLLER_DIR, "futures_status.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
            
        avail_balance = data.get("available_balance", 0)
        positions = data.get("positions", {})
        
        msg = f"📉 <b>[선물 봇 실시간 현황]</b>\n"
        msg += f"💸 주문가능 현금: {avail_balance:,}원\n\n"
        
        if not positions:
            msg += "텅~ (현재 진입한 포지션이 없습니다)\n"
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
        return f"🚨 선물 봇 상태 파일을 읽을 수 없습니다.\n(봇이 꺼져 있거나 아직 데이터를 저장하지 않았습니다.)"

def execute_command(cmd_text, current_offset=None):
    if cmd_text == "!상태":
        msg = check_process_status()
        send_message(msg)
        
    elif cmd_text == "!주식현황":
        msg = get_unified_status()
        send_message(msg)
        
    elif cmd_text == "!선물현황":
        msg = get_futures_status()
        send_message(msg)
        
    elif cmd_text == "!주식시작":
        send_message("⏳ 주식 봇 시작을 준비합니다... (기존 프로세스 정리 및 5초 대기)")
        subprocess.run("kill_unified_bot.bat", shell=True, cwd=UNIFIED_DIR, capture_output=True)
        time.sleep(5)
        subprocess.Popen("start run_unified_bot.bat", shell=True, cwd=UNIFIED_DIR)
        send_message("✅ 주식 봇 실행 명령이 전달되었습니다.")
        
    elif cmd_text == "!주식종료":
        send_message("⏳ 주식 봇을 안전하게 종료합니다...")
        subprocess.run("kill_unified_bot.bat", shell=True, cwd=UNIFIED_DIR, capture_output=True)
        send_message("✅ 주식 봇이 종료되었습니다.")
        
    elif cmd_text == "!선물시작":
        send_message("⏳ 선물 봇 시작을 준비합니다... (기존 프로세스 정리 및 5초 대기)")
        subprocess.run("kill_futures_bot.bat", shell=True, cwd=FUTURES_DIR, capture_output=True)
        time.sleep(5)
        subprocess.Popen("start run_futures_bot.bat", shell=True, cwd=FUTURES_DIR)
        send_message("✅ 선물 봇 실행 명령이 전달되었습니다.")
        
    elif cmd_text == "!선물종료":
        send_message("⏳ 선물 봇을 안전하게 종료합니다...")
        subprocess.run("kill_futures_bot.bat", shell=True, cwd=FUTURES_DIR, capture_output=True)
        send_message("✅ 선물 봇이 종료되었습니다.")
        
    elif cmd_text == "!주식재연결":
        send_message("🔄 주식 봇 재연결을 시작합니다...\n1. 기존 봇 강제 종료 중...")
        subprocess.run("kill_unified_bot.bat", shell=True, cwd=UNIFIED_DIR, capture_output=True)
        send_message("⏳ 키움증권 서버에서 기존 접속이 완전히 끊길 때까지 60초 대기합니다...")
        time.sleep(60) # 종료 대기 (키움증권 서버 세션 타임아웃 고려)
        send_message("2. 주식 봇 재시작 중...")
        subprocess.Popen("start run_unified_bot.bat", shell=True, cwd=UNIFIED_DIR)
        send_message("✅ 주식 봇 재연결 명령이 전달되었습니다.")
        
    elif cmd_text == "!선물재연결":
        send_message("🔄 선물 봇 재연결을 시작합니다...\n1. 기존 봇 강제 종료 중...")
        subprocess.run("kill_futures_bot.bat", shell=True, cwd=FUTURES_DIR, capture_output=True)
        send_message("⏳ 키움증권 서버에서 기존 접속이 완전히 끊길 때까지 60초 대기합니다...")
        time.sleep(60) # 종료 대기 (키움증권 서버 세션 타임아웃 고려)
        send_message("2. 선물 봇 재시작 중...")
        subprocess.Popen("start run_futures_bot.bat", shell=True, cwd=FUTURES_DIR)
        send_message("✅ 선물 봇 재연결 명령이 전달되었습니다.")

    elif cmd_text == "!텔레그램재연결":
        send_message("🔄 텔레그램 컨트롤러를 재시작합니다...\n잠시 후 다시 연결됩니다.")
        if current_offset is not None:
            try:
                requests.get(f"{BASE_URL}/getUpdates", params={'offset': current_offset, 'timeout': 1}, timeout=5)
            except:
                pass
        import sys
        sys.exit(0)

    elif cmd_text.startswith("!매도"):
        try:
            parts = cmd_text.split(" ", 1)
            if len(parts) > 1:
                target_name = parts[1].strip()
                with open(r"c:\antigravity\노트븍활용\telegram_controller\unified_status.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                portfolio = data.get("portfolio", {})
                target_code = None
                for code, info in portfolio.items():
                    if info['name'] == target_name:
                        target_code = code
                        break
                
                if target_code:
                    import sqlite3
                    conn = sqlite3.connect(r"c:\antigravity\노트븍활용\unified_trader\unified_data.db")
                    cursor = conn.cursor()
                    cursor.execute('''INSERT INTO signals (code, name, strategy_type, price, status, open_price)
                                      VALUES (?, ?, 'MANUAL_SELL', 0, 'PENDING', 0)''', 
                                   (target_code, target_name))
                    conn.commit()
                    conn.close()
                    send_message(f"✅ <b>[{target_name}]</b> 수동 매도 명령이 봇에 전달되었습니다. (시장가 전량 청산)")
                else:
                    send_message(f"⚠️ <b>[{target_name}]</b> 종목이 현재 보유 포트폴리오에 없습니다.")
        except Exception as e:
            send_message(f"❌ 매도 명령 처리 중 오류: {e}")

    elif cmd_text == "!전량매도":
        try:
            with open(r"c:\antigravity\노트븍활용\telegram_controller\unified_status.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            portfolio = data.get("portfolio", {})
            if portfolio:
                import sqlite3
                conn = sqlite3.connect(r"c:\antigravity\노트븍활용\unified_trader\unified_data.db")
                cursor = conn.cursor()
                for target_code, info in portfolio.items():
                    target_name = info['name']
                    cursor.execute('''INSERT INTO signals (code, name, strategy_type, price, status, open_price)
                                      VALUES (?, ?, 'MANUAL_SELL', 0, 'PENDING', 0)''', 
                                   (target_code, target_name))
                conn.commit()
                conn.close()
                send_message(f"✅ 보유 중인 총 {len(portfolio)}개 종목에 대한 수동 전량 매도 명령이 전달되었습니다.")
            else:
                send_message(f"⚠️ 현재 보유 중인 종목이 없습니다.")
        except Exception as e:
            send_message(f"❌ 전량 매도 명령 처리 중 오류: {e}")

    elif cmd_text == "/start" or cmd_text == "!도움말":
        help_msg = (
            "🤖 <b>AI 원격 제어 봇 작동 시작</b>\n\n"
            "<b>[사용 가능 명령어]</b>\n"
            "• <code>!상태</code> : 현재 켜져 있는지 확인\n"
            "• <code>!주식현황</code> / <code>!선물현황</code> : 실시간 수익률 및 잔고 브리핑\n"
            "• <code>!매도 삼성전자</code> : 보유 중인 특정 종목 수동 익절/손절 (시장가 전량)\n"
            "• <code>!전량매도</code> : 보유 중인 전체 주식 종목 즉시 청산\n"
            "• <code>!주식시작</code> / <code>!주식종료</code> / <code>!주식재연결</code>\n"
            "• <code>!선물시작</code> / <code>!선물종료</code> / <code>!선물재연결</code>\n"
            "• <code>!텔레그램재연결</code> : 메신저 봇 응답 지연 시 초기화\n\n"
            "<i>보안: 대표님 외 타인의 접근은 완벽히 차단됩니다.</i>"
        )
        send_message(help_msg)

def run_controller():
    print("==================================================")
    print("   Telegram Remote Controller (Waiting for commands)")
    print("==================================================")
    
    send_message("📡 <b>원격 제어 봇이 온라인 상태입니다.</b>\n(도움말: `!도움말`)")
    
    offset = None
    fail_count = 0
    
    while True:
        try:
            url = f"{BASE_URL}/getUpdates"
            params = {'timeout': 30}
            if offset:
                params['offset'] = offset
                
            response = requests.get(url, params=params, timeout=40)
            data = response.json()
            fail_count = 0 # 통신 성공 시 에러 카운트 초기화
            
            if data.get("ok"):
                for result in data["result"]:
                    offset = result["update_id"] + 1
                    
                    if "message" in result and "text" in result["message"]:
                        chat_id = result["message"]["chat"]["id"]
                        text = result["message"]["text"].strip()
                        msg_date = result["message"]["date"]
                        
                        # 보안 차단
                        if str(chat_id) != str(ALLOWED_CHAT_ID):
                            print(f"[보안 차단] 인가되지 않은 접근 시도! (Chat ID: {chat_id}, Msg: {text})")
                            continue
                            
                        # 5분 이상 지난 메시지(백로그)는 무시 (시간 오차 감안)
                        time_diff = time.time() - msg_date
                        if time_diff > 300:
                            print(f"[백로그 무시] 너무 오래된 메시지 (지연: {time_diff:.1f}초): {text}")
                            continue
                            
                        print(f"명령어 수신 (지연: {time_diff:.1f}초): {text}")
                        execute_command(text, current_offset=offset)
                        
        except Exception as e:
            print(f"Exception: {e}")
            import traceback
            traceback.print_exc()
            
            fail_count += 1
            if fail_count >= 5:
                print(f"[에러] 텔레그램 서버 통신 실패 5회 누적. 봇을 자동으로 재시작합니다.")
                import sys
                sys.exit(1)
                
            time.sleep(5)

if __name__ == "__main__":
    try:
        import ctypes
        # ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED = 0x80000003
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        print("[System] 윈도우 절전 모드 및 화면보호기 진입 방지 활성화 완료.")
    except Exception as e:
        print(f"[System] 절전 방지 실패: {e}")

    run_controller()
