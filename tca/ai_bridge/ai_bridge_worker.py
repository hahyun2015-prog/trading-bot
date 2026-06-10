import socket
# Force IPv4 globally to prevent IPv6 DNS resolution hangs and connection timeouts on Windows
orig_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo

import os
import sys
import json
import requests
import subprocess
import traceback
import re
from datetime import datetime

# 윈도우 CP949 콘솔 인코딩 에러 방지용
class SafeStreamWrapper:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        
    def write(self, data):
        if not data:
            return
        try:
            encoding = getattr(self.original_stream, 'encoding', 'cp949') or 'cp949'
            data.encode(encoding)
            self.original_stream.write(data)
        except UnicodeEncodeError:
            cleaned_data = "".join(c for c in data if ord(c) < 256)
            self.original_stream.write(cleaned_data)
            
    def flush(self):
        self.original_stream.flush()

sys.stdout = SafeStreamWrapper(sys.stdout)
sys.stderr = SafeStreamWrapper(sys.stderr)

current_dir = os.path.dirname(os.path.abspath(__file__))
tca_dir = os.path.dirname(current_dir)
workspace_root = os.path.dirname(tca_dir)

CONFIG_PATH = os.path.join(workspace_root, "config", "config.json")
CONFIG_LOCAL_PATH = os.path.join(workspace_root, "config", "config_local.json")
QUEUE_PATH = os.path.join(current_dir, "ai_task_queue.json")

ERA_LOG_PATH = os.path.join(workspace_root, "era", "era_order_manager.log")
TCA_LOG_PATH = os.path.join(workspace_root, "tca", "tca_controller.log")

def send_telegram_message(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[AI Worker] 텔레그램 메시지 전송 실패: {e}")

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if os.path.exists(CONFIG_LOCAL_PATH):
            with open(CONFIG_LOCAL_PATH, "r", encoding="utf-8") as f:
                local = json.load(f)
            for k, v in local.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        return cfg
    except Exception as e:
        print(f"[AI Worker] 설정 로드 에러: {e}")
        return {}

def read_last_lines(file_path, num_lines=50):
    if not os.path.exists(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return "".join(lines[-num_lines:])
    except Exception as e:
        return f"(로그 파일 읽기 실패: {e})"

def find_file_in_workspace(filename):
    # 워크스페이스 내에서 지정된 파일의 절대 경로를 재귀 탐색
    for root, dirs, files in os.walk(workspace_root):
        if ".git" in root or "venv32" in root or "__pycache__" in root:
            continue
        if filename in files:
            return os.path.join(root, filename)
    return None

def main():
    print(f"[AI Worker] AI 원격 디버그 세션 시작: {datetime.now()}")
    
    # 1. 큐 파일 로드
    if not os.path.exists(QUEUE_PATH):
        print("[AI Worker] 처리할 태스크 큐 파일이 없습니다.")
        return
        
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except Exception as e:
        print(f"[AI Worker] 태스크 큐 읽기 실패: {e}")
        return
 
    # 대기 중(PENDING)인 작업 검색
    active_task = None
    for task in queue.get("tasks", []):
        if task.get("status") == "PENDING":
            active_task = task
            break
            
    if not active_task:
        print("[AI Worker] 대기 중인 AI 점검 태스크가 없습니다.")
        return

    # 태스크 상태를 RUNNING으로 변경
    active_task["status"] = "RUNNING"
    active_task["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=4)

    # 설정 파일 로드
    cfg = load_config()
    bot_token = cfg.get("telegram", {}).get("bot_token", "")
    chat_id = cfg.get("telegram", {}).get("allowed_chat_id", 0)
    api_key = cfg.get("api_settings", {}).get("gemini_api_key", "")
    venv_path = cfg.get("paths", {}).get("venv32_path", "venv32")
    py_exec = os.path.join(workspace_root, venv_path, "Scripts", "python.exe")
    if not os.path.exists(py_exec):
        py_exec = "python"

    request_text = active_task.get("request", "")
    
    # 2. 콘텍스트(로그 및 코드) 수집
    era_log = read_last_lines(ERA_LOG_PATH, 50)
    tca_log = read_last_lines(TCA_LOG_PATH, 50)
    
    # 로그에서 트레이스백(에러) 발생 파일 자동 분석
    target_file_path = None
    target_filename = None
    
    # 2-A. 요청 텍스트에서 명시적 파일 이름 추출 (예: futures_order_manager.py)
    py_files = re.findall(r'[a-zA-Z0-9_]+\.py', request_text)
    if py_files:
        target_filename = py_files[0]
        target_file_path = find_file_in_workspace(target_filename)
        
    # 2-B. 요청에 파일이 없으면 로그의 트레이스백에서 에러가 발생한 파일 탐색
    if not target_file_path:
        log_files = re.findall(r'File "([^"]+\.py)", line \d+', era_log + tca_log)
        if log_files:
            # 가장 마지막 에러 발생 지점의 파일 추출
            path_candidate = log_files[-1]
            if os.path.isabs(path_candidate) and os.path.exists(path_candidate):
                target_file_path = path_candidate
                target_filename = os.path.basename(path_candidate)
            else:
                target_filename = os.path.basename(path_candidate)
                target_file_path = find_file_in_workspace(target_filename)

    # 폴백: 파일이 감지되지 않으면 주식/선물 매니저 핵심 파일 기본 설정
    if not target_file_path:
        target_filename = "era_order_manager.py"
        target_file_path = os.path.join(workspace_root, "era", "era_order_manager.py")

    print(f"[AI Worker] 에러 분석 대상 파일: {target_file_path}")

    # 대상 파일 코드 로드
    code_content = ""
    if os.path.exists(target_file_path):
        try:
            with open(target_file_path, "r", encoding="utf-8", errors="ignore") as f:
                code_content = f.read()
        except Exception as e:
            code_content = f"(코드 파일 로드 실패: {e})"

    # 3. Gemini API 프롬프트 구성
    prompt = f"""You are an expert quantitative trading system software engineer.
A crash or user repair request has occurred on the Windows PC running the AMATS trading bot.

User request: {request_text}

--- LATEST ERA ORDER ENGINE LOGS ---
{era_log}

--- LATEST TCA CONTROLLER LOGS ---
{tca_log}

--- TARGET CODE FILE: {target_filename} ---
{code_content}

--- INSTRUCTIONS ---
Analyze the logs and code to find the bug or implement the user's change request.
You MUST respond with a single valid JSON object only. Do NOT warp in markdown code blocks like ```json ... ```. Just return raw JSON.

JSON schema:
{{
    "analysis": "Short explanation in Korean of what the bug is and what you corrected",
    "target_code": "The EXACT lines of code in the target file that need to be replaced. Be extremely precise including spaces, indentation, and newlines. This must exist exactly in the file.",
    "replacement_code": "The corrected drop-in code snippet to replace 'target_code' with. Must have identical indentation and perfect syntax."
}}
"""

    # Gemini REST API 호출
    api_url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.0-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }]
    }

    ai_analysis = "분석 실패"
    success = False
    
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)
        res_json = response.json()
        
        # 텍스트 추출
        text_response = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # 마크다운 ```json 래핑 청소
        if text_response.startswith("```"):
            # 첫 번째 라인 제거
            lines = text_response.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            text_response = "\n".join(lines).strip()

        data = json.loads(text_response)
        
        ai_analysis = data.get("analysis", "원인 파악 불가")
        target_code = data.get("target_code", "")
        replacement_code = data.get("replacement_code", "")
        
        print(f"[AI Worker] AI 분석 결과: {ai_analysis}")
        
        if not target_code or not replacement_code:
            raise ValueError("API 응답에 target_code 또는 replacement_code가 누락되었습니다.")

        # 4. 가상 패치 및 무결성 검증 (파일 오버라이트는 하지 않고 구문 검사만 수행)
        if target_code not in code_content:
            raise ValueError("target_code가 원본 소스 코드에 정확하게 존재하지 않습니다.")

        patched_content = code_content.replace(target_code, replacement_code)
        
        # 임시 파일 문법 검증
        temp_file = target_file_path + ".tmp_ai"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(patched_content)
            
        # 컴파일 검사
        try:
            subprocess.check_output(f'"{py_exec}" -m py_compile "{temp_file}"', shell=True, stderr=subprocess.STDOUT)
            syntax_ok = True
        except subprocess.CalledProcessError as compile_err:
            syntax_ok = False
            compile_output = compile_err.output.decode('utf-8', errors='ignore')
            print(f"[AI Worker] 문법 검사 실패: {compile_output}")
            raise ValueError(f"AI가 제안한 패치에 문법 에러(SyntaxError)가 있습니다.\n{compile_output}")
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

        if syntax_ok:
            print(f"[AI Worker] 제안 패치 문법 검증 성공. (안전조치를 위해 파일 쓰기는 생략합니다)")
            success = True
            
    except Exception as err:
        error_msg = str(err)
        print(f"[AI Worker] 디버그 실패: {error_msg}")
        traceback.print_exc()
        send_telegram_message(
            bot_token, chat_id,
            f"❌ <b>[AI 원격 디버그 실패]</b>\n\n"
            f"👤 <b>요청:</b> {request_text}\n"
            f"📂 <b>대상:</b> {target_filename}\n"
            f"🚨 <b>오류:</b> <pre>{error_msg}</pre>\n"
            f"<i>안전장치에 의해 소스 코드는 변경되지 않았습니다.</i>"
        )
        
        active_task["status"] = "FAILED"
        active_task["error"] = error_msg
        active_task["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=4)
        return

    # 6. 최종 성공 처리 및 제안 리포트 발송
    if success:
        # 가상의 git diff 생성
        diff_text = f"- {target_code.strip()}\n+ {replacement_code.strip()}"
        if len(diff_text) > 1000:
            diff_text = diff_text[:1000] + "\n... (생략)"

        send_telegram_message(
            bot_token, chat_id,
            f"✅ <b>[AI 원격 분석 및 패치 제안 완료]</b>\n\n"
            f"👤 <b>요청:</b> <i>{request_text}</i>\n"
            f"📂 <b>대상 파일:</b> <code>{target_filename}</code>\n"
            f"🔬 <b>AI 분석:</b> {ai_analysis}\n\n"
            f"📝 <b>제안된 패치 내역 (문법 검증 완료):</b>\n"
            f"<pre>{diff_text}</pre>\n"
            f"⚠️ <b>주의:</b> 시스템 보안 안정장치에 의해 자동 소스 코드 패치 및 엔진 재시작 기능은 비활성화되었습니다. 위 변경 사항을 확인하시고 직접 소스 코드에 적용하시기 바랍니다."
        )

        active_task["status"] = "COMPLETED"
        active_task["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        active_task["analysis"] = ai_analysis
        with open(QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()
