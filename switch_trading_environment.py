# -*- coding: utf-8 -*-
"""모의투자 ↔ 실전매매 전환 도구 (2026-08-12).

실제 자금이 걸리는 전환이므로, 값을 화면에 찍지 않고 되돌릴 수 있게 만든다.

  python switch_trading_environment.py            현재 상태 보기 (값은 마스킹)
  python switch_trading_environment.py --live     실전매매로 전환
  python switch_trading_environment.py --revert   모의투자로 되돌리기

왜 config_local.json에 쓰는가:
    era_order_manager.load_config()는 config.json을 읽은 뒤
        for key, val in local_overrides.items():
            if isinstance(val, dict) and isinstance(config.get(key), dict):
                config[key].update(val)
            else:
                config[key] = val
    로 config_local.json을 덮어쓴다. 최상위 키 안에서 키 단위로 병합되므로,
    config_local.json에 있는 값이 항상 이긴다.

    실제로 config_local.json의 accounts.futures_account가 빈 문자열이어서,
    config.json에만 계좌번호를 넣으면 빈 값으로 덮여 동작하지 않는다.

    두 파일 모두 .gitignore 대상이라 커밋되지 않는다(확인함).
"""
import argparse
import getpass
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(ROOT, "config", "config.json")
LOCAL = os.path.join(ROOT, "config", "config_local.json")


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_atomic(path, data):
    """tmp 작성 후 os.replace — 다른 프로세스(TCA 등)가 같은 파일을 읽는 중일 수 있다."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


def backup(path):
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = f"{path}.bak_{stamp}"
    shutil.copy2(path, dst)
    return dst


def mask(v):
    """계좌번호를 화면에 그대로 찍지 않는다."""
    s = str(v or "")
    if not s:
        return "(미설정)"
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def effective():
    """ERA가 실제로 보게 될 병합 결과를 같은 규칙으로 재현한다."""
    cfg = load(MAIN)
    for key, val in load(LOCAL).items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    return cfg


def show_status():
    cfg = effective()
    env = cfg.get("environment", "mock")
    fs = cfg.get("futures_settings", {})
    acc = cfg.get("accounts", {})
    pct = fs.get("daily_loss_limit_pct", 0.0)

    print("=" * 66)
    print("  현재 적용 상태 (config.json + config_local.json 병합 결과)")
    print("=" * 66)
    print(f"  환경              {'🔴 실전매매 (live)' if env == 'live' else '🟢 모의투자 (mock)'}")
    print(f"  선물 계좌         {mask(acc.get('futures_account'))}")
    print(f"  trading_mode      {cfg.get('trading_mode', '(없음)')}")
    print(f"  전략              {fs.get('strategy_type', '(active_strategy.json 참조)')}")
    print(f"  최대 계약수       {fs.get('max_contracts', '(기본 15)')}")
    print(f"  마진 요율         {fs.get('margin_rate', '(기본 0.10)')}")
    print(f"  연속손절 한도     {fs.get('consecutive_loss_limit', '(기본 5)')}회")
    print(f"  일일손실 한도     {pct if pct else '0 (비활성)'}"
          + (f"  ({pct * 100:.1f}%)" if pct else ""))
    print("=" * 66)
    return cfg


def to_live():
    cfg = show_status()
    if cfg.get("environment") == "live":
        print("\n이미 실전매매 상태입니다. 계좌번호만 다시 넣으려면 --revert 후 재실행하세요.")
        return 1

    print("""
실전매매로 전환하면 다음이 함께 바뀝니다.

  · 키움 접속 서버      모의투자서버(2) → 실서버(1)
  · 선물 종목코드       A05…/A01… → 105…/101…
  · 최소 1계약 강제     모의 전용 우회 해제
                        (단, 주 주문경로는 max(1,…)이 있어 1계약 보장 유지)
  · RSA 자동통과        해제 (주식 전용 — 현재 trading_mode=futures라 무관)

이 스크립트가 쓰는 값은 셋입니다.

  environment                          → "live"
  accounts.futures_account             → 입력하신 계좌번호
  futures_settings.daily_loss_limit_pct → 0 (비활성)

되돌리려면  python switch_trading_environment.py --revert
""")

    print("""  계좌번호는 비워 두는 편을 권합니다.

    비워 두면 ERA가 키움 로그인 계좌 목록에서 자동으로 고릅니다(2396~2414행).
    지금 모의가 정상 동작하는 것도 이 경로입니다. 직접 넣은 값은 검증 없이
    그대로 쓰이므로, 자릿수나 상품코드가 틀리면 조회부터 실패합니다.

    다만 이 계정에는 계좌가 2개 있고, 실거래 자동탐지는 "'11'로 끝나지 않는
    첫 계좌"를 고르는 휴리스틱이라 주식 계좌를 집을 수 있습니다. 그래서
    비워 두고 전환한 뒤, 기동 로그의 "선물 계좌:" 줄과 예수금을 확인하고,
    틀렸을 때만 이 스크립트를 다시 돌려 명시하는 순서가 안전합니다.
""")
    raw = getpass.getpass("  실계좌번호 (그냥 Enter = 자동탐지 사용): ").strip()
    acct = re.sub(r"[^0-9]", "", raw)
    if acct:
        if not (8 <= len(acct) <= 12):
            print(f"\n  ✗ 숫자 {len(acct)}자리로 읽혔습니다. 8~12자리여야 합니다. 중단합니다.")
            return 1
        again = getpass.getpass("  확인을 위해 한 번 더 입력하세요: ").strip()
        if re.sub(r"[^0-9]", "", again) != acct:
            print("\n  ✗ 두 입력이 다릅니다. 중단합니다.")
            return 1
        print(f"\n  입력된 계좌: {mask(acct)}  ({len(acct)}자리)")
    else:
        print("\n  계좌번호: 자동탐지 사용 (config를 비워 둡니다)")

    print("\n  이 설정으로 실제 자금이 주문에 사용됩니다.")
    if input('  진행하려면 LIVE 를 입력하세요: ').strip().upper() != "LIVE":
        print("\n  취소했습니다. 아무것도 바꾸지 않았습니다.")
        return 1

    b1, b2 = backup(MAIN), backup(LOCAL)
    local = load(LOCAL)
    local["environment"] = "live"
    # 빈 문자열이면 ERA가 자동탐지 경로로 간다(2397행의 `if not self.futures_account`).
    local.setdefault("accounts", {})["futures_account"] = acct
    local.setdefault("futures_settings", {})["daily_loss_limit_pct"] = 0
    save_atomic(LOCAL, local)

    print("\n  ✓ config_local.json 갱신 완료")
    for b in (b1, b2):
        if b:
            print(f"    백업: {os.path.basename(b)}")
    print()
    show_status()
    print("""
다음 단계

  1. 키움 실계좌에 예수금을 입금하고, 선물옵션 거래 등록·계좌비밀번호 설정을 마칩니다.
  2. ERA 재기동:  schtasks /Run /TN "AMATS ERA Reconnect"
  3. 기동 로그에서 아래 두 줄을 반드시 확인합니다.

       [ERA] 환경: 실전매매 (environment=live)
       [ERA] 선물 계좌 자금  — 예수금이 실제 입금액과 일치하는지

  4. 첫 주문이 나가기 전에 종목코드가 105… 체계로 잡혔는지 확인합니다.
     모의에서 쓰던 A05… 코드가 그대로면 주문이 거부됩니다.

되돌리기:  python switch_trading_environment.py --revert
""")
    return 0


def to_mock():
    show_status()
    print("\n  모의투자로 되돌립니다. 계좌번호 설정은 남겨 둡니다(실서버 접속만 끊김).")
    if input('  진행하려면 MOCK 를 정확히 입력하세요: ').strip() != "MOCK":
        print("\n  취소했습니다.")
        return 1
    b = backup(LOCAL)
    local = load(LOCAL)
    local["environment"] = "mock"
    local.setdefault("futures_settings", {})["daily_loss_limit_pct"] = 0.03
    save_atomic(LOCAL, local)
    print(f"\n  ✓ 모의투자로 복귀 (일일손실 한도 3% 복원)")
    if b:
        print(f"    백업: {os.path.basename(b)}")
    print('\n  ERA 재기동 필요:  schtasks /Run /TN "AMATS ERA Reconnect"\n')
    show_status()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="모의투자 ↔ 실전매매 전환")
    ap.add_argument("--live", action="store_true", help="실전매매로 전환")
    ap.add_argument("--revert", action="store_true", help="모의투자로 되돌리기")
    a = ap.parse_args()
    if a.live and a.revert:
        print("--live 와 --revert 는 함께 쓸 수 없습니다.")
        sys.exit(1)
    if a.live:
        sys.exit(to_live())
    if a.revert:
        sys.exit(to_mock())
    show_status()
    print("\n  전환하려면:  python switch_trading_environment.py --live\n")
