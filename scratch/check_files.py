import os
import py_compile
import json
import traceback

def check_system():
    base_dir = r"c:\Antigravity\AI_T_Agent"
    exclude_dirs = { "venv32", ".git", ".claude", "__pycache__" }
    
    conflicted_files = []
    syntax_errors = []
    
    print("====== 1. 파일 이름 중복/충돌 검사 ======")
    for root, dirs, files in os.walk(base_dir):
        # 제외 폴더 필터링
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            path = os.path.join(root, file)
            # 충돌 파일 감지용 키워드
            if any(k in file for k in ["(1)", "(2)", "복사본", "conflicted"]):
                conflicted_files.append(path)
                
            # 파이썬 구문 에러 검사
            if file.endswith(".py"):
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError as e:
                    syntax_errors.append((path, str(e)))
                except Exception as e:
                    syntax_errors.append((path, f"기타 에러: {e}"))
                    
    if conflicted_files:
        print(f"[WARN] Conflict/Duplicate suspected files detected ({len(conflicted_files)}):")
        for f in conflicted_files:
            print(f"  - {f}")
    else:
        print("[OK] No conflict/duplicate files detected.")
        
    print("\n====== 2. Python Syntax Check ======")
    if syntax_errors:
        print(f"[WARN] Syntax errors detected in {len(syntax_errors)} files:")
        for path, err in syntax_errors:
            print(f"  - File: {path}\n    Error details:\n{err}\n")
    else:
        print("[OK] Python syntax check completed with no errors.")
        
    print("\n====== 3. JSON Configuration Check ======")
    config_paths = [
        os.path.join(base_dir, "config", "config.json"),
        os.path.join(base_dir, "config", "config_local.json")
    ]
    for c_path in config_paths:
        if os.path.exists(c_path):
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    json.load(f)
                print(f"[OK] {os.path.basename(c_path)}: JSON parsed successfully")
            except Exception as e:
                print(f"[FAIL] {os.path.basename(c_path)}: JSON parsing failed! Error: {e}")
        else:
            print(f"[INFO] {os.path.basename(c_path)} file does not exist.")

def print_python_processes():
    try:
        import subprocess
        cmd = ["powershell", "-Command", "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Select-Object CommandLine, ProcessId | Format-Table -Wrap"]
        out = subprocess.check_output(cmd, text=True, errors='ignore')
        print("\n====== 4. Running Python Processes ======")
        print(out)
    except Exception as e:
        print(f"Failed to check processes: {e}")

def check_db_activity():
    import sqlite3
    import os
    print("\n====== 5. Database Activity Scan ======")
    for db_name in ["unified_data.db", "futures_data.db"]:
        if not os.path.exists(db_name):
            print(f"[INFO] {db_name} does not exist.")
            continue
        try:
            conn = sqlite3.connect(db_name)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            print(f"\n[{db_name} Tables]: {tables}")
            
            # 각 테이블별 최근 데이터 1건 확인
            for t in tables:
                try:
                    # 날짜 혹은 timestamp/id 컬럼을 기준으로 정렬하여 가장 최근 기록 확인 시도
                    # 보통 date, timestamp, id, created_at 등의 컬럼이 존재함.
                    cursor.execute(f"PRAGMA table_info({t})")
                    cols = [c[1] for c in cursor.fetchall()]
                    sort_col = None
                    for candidate in ["timestamp", "date", "created_at", "id"]:
                        if candidate in cols:
                            sort_col = candidate
                            break
                    
                    query = f"SELECT * FROM {t}"
                    if sort_col:
                        query += f" ORDER BY {sort_col} DESC"
                    query += " LIMIT 1"
                    
                    cursor.execute(query)
                    row = cursor.fetchone()
                    print(f"  - Table '{t}' (Sort by {sort_col}): {row}")
                except Exception as ex:
                    print(f"  - Table '{t}' read failed: {ex}")
            conn.close()
        except Exception as e:
            print(f"  - {db_name} open/read failed: {e}")

if __name__ == "__main__":
    check_system()
    print_python_processes()
    check_db_activity()
