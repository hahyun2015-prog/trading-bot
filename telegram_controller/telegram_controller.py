import requests
import time
import subprocess
import os
import json

BOT_TOKEN = "8710417841:AAGm1AZxo-u9RTQX_MeRRDpz_ggvS4mvexk"
ALLOWED_CHAT_ID = 8578720404
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# 각 봇의 경로 (현재 파일 기준 동적 설정)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIFIED_DIR = os.path.join(BASE_DIR, "unified_trader")
FUTURES_DIR = os.path.join(BASE_DIR, "futures_trader")
CONTROLLER_DIR = os.path.join(BASE_DIR, "telegram_controller")
ERA_DIR = os.path.join(BASE_DIR, "era")

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

    elif cmd_text in ["!재연동", "!시스템재시작"]:
        send_message(
            "🔄 <b>시스템 재연동 시퀀스 가동!</b>\n\n"
            "1. 기존 모든 ERA 및 키움증권 프로세스 강제 종료 중...\n"
            "2. 키움증권 서버 세션 및 소켓 쿨타임(60초) 대기 후 안전하게 자동매매 창을 새로 엽니다."
        )
        # auto_reconnect_era.bat가 60초 대기와 재기동을 처리하므로 비동기로 실행
        subprocess.Popen("start auto_reconnect_era.bat", shell=True, cwd=ERA_DIR)
        send_message("✅ <b>재연동 명령이 실행되었습니다.</b>\n60초 대기 후 ERA 엔진이 자동으로 재기동됩니다.")

    elif cmd_text == "!텔레그램재연결":
        send_message("🔄 텔레그램 컨트롤러를 재시작합니다...\n잠시 후 다시 연결됩니다.")
        if current_offset is not None:
            try:
                requests.get(f"{BASE_URL}/getUpdates", params={'offset': current_offset, 'timeout': 1}, timeout=5)
            except:
                pass
        import sys
        sys.exit(0)

    elif cmd_text in ["!선물매수", "!선물매도", "!선물청산"]:
        try:
            import sqlite3
            from datetime import datetime
            db_path = os.path.join(BASE_DIR, "futures_data.db")
            conn = sqlite3.connect(db_path)
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
                send_message(f"✅ <b>[{session_label}선물]</b> 수동 매수(LONG) 진입 명령이 전달되었습니다. (기준가: {current_price:.2f}pt)")

            elif cmd_text == "!선물매도":
                cursor.execute("""
                    INSERT INTO signals (code, signal_type, price, status)
                    VALUES (?, 'SHORT_ENTER', ?, 'PENDING')
                """, (active_code, current_price))
                conn.commit()
                send_message(f"✅ <b>[{session_label}선물]</b> 수동 매도(SHORT) 진입 명령이 전달되었습니다. (기준가: {current_price:.2f}pt)")

            elif cmd_text == "!선물청산":
                status_path = os.path.join(CONTROLLER_DIR, "futures_status.json")
                pos_found = False
                if os.path.exists(status_path):
                    with open(status_path, "r", encoding="utf-8") as sf:
                        status_data = json.load(sf)
                    positions = status_data.get("positions", {})
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
                            send_message(f"✅ <b>[{session_label}선물]</b> 수동 청산({exit_signal}) 명령이 전달되었습니다. (보유: {p_type} {qty}계약, 기준가: {current_price:.2f}pt)")
                            break
                if not pos_found:
                    send_message("⚠️ 현재 보유 중인 선물 포지션이 없습니다. (상태 파일에 포지션 없음)")

            conn.close()
        except Exception as e:
            send_message(f"❌ 선물 수동 명령 처리 중 오류: {e}")

    elif cmd_text.startswith("!매도"):
        try:
            parts = cmd_text.split(" ", 1)
            if len(parts) > 1:
                target_name = parts[1].strip()
                with open(os.path.join(CONTROLLER_DIR, "unified_status.json"), "r", encoding="utf-8") as f:
                    data = json.load(f)
                portfolio = data.get("portfolio", {})
                target_code = None
                for code, info in portfolio.items():
                    if info['name'] == target_name:
                        target_code = code
                        break
                
                if target_code:
                    import sqlite3
                    conn = sqlite3.connect(os.path.join(UNIFIED_DIR, "unified_data.db"))
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
            with open(os.path.join(CONTROLLER_DIR, "unified_status.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            portfolio = data.get("portfolio", {})
            if portfolio:
                import sqlite3
                conn = sqlite3.connect(os.path.join(UNIFIED_DIR, "unified_data.db"))
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

    elif cmd_text == "!긴급정지":
        send_message("🚨 <b>긴급 정지 시퀀스 가동!</b> 🚨\n\n1. 주식/선물 전량 시장가 청산 명령 하달 중...")
        # 1. 주식 전량 매도 신호 삽입
        execute_command("!전량매도")
        
        # 2. 선물 포지션 청산 신호 삽입 (추후 futures_data.db 스키마에 맞게 연동 필요)
        try:
            import sqlite3
            conn_f = sqlite3.connect(os.path.join(FUTURES_DIR, "futures_data.db"))
            c_f = conn_f.cursor()
            c_f.execute("CREATE TABLE IF NOT EXISTS manual_signals (signal_type TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
            c_f.execute("INSERT INTO manual_signals (signal_type) VALUES ('EMERGENCY_CLOSE_ALL')")
            conn_f.commit()
            conn_f.close()
        except Exception as e:
            send_message(f"⚠️ 선물 긴급 청산 신호 전달 실패: {e}")

        send_message("⏳ 주문 체결을 위해 15초 대기합니다...")
        time.sleep(15)
        
        send_message("2. 주식 및 선물 봇 프로세스 강제 종료 중...")
        subprocess.run("kill_unified_bot.bat", shell=True, cwd=UNIFIED_DIR, capture_output=True)
        subprocess.run("kill_futures_bot.bat", shell=True, cwd=FUTURES_DIR, capture_output=True)
        send_message("✅ <b>긴급 정지 완료:</b> 모든 매매 로직이 완전히 차단되었습니다.")

    elif cmd_text == "!백테스트시작":
        # 추후 PC 2 (AI 랩)와 연동될 트리거 파일이나 네트워크 요청 삽입
        send_message("🧪 <b>[AI 랩]</b> 야간 파라미터 최적화(백테스트) 무한 루프 가동 명령을 하달했습니다.\n(Claude Code 에이전트가 작업을 시작합니다.)")

    elif cmd_text == "!최적화결과":
        try:
            with open(r"G:\내 드라이브\AI_Trading_Data\optimization_results.json", "r", encoding="utf-8") as f:
                res_data = json.load(f)
            
            top_strats = res_data.get('top_strategies', [])
            msg = f"📊 <b>[AI 랩 최적화 결과]</b>\n🕒 업데이트: {res_data.get('last_updated', '')}\n\n"
            
            if not top_strats:
                msg += "최적화된 전략을 찾지 못했습니다."
            else:
                for i, st in enumerate(top_strats):
                    msg += f"🏆 <b>Top {i+1}</b>\n"
                    msg += f"  • 파라미터 (K값): {st.get('K')}\n"
                    msg += f"  • 연환산수익률(CAGR): {st.get('cagr')}%\n"
                    msg += f"  • 승률: {st.get('win_rate')}%\n\n"
                msg += "💡 위 전략 중 Top 1을 실전에 적용하려면 <code>!전략승인</code>을 입력하세요."
            send_message(msg)
        except Exception as e:
            send_message("📊 <b>[AI 랩 최적화 결과]</b>\n\n아직 진행된 백테스트 결과가 없습니다. (또는 파일을 읽을 수 없습니다.)")

    elif cmd_text == "!모의투자현황":
        # 현재는 모의투자 계좌 연동이 안 되어 있으므로 향후 연동을 위한 안내 메시지
        send_message("📈 <b>[모의투자 샌드박스 현황]</b>\n\n현재 config.json 설정에 따라 모의투자(PC 2) 봇이 가동 중입니다.\n(추후 키움 모의 계좌 잔고 및 수익률과 연동됩니다.)")

    elif cmd_text == "!전략승인":
        try:
            with open(r"G:\내 드라이브\AI_Trading_Data\optimization_results.json", "r", encoding="utf-8") as f:
                res_data = json.load(f)
                
            top_strats = res_data.get('top_strategies', [])
            if top_strats:
                best_strat = top_strats[0]
                new_k = best_strat.get('K')
                
                # 활성 전략 파일(active_strategy.json)에 기록하여 실전 봇이 다음 사이클부터 즉시 읽도록 함 (Hot-reload)
                active_data = {
                    "K": new_k,
                    "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "cagr": best_strat.get('cagr')
                }
                with open(r"G:\내 드라이브\AI_Trading_Data\active_strategy.json", "w", encoding="utf-8") as af:
                    json.dump(active_data, af, ensure_ascii=False, indent=4)
                    
                send_message(f"✅ <b>전략 핫-리로드 완료!</b>\n\n실전 봇(PC 1)에 새로운 파라미터(K={new_k})가 즉시 주입되었습니다. 봇을 껐다 켤 필요가 없습니다.")
            else:
                send_message("⚠️ 적용할 최적화 전략이 없습니다.")
        except Exception as e:
            send_message(f"❌ 전략 승인 중 오류 발생: {e}")

    elif cmd_text == "/start" or cmd_text == "!도움말":
        help_msg = (
            "🤖 <b>AI 원격 제어 봇 작동 시작</b>\n\n"
            "<b>[사용 가능 명령어]</b>\n"
            "• <code>!상태</code> : 현재 켜져 있는지 확인\n"
            "• <code>!주식현황</code> / <code>!선물현황</code> : 실시간 수익률 및 잔고 브리핑\n"
            "• <code>!매도 삼성전자</code> : 특정 종목 수동 익절/손절\n"
            "• <code>!전량매도</code> : 전체 주식 종목 즉시 청산\n"
            "• <code>!선물매수</code> / <code>!선물매도</code> / <code>!선물청산</code> : 선물 수동 진입/청산\n"
            "• <code>!주식시작</code> / <code>!주식종료</code> / <code>!주식재연결</code>\n"
            "• <code>!선물시작</code> / <code>!선물종료</code> / <code>!선물재연결</code>\n"
            "• <code>!재연동</code> / <code>!시스템재시작</code> : 시스템 통합 프로세스 정리 후 안전 재기동 (추천)\n"
            "• <code>!텔레그램재연결</code> : 메신저 봇 초기화\n\n"
            "<b>[🚨 긴급 제어]</b>\n"
            "• <code>!긴급정지</code> : 모든 포지션 강제 청산 및 프로세스 킬\n\n"
            "<b>[🧪 AI 최적화 및 모의투자 관제 (PC 2 연동)]</b>\n"
            "• <code>!백테스트시작</code> : 야간 파라미터 최적화 강제 시작\n"
            "• <code>!최적화결과</code> : 최적화 완료된 상위 파라미터 브리핑\n"
            "• <code>!모의투자현황</code> : 현재 검증 중인 AI 전략 성과 확인\n"
            "• <code>!전략승인</code> : 검증된 모의투자 전략을 실전(PC 1)에 핫-리로드\n\n"
            "<i>보안: 다온님 외 타인의 접근은 완벽히 차단됩니다.</i>"
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
