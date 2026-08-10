"""텔레그램 봇 토큰 재발급분을 config.json에 안전하게 반영한다.

배경 (2026-08-11):
    requests가 던지는 예외 메시지에는 요청 URL이 그대로 들어간다. 텔레그램 API는
    경로에 봇 토큰을 담으므로, 전송 실패를 print하면 토큰이 평문으로 로그에 남았다.
    실제로 로그 5개에 9,013건이 쌓여 있었다(봇ID 8710417841 = 현재 활성 봇).

    notifier.py는 커밋 4359f83에서 마스킹하도록 고쳤고 기존 로그도 정리했지만,
    **이미 노출된 토큰 값 자체는 재발급해야 무력화된다.**

재발급 절차 (텔레그램 앱에서 직접):
    1. @BotFather 대화 열기
    2. /mybots  →  해당 봇 선택
    3. API Token  →  Revoke current token
    4. 새로 표시되는 토큰을 복사

    ※ Revoke하는 순간 기존 토큰은 즉시 무효가 된다. 이 스크립트로 반영하고
      프로세스를 재시작하기 전까지 알림이 나가지 않는다.

이 스크립트가 하는 일:
    1. 새 토큰을 화면에 표시하지 않고 입력받는다 (getpass)
    2. **먼저 텔레그램 getMe로 검증**하고, 봇ID가 기존과 같은지 확인한다
       (다른 봇의 토큰을 넣으면 allowed_chat_id와 어긋나 알림이 안 간다)
    3. 유효할 때만 백업을 뜬 뒤 config.json에 반영한다
    4. 원하면 테스트 메시지를 보낸다
    5. 재시작이 필요한 프로세스를 안내한다

주의:
    getpass는 실제 콘솔이 필요하다. PowerShell이나 cmd에서 직접 실행할 것.

실행:
    python update_telegram_token.py
"""
import io
import os
import re
import sys
import json
import shutil
import getpass
import subprocess
from datetime import datetime

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "config", "config.json")

# notifier를 임포트하는 모듈들 — 토큰은 임포트 시점에 한 번만 읽으므로 재시작이 필요하다
RESTART_HINTS = [
    ("ERA (주간선물 매매)", 'Start-ScheduledTask -TaskName "AMATS ERA Reconnect"'),
    ("KIS 야간수집기", 'Start-ScheduledTask -TaskName "AMATS KIS Night Collector Start"'),
]


def ask(prompt, valid):
    while True:
        v = input(prompt).strip().lower()
        if v in valid:
            return v
        print(f"  → {'/'.join(valid)} 중에서 고르세요.")


def bot_id(token):
    return token.split(":", 1)[0] if ":" in token else ""


def verify(token):
    """getMe로 토큰 유효성 확인. (성공여부, 설명, 봇정보) 반환."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    except Exception as e:
        return False, f"요청 실패: {type(e).__name__}: {e}", None
    try:
        j = r.json()
    except Exception:
        return False, f"HTTP {r.status_code} (비JSON 응답)", None
    if r.status_code == 200 and j.get("ok"):
        return True, "유효", j.get("result", {})
    return False, f"HTTP {r.status_code} {j.get('error_code','')} {j.get('description','')}".strip(), None


def main():
    if not os.path.exists(CONFIG):
        print(f"[중단] 설정 파일이 없습니다: {CONFIG}")
        return 1

    raw = io.open(CONFIG, encoding="utf-8").read()
    cfg = json.loads(raw)
    tg = cfg.get("telegram", {})
    old = str(tg.get("bot_token", ""))
    chat = tg.get("allowed_chat_id")

    print("=" * 66)
    print("텔레그램 봇 토큰 갱신")
    print("=" * 66)
    print(f"대상 파일 : {CONFIG}")
    print(f"현재 봇ID : {bot_id(old) or '(없음)'}")
    print(f"수신 채팅 : {chat}")
    print()
    print("먼저 텔레그램 @BotFather 에서 재발급하세요:")
    print("  /mybots → 봇 선택 → API Token → Revoke current token")
    print("  ※ Revoke 즉시 기존 토큰이 무효가 되어, 반영 전까지 알림이 끊깁니다.")
    print()
    if ask("재발급을 마쳤습니까? [y/n]: ", {"y", "n"}) == "n":
        print("먼저 재발급을 진행하신 뒤 다시 실행하세요.")
        return 0

    print()
    print("새 토큰을 붙여넣으세요 (화면에 표시되지 않습니다).")
    while True:
        token = getpass.getpass("  새 봇 토큰 : ").strip()
        if not token:
            print("  → 값이 비었습니다.\n")
            continue
        if not re.match(r"^\d{6,}:[A-Za-z0-9_\-]{20,}$", token):
            print("  ⚠ 형식이 봇 토큰 같지 않습니다 (숫자ID:영문숫자 형태).")
            if ask("     그래도 진행할까요? [y/n]: ", {"y", "n"}) == "n":
                print()
                continue
        break

    print(f"  입력 확인 : 봇ID {bot_id(token)}, 전체 {len(token)}자")
    if token == old:
        print("[중단] 기존 토큰과 같습니다 — 재발급이 되지 않았습니다.")
        return 1
    if old and bot_id(token) != bot_id(old):
        print(f"  ⚠ 봇ID가 다릅니다 (기존 {bot_id(old)} → 입력 {bot_id(token)}).")
        print(f"     다른 봇이면 allowed_chat_id({chat})로 메시지가 가지 않을 수 있습니다.")
        if ask("     그래도 진행할까요? [y/n]: ", {"y", "n"}) == "n":
            return 1

    print()
    print("텔레그램에 검증 요청 중...")
    ok, why, info = verify(token)
    print(f"  {'✅ 유효' if ok else '❌ 거부'} — {why}")
    if ok and info:
        print(f"  봇 이름 : @{info.get('username')} ({info.get('first_name')})")
    if not ok:
        print("\n[중단] 검증 실패로 파일을 수정하지 않았습니다.")
        return 1

    backup = f"{CONFIG}.bak_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(CONFIG, backup)
    print(f"\n백업 생성 : {os.path.basename(backup)}")

    # 해당 줄만 치환해 나머지 서식·주석 위치를 보존한다
    out, done = [], False
    for line in raw.split("\n"):
        m = re.match(r'^(\s*)"bot_token"\s*:\s*".*?"(\s*,?)\s*$', line)
        if m and not done:
            out.append(f'{m.group(1)}"bot_token": "{token}"{m.group(2)}')
            done = True
        else:
            out.append(line)
    if not done:
        print("[중단] bot_token 줄을 찾지 못했습니다. 백업은 그대로 둡니다.")
        return 1
    io.open(CONFIG, "w", encoding="utf-8").write("\n".join(out))

    check = json.loads(io.open(CONFIG, encoding="utf-8").read())
    if check.get("telegram", {}).get("bot_token") != token:
        print(f"[경고] 반영 확인 실패 — 백업으로 되돌리세요: {backup}")
        return 1
    if len(check) != len(cfg):
        print(f"[경고] 최상위 키 수가 달라졌습니다 ({len(cfg)} → {len(check)}).")
    print(f"반영 완료 : telegram.bot_token (봇ID {bot_id(token)})")

    print()
    if ask("테스트 메시지를 보낼까요? [y/n]: ", {"y", "n"}) == "y":
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat,
                      "text": "🔑 <b>[AMATS]</b> 텔레그램 봇 토큰이 갱신되었습니다.",
                      "parse_mode": "HTML"},
                timeout=10)
            print("  전송 성공 — 텔레그램을 확인하세요." if r.status_code == 200
                  else f"  전송 실패: HTTP {r.status_code} {r.text[:150]}")
        except Exception as e:
            print(f"  전송 실패: {type(e).__name__}: {e}")

    print()
    print("=" * 66)
    print("남은 작업 — 토큰은 프로세스 기동 시 한 번만 읽으므로 재시작이 필요합니다")
    print("=" * 66)
    for name, cmd in RESTART_HINTS:
        print(f"  {name}")
        print(f"    {cmd}")
    print()
    if ask("지금 재시작할까요? [y/n]: ", {"y", "n"}) == "y":
        for name, cmd in RESTART_HINTS:
            print(f"  {name} 재시작 요청...")
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=False)
        print("  요청 완료. 1~2분 뒤 로그를 확인하세요.")
    else:
        print("  재시작 전까지 기존 프로세스는 무효가 된 옛 토큰을 계속 사용합니다.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
