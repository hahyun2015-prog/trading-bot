"""
KRX 야간선물 실시간 수집기를 지정된 시간(기본 10분)만 돌려보고, 실제로 데이터가
들어왔는지 요약해서 result 파일에 남기는 일회성 테스트 러너.
Windows 작업 스케줄러에서 이 파일을 한 번 실행하도록 등록해서 쓴다.
"""
import os
import sys
import sqlite3
import subprocess
from datetime import datetime, timedelta

KIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.abspath(os.path.join(KIS_DIR, ".."))
FUTURES_DB_PATH = os.path.join(WORKSPACE_ROOT, "futures_data.db")
RESULT_PATH = os.path.join(KIS_DIR, "night_realtime_test_result.log")
PYTHON = sys.executable

DURATION_MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def count_rows_since(code, since_dt):
    if not os.path.exists(FUTURES_DB_PATH):
        return 0
    conn = sqlite3.connect(FUTURES_DB_PATH, timeout=30)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM futures_ohlcv WHERE code=? AND date >= ?",
        (code, since_dt.strftime("%Y%m%d%H%M%S"))
    )
    n = cur.fetchone()[0]
    conn.close()
    return n


def main():
    start_time = datetime.now()

    # 최근월물 코드 자동 조회
    sys.path.insert(0, KIS_DIR)
    from kis_index_future_code import find_kospi200_front_month
    code, name = find_kospi200_front_month(KIS_DIR)

    log_lines = [
        f"=== KIS 야간선물 실시간 테스트 시작 {start_time} ===",
        f"종목: {name} / 코드: {code}",
        f"실행 시간: {DURATION_MIN}분",
    ]

    proc = subprocess.Popen(
        [PYTHON, os.path.join(KIS_DIR, "kis_night_futures_collector.py"),
         "--mode", "realtime", "--code", code],
        cwd=KIS_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace"
    )

    try:
        stdout, _ = proc.communicate(timeout=DURATION_MIN * 60)
        log_lines.append("[수집기 프로세스가 시간 전에 자체 종료됨]")
        log_lines.append(stdout[-3000:] if stdout else "(출력 없음)")
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
        log_lines.append(f"[{DURATION_MIN}분 경과, 정상 종료 처리]")
        if stdout:
            log_lines.append(stdout[-3000:])

    n = count_rows_since(code, start_time)
    log_lines.append(f"--- 결과 ---")
    log_lines.append(f"테스트 시작 이후 DB에 새로 쌓인 5분봉 수: {n}건")
    if n > 0:
        log_lines.append("=> 실시간 야간선물 데이터가 실제로 수신됨!")
    else:
        log_lines.append("=> 이번 실행 동안 수신된 데이터 없음.")

    with open(RESULT_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n\n")

    print("\n".join(log_lines))


if __name__ == "__main__":
    main()
