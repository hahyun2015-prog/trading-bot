"""KIS AppKey/AppSecret 재발급분을 kis_devlp.yaml에 안전하게 반영한다.

배경 (2026-08-10):
    야간선물 수집기가 2026-08-08 11:16부터 죽어 있었다. 원인은 코드가 아니라
    자격증명으로, KIS가 모의·실전 양쪽 도메인에서 동일하게 거부했다.
        HTTP 403  EGW00103  유효하지 않은 AppKey입니다.
    kis_devlp.yaml은 2026-07-11 이후 수정된 적이 없으므로 서버측 무효화다.

    당시 실패가 로그에 남지 않았던 이유도 함께 확인됐다. kis_auth.py의 auth()/
    auth_ws()는 발급 실패 시 changeTREnv()를 부르지 않고 조기 반환하는데, 그러면
    getTREnv()가 빈 튜플을 돌려주고 호출부가 .my_url_ws에서 AttributeError로
    터진다. 서버가 준 EGW00103은 어디에도 남지 않는다.

이 스크립트가 하는 일:
    1. 새 AppKey/AppSecret을 화면에 찍지 않고 입력받는다 (getpass)
    2. **먼저 KIS에 검증 요청**을 보내 실제로 유효한지 확인한다
    3. 유효할 때만 백업을 뜬 뒤 yaml에 반영한다 (주석·순서 보존, 해당 줄만 치환)
    4. 0바이트로 남은 오늘자 토큰 파일을 지운다
    5. 수집기 기동 여부를 묻는다

주의:
    getpass는 실제 콘솔이 필요하다. PowerShell이나 cmd에서 직접 실행할 것.

실행:
    C:\\Antigravity\\AI_T_Agent\\venv32\\Scripts\\python.exe kis\\update_kis_keys.py
"""
import os
import sys
import json
import shutil
import getpass
import subprocess
from datetime import datetime

import yaml
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CONFIG = os.path.join(os.path.expanduser("~"), "KIS", "config", "kis_devlp.yaml")
TOKEN_DIR = os.path.dirname(CONFIG)

# (슬롯 이름, 앱키 필드, 시크릿 필드, 도메인 필드)
SLOTS = {
    "1": ("모의투자", "paper_app", "paper_sec", "vps"),
    "2": ("실전투자", "my_app", "my_sec", "prod"),
}


def ask(prompt, valid):
    while True:
        v = input(prompt).strip()
        if v in valid:
            return v
        print(f"  → {'/'.join(valid)} 중에서 선택하세요.")


def verify(base, app, sec):
    """발급 요청을 보내 키가 유효한지 확인. (성공여부, 설명) 반환."""
    try:
        r = requests.post(
            f"{base}/oauth2/tokenP",
            data=json.dumps({"grant_type": "client_credentials",
                             "appkey": app, "appsecret": sec}),
            headers={"content-type": "application/json"},
            timeout=15,
        )
    except Exception as e:
        return False, f"요청 실패: {type(e).__name__}: {e}"

    if r.status_code == 200 and "access_token" in r.text:
        return True, "토큰 발급 성공"
    try:
        j = r.json()
        msg = j.get("error_description") or j.get("msg1") or r.text[:200]
        code = j.get("error_code") or j.get("msg_cd") or ""
        return False, f"HTTP {r.status_code} {code} {msg}".strip()
    except Exception:
        return False, f"HTTP {r.status_code} {r.text[:200]}"


def replace_line(text, field, value):
    """'field: ...' 줄만 치환. 주석과 다른 줄은 건드리지 않는다."""
    out, done = [], False
    for line in text.split("\n"):
        if not done and line.lstrip().startswith(f"{field}:") and not line.lstrip().startswith("#"):
            esc = value.replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'{field}: "{esc}"')
            done = True
        else:
            out.append(line)
    if not done:
        raise KeyError(f"{field} 줄을 찾지 못했습니다")
    return "\n".join(out)


def main():
    if not os.path.exists(CONFIG):
        print(f"[중단] 설정 파일이 없습니다: {CONFIG}")
        return 1

    raw = open(CONFIG, encoding="utf-8").read()
    cfg = yaml.safe_load(raw)

    print("=" * 64)
    print("KIS AppKey / AppSecret 갱신")
    print("=" * 64)
    print(f"대상 파일 : {CONFIG}")
    print(f"최종 수정 : {datetime.fromtimestamp(os.path.getmtime(CONFIG)):%Y-%m-%d %H:%M}")
    print()
    print("현재 등록된 키:")
    same = cfg.get("my_app") == cfg.get("paper_app")
    for key, (label, af, sf, dom) in SLOTS.items():
        a = str(cfg.get(af, ""))
        shown = f"{a[:6]}…{a[-4:]}" if len(a) > 10 else "(비어있음)"
        print(f"  [{key}] {label:6s} {af:10s} = {shown} ({len(a)}자)")
    if same:
        print("  ※ 실전과 모의에 같은 키가 들어 있습니다.")
    print()

    print("어느 쪽을 갱신합니까?")
    print("  1) 모의투자만   2) 실전투자만   3) 둘 다 같은 키로")
    choice = ask("선택 [1/2/3]: ", {"1", "2", "3"})
    targets = [SLOTS["1"]] if choice == "1" else [SLOTS["2"]] if choice == "2" else [SLOTS["1"], SLOTS["2"]]

    print()
    print("KIS 개발자센터에서 재발급받은 값을 붙여넣으세요 (화면에 표시되지 않습니다).")
    print("  ※ AppKey가 짧고(36자 내외) AppSecret이 깁니다(180자 내외). 순서 주의.")
    while True:
        app = getpass.getpass("  AppKey    : ").strip()
        sec = getpass.getpass("  AppSecret : ").strip()
        if not app or not sec:
            print("  → 값이 비었습니다. 다시 입력하세요.\n")
            continue
        print(f"  입력 확인 : AppKey {len(app)}자, AppSecret {len(sec)}자")
        # 입력이 가려져 있어 뒤바꿔 넣어도 눈으로 알 수 없다. KIS는 AppKey가
        # AppSecret보다 훨씬 짧으므로, 길이가 뒤집혔으면 십중팔구 순서를 바꾼 것이다.
        if len(app) > len(sec):
            print("  ⚠ AppKey가 AppSecret보다 깁니다 — 순서를 바꿔 넣으신 것 같습니다.")
            if ask("     그래도 이대로 진행할까요? [y/n]: ", {"y", "n"}) == "n":
                print()
                continue
        break
    print()

    # ── 반영 전에 검증한다 ───────────────────────────────────────────
    print("KIS 서버에 검증 요청 중...")
    ok_all = True
    for label, af, sf, dom in targets:
        ok, why = verify(cfg[dom], app, sec)
        print(f"  {label}: {'✅ 유효' if ok else '❌ 거부'} — {why}")
        ok_all = ok_all and ok
    print()

    if not ok_all:
        print("[중단] 검증에 실패해 파일을 수정하지 않았습니다.")
        print("       키를 다시 확인하시고, 모의투자는 신청 기간 만료 여부도 확인하세요.")
        return 1

    # ── 백업 후 반영 ────────────────────────────────────────────────
    backup = f"{CONFIG}.bak_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(CONFIG, backup)
    print(f"백업 생성 : {backup}")

    new = raw
    for label, af, sf, dom in targets:
        new = replace_line(new, af, app)
        new = replace_line(new, sf, sec)
    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write(new)

    check = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    for label, af, sf, dom in targets:
        if check.get(af) != app or check.get(sf) != sec:
            print(f"[경고] {label} 반영 확인 실패 — 백업으로 되돌리세요: {backup}")
            return 1
    print("반영 완료 : " + ", ".join(t[0] for t in targets))

    # ── 0바이트로 남은 오늘자 토큰 파일 정리 ────────────────────────
    today = os.path.join(TOKEN_DIR, f"KIS{datetime.today():%Y%m%d}")
    if os.path.exists(today) and os.path.getsize(today) == 0:
        os.remove(today)
        print(f"정리      : 0바이트 토큰 파일 삭제 ({os.path.basename(today)})")

    # ── 수집기 기동 ─────────────────────────────────────────────────
    print()
    if ask("야간선물 수집기를 지금 기동할까요? [y/n]: ", {"y", "n"}) == "y":
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             'Start-ScheduledTask -TaskName "AMATS KIS Night Collector Start"'],
            check=False)
        print("기동 요청을 보냈습니다. 1~2분 뒤 kis/kis_night_collector.log 를 확인하세요.")
        print("정상이면 'PINGPONG' 수신 로그가 보이고, futures_ohlcv에 A05608 봉이 쌓입니다.")
    else:
        print("기동하지 않았습니다. 매일 17:55 자동 기동 트리거가 걸려 있습니다.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
