"""주 1회 키움 수동 로그인(버전처리) 리마인더 — 텔레그램 발송 전용 스크립트.

배경 (2026-08-03 장애):
    AUTO 자동로그인으로 무인 운영하면 키움 버전처리(파일 업데이트)를 받지 못한다.
    2026-07-29에 내려온 새 바이너리(khopenapi.ocx / opcommapi.dll / opcomms.dll)가
    5일간 적용되지 못한 채 쌓였고, 8/3 로그인마다 opversionup이 교체를 재시도하다
    실패해 모달 에러창을 띄웠다. 그 창이 OCX 메시지 처리를 막아 로그인은 되는데
    계좌조회 TR만 무응답이 되었고, 장 시작부터 오후까지 매매가 완전히 정지했다.
    (상세: 매매시스템_점검보고서_20260731.md)

    키움 로그인창 안내문도 "자동로그인 사용시 주기적(주당 1회이상)으로 수동로그인을
    통해 버전처리를 수행하시길 권장"이라고 명시하고 있다.

용도:
    Windows 작업 스케줄러에 주 1회 등록해 실행한다. 알림만 보내며 시스템은 건드리지 않는다.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MESSAGE = (
    "🗓️ <b>[주간 점검] 키움 수동 로그인 필요</b>\n\n"
    "자동로그인(AUTO) 상태로는 <b>버전처리(파일 업데이트)를 받지 못합니다.</b>\n"
    "밀린 업데이트가 쌓이면 로그인은 되는데 <b>계좌조회가 무응답</b>이 되어 "
    "매매가 통째로 멈춥니다. (2026-08-03 실제 발생, 하루 매매 정지)\n\n"
    "<b>진행 순서</b>\n"
    "1. ERA 종료 — 텔레그램 <code>!시스템종료</code>\n"
    "2. 키움 로그인창에서 <b>AUTO 체크 해제</b>\n"
    "3. ID/비밀번호로 <b>수동 로그인</b> → 버전처리 완료까지 대기\n"
    "4. 계좌비밀번호 입력 → <b>전체계좌에 등록</b>\n"
    "5. <b>AUTO 다시 체크</b>\n"
    "6. ERA 재시작 — 텔레그램 <code>!시스템재시작</code>\n\n"
    "⚠️ 버전처리가 <code>파일 삭제 실패 [183]</code>로 실패하면, "
    "<code>C:\\OpenAPI</code>에 <code>_</code>로 시작하는 파일이 남아 있는지 확인하세요. "
    "그건 <b>적용 대기 중인 새 버전</b>이며, 키움 호스트를 모두 끈 상태에서 "
    "<code>_</code>를 떼고 기존 파일을 대체해야 합니다."
)


def main():
    try:
        from notifier import _send_message_sync
    except Exception as e:
        print(f"[리마인더] notifier 임포트 실패: {e}")
        return 1

    _send_message_sync(MESSAGE)
    print("[리마인더] 주간 수동 로그인 알림 발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
