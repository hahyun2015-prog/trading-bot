import io
import sys

def main():
    file_path = r'c:\Antigravity\AI_T_Agent\telegram_controller\telegram_controller.py'
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    modified = False
    new_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. Insert manual futures trade execution command handler before 'elif cmd_text.startswith("!매도"):'
        if 'elif cmd_text.startswith("!매도"):' in line:
            if '!선물매수' not in content_before_check(lines, i):
                indent = len(line) - len(line.lstrip())
                new_lines.append(' ' * indent + 'elif cmd_text in ["!선물매수", "!선물매도", "!선물청산"]:\n')
                new_lines.append(' ' * (indent + 4) + 'try:\n')
                new_lines.append(' ' * (indent + 8) + 'import sqlite3\n')
                new_lines.append(' ' * (indent + 8) + 'from datetime import datetime\n')
                new_lines.append(' ' * (indent + 8) + 'db_path = os.path.join(BASE_DIR, "futures_data.db")\n')
                new_lines.append(' ' * (indent + 8) + 'conn = sqlite3.connect(db_path)\n')
                new_lines.append(' ' * (indent + 8) + 'cursor = conn.cursor()\n')
                new_lines.append('\n')
                new_lines.append(' ' * (indent + 8) + '# 1. 현재 시간에 따라 주간(10100000) / 야간(10500000) 코드 결정\n')
                new_lines.append(' ' * (indent + 8) + 'now_hour = datetime.now().hour\n')
                new_lines.append(' ' * (indent + 8) + 'active_code = "10500000" if (now_hour >= 17 or now_hour < 6) else "10100000"\n')
                new_lines.append(' ' * (indent + 8) + 'session_label = "야간" if active_code == "10500000" else "주간"\n')
                new_lines.append('\n')
                new_lines.append(' ' * (indent + 8) + '# 2. DB에서 가장 최근 수신된 종목 가격 조회\n')
                new_lines.append(' ' * (indent + 8) + 'cursor.execute("""\n')
                new_lines.append(' ' * (indent + 12) + 'CREATE TABLE IF NOT EXISTS futures_ohlcv (\n')
                new_lines.append(' ' * (indent + 16) + 'code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER,\n')
                new_lines.append(' ' * (indent + 16) + 'UNIQUE(code, date)\n')
                new_lines.append(' ' * (indent + 12) + ')\n')
                new_lines.append(' ' * (indent + 8) + '""")\n')
                new_lines.append(' ' * (indent + 8) + 'cursor.execute("SELECT close FROM futures_ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1", (active_code,))\n')
                new_lines.append(' ' * (indent + 8) + 'row = cursor.fetchone()\n')
                new_lines.append(' ' * (indent + 8) + 'current_price = row[0] if row else 400.0\n')
                new_lines.append('\n')
                new_lines.append(' ' * (indent + 8) + 'if cmd_text == "!선물매수":\n')
                new_lines.append(' ' * (indent + 12) + 'cursor.execute("""\n')
                new_lines.append(' ' * (indent + 16) + 'INSERT INTO signals (code, signal_type, price, status)\n')
                new_lines.append(' ' * (indent + 16) + 'VALUES (?, \'LONG_ENTER\', ?, \'PENDING\')\n')
                new_lines.append(' ' * (indent + 12) + '""", (active_code, current_price))\n')
                new_lines.append(' ' * (indent + 12) + 'conn.commit()\n')
                new_lines.append(' ' * (indent + 12) + 'send_message(f"✅ <b>[{session_label}선물]</b> 수동 매수(LONG) 진입 명령이 전달되었습니다. (기준가: {current_price:.2f}pt)")\n')
                new_lines.append('\n')
                new_lines.append(' ' * (indent + 8) + 'elif cmd_text == "!선물매도":\n')
                new_lines.append(' ' * (indent + 12) + 'cursor.execute("""\n')
                new_lines.append(' ' * (indent + 16) + 'INSERT INTO signals (code, signal_type, price, status)\n')
                new_lines.append(' ' * (indent + 16) + 'VALUES (?, \'SHORT_ENTER\', ?, \'PENDING\')\n')
                new_lines.append(' ' * (indent + 12) + '""", (active_code, current_price))\n')
                new_lines.append(' ' * (indent + 12) + 'conn.commit()\n')
                new_lines.append(' ' * (indent + 12) + 'send_message(f"✅ <b>[{session_label}선물]</b> 수동 매도(SHORT) 진입 명령이 전달되었습니다. (기준가: {current_price:.2f}pt)")\n')
                new_lines.append('\n')
                new_lines.append(' ' * (indent + 8) + 'elif cmd_text == "!선물청산":\n')
                new_lines.append(' ' * (indent + 12) + 'status_path = os.path.join(CONTROLLER_DIR, "futures_status.json")\n')
                new_lines.append(' ' * (indent + 12) + 'pos_found = False\n')
                new_lines.append(' ' * (indent + 12) + 'if os.path.exists(status_path):\n')
                new_lines.append(' ' * (indent + 16) + 'with open(status_path, "r", encoding="utf-8") as sf:\n')
                new_lines.append(' ' * (indent + 20) + 'status_data = json.load(sf)\n')
                new_lines.append(' ' * (indent + 16) + 'positions = status_data.get("positions", {})\n')
                new_lines.append(' ' * (indent + 16) + 'for pos_key, pos_info in positions.items():\n')
                new_lines.append(' ' * (indent + 20) + 'p_type = pos_info.get("type")\n')
                new_lines.append(' ' * (indent + 20) + 'qty = pos_info.get("qty", 0)\n')
                new_lines.append(' ' * (indent + 20) + 'if qty > 0 and p_type in ["LONG", "SHORT"]:\n')
                new_lines.append(' ' * (indent + 24) + 'exit_signal = "LONG_EXIT" if p_type == "LONG" else "SHORT_EXIT"\n')
                new_lines.append(' ' * (indent + 24) + 'cursor.execute("""\n')
                new_lines.append(' ' * (indent + 28) + 'INSERT INTO signals (code, signal_type, price, status)\n')
                new_lines.append(' ' * (indent + 28) + 'VALUES (?, ?, ?, \'PENDING\')\n')
                new_lines.append(' ' * (indent + 24) + '""", (active_code, exit_signal, current_price))\n')
                new_lines.append(' ' * (indent + 24) + 'conn.commit()\n')
                new_lines.append(' ' * (indent + 24) + 'pos_found = True\n')
                new_lines.append(' ' * (indent + 24) + 'send_message(f"✅ <b>[{session_label}선물]</b> 수동 청산({exit_signal}) 명령이 전달되었습니다. (보유: {p_type} {qty}계약, 기준가: {current_price:.2f}pt)")\n')
                new_lines.append(' ' * (indent + 24) + 'break\n')
                new_lines.append(' ' * (indent + 12) + 'if not pos_found:\n')
                new_lines.append(' ' * (indent + 16) + 'send_message("⚠️ 현재 보유 중인 선물 포지션이 없습니다. (상태 파일에 포지션 없음)")\n')
                new_lines.append('\n')
                new_lines.append(' ' * (indent + 8) + 'conn.close()\n')
                new_lines.append(' ' * (indent + 4) + 'except Exception as e:\n')
                new_lines.append(' ' * (indent + 8) + 'send_message(f"❌ 선물 수동 명령 처리 중 오류: {e}")\n')
                new_lines.append('\n')
                print("Inserted manual futures trade command handler.")
                modified = True
            new_lines.append(line)
            i += 1
            continue

        # 2. Add manual futures trade description into the help message
        elif '• <code>!전량매도</code> : 전체 주식 종목 즉시 청산' in line:
            new_lines.append(line)
            if '!선물매수' not in content_before_check(lines, i+20):
                indent = len(line) - len(line.lstrip())
                new_lines.append(' ' * indent + '"• <code>!선물매수</code> / <code>!선물매도</code> / <code>!선물청산</code> : 선물 수동 진입/청산\\n"\n')
                print("Inserted manual futures trade commands into help message.")
                modified = True
            i += 1
            continue

        new_lines.append(line)
        i += 1

    if modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print("telegram_controller.py successfully modified and saved.")
    else:
        print("No modifications needed or target lines not found.")

def content_before_check(lines, index):
    # Helper to check if string already exists nearby to avoid double injection
    context = ""
    start = max(0, index - 20)
    end = min(len(lines), index + 20)
    for j in range(start, end):
        context += lines[j]
    return context

if __name__ == '__main__':
    main()
