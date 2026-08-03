"""08:45 안전청산 실패 알림 로직 검증 (2026-08-04 추가분).

이 알림은 "청산이 실패했고 다른 안전장치가 전부 놓쳤을 때" 울리는 최후 수단이다.
정작 이게 조용히 안 울리면 2026-08-03 사고(포지션 오버나잇 방치, 사용자가 밤에 직접 발견)가
그대로 반복된다. 그래서 실제 _do_daily_reset()을 호출해 다음을 확인한다.

  1) 08:40 리셋 시 08:57 감시가 예약되는가
  2) 예약된 콜백이 '포지션 잔존' 시 실제로 알림을 내보내는가
  3) 포지션이 없으면 조용한가 (거짓 경보 없음)
  4) 늦게(장중) 기동한 경우엔 예약 자체를 안 하는가 (정상 보유를 실패로 오인 방지)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import datetime as _dt
import era.era_order_manager as eom


class FakeTimer:
    scheduled = []

    @staticmethod
    def singleShot(ms, cb):
        FakeTimer.scheduled.append((ms, cb))


class FakeNotifier:
    sent = []

    @staticmethod
    def send_message(text):
        FakeNotifier.sent.append(text)


def make_fixed_dt(h, m):
    class FixedDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 4, h, m, 0)
    return FixedDateTime


def build(with_position=True):
    o = object.__new__(eom.ERAOrderManager)
    o._daily_reset_done_date = "2026-08-03"
    o._night_reset_done_date = "2026-08-03_0500"
    o._night_start_done_date = "2026-08-03_1800"
    o.system_halted = False
    o.stock_daily_loss = 0
    o.stock_daily_halted = False
    o.stock_total_balance = 0
    o.stock_monthly_loss = 0
    o.stock_monthly_initial = 0
    o.trading_mode = 'futures'
    o.futures_day_open = 0.0
    o._day_strategy_activated_at = None
    o.futures_order_locked = False
    o.futures_day_entry_price = 0.0
    o.futures_day_peak = 0.0
    o.futures_last_long_exit_price = 0.0
    o.futures_last_long_exit_time = 0.0
    o.futures_last_short_exit_price = 0.0
    o.futures_last_short_exit_time = 0.0
    o.futures_day_consecutive_losses = 0
    o.futures_day_trade_count = 0
    o.ohlcv_buffer = {}
    o._tick_reject = {'A0568000': [1.0, 2.0]}       # 어제 잔재 — 초기화되는지 확인용
    o._tick_feed_alerted = {'A0568000': True}
    o.futures_db_path = 'futures_data.db'
    o.real_day_code = 'A0568000'
    o.futures_prefix = '105'   # 실전과 동일: 미니선물(승수 50,000)
    o.futures_positions = (
        {"KOSPI200": {'type': 'LONG', 'qty': 15, 'price': 990.06, 'code': 'A0568000'}}
        if with_position else {}
    )
    # 무거운 부수효과는 스텁 처리 (이 테스트의 관심사가 아님)
    o.save_futures_exit_state = lambda: None
    o._load_prev_range = lambda: None
    o.update_futures_dynamic_sl_tp = lambda: None
    return o


def run(label, hour, minute, with_position):
    FakeTimer.scheduled = []
    FakeNotifier.sent = []
    m = build(with_position)

    o_dt, o_timer, o_notif = eom.datetime, eom.QTimer, eom.notifier
    eom.datetime = make_fixed_dt(hour, minute)
    eom.QTimer = FakeTimer
    eom.notifier = FakeNotifier
    try:
        m._do_daily_reset()
        # 08:57 감시 예약 찾기 (지연시간이 분 단위인 것)
        sched = [(ms, cb) for ms, cb in FakeTimer.scheduled if ms > 60_000]
        fired = None
        if sched:
            # ★ 반드시 notifier 패치가 살아있는 동안 실행해야 한다. 패치를 되돌린 뒤
            #   호출하면 콜백이 전역 notifier를 실제 모듈로 조회해(=진짜 텔레그램 발송)
            #   검증도 안 되고 실계정에 메시지가 나간다.
            sched[0][1]()
            fired = len(FakeNotifier.sent) > 0
    finally:
        eom.datetime, eom.QTimer, eom.notifier = o_dt, o_timer, o_notif

    print(f"\n=== {label} ===")
    print(f"  리셋 실행 시각      : {hour:02d}:{minute:02d}")
    print(f"  포지션 보유         : {'예' if with_position else '아니오'}")
    print(f"  08:57 감시 예약     : {'예 (' + str(round(sched[0][0]/60000, 1)) + '분 후)' if sched else '아니오'}")
    print(f"  틱 스트릭 초기화    : {'예' if not m._tick_reject else '아니오(미정리)'}")
    if sched:
        print(f"  콜백 실행 시 알림   : {'발송됨' if fired else '조용함'}")
        if fired:
            print("  --- 알림 전문 ---")
            for _ln in FakeNotifier.sent[0].splitlines():
                print(f"    {_ln}")
            has_gap = "야간 갭" in FakeNotifier.sent[0] and "평가손익" in FakeNotifier.sent[0]
            print(f"  갭/평가손익 포함    : {'예' if has_gap else '아니오 (누락!)'}")
    return bool(sched), fired


print("08:45 안전청산 실패 알림 — 로직 검증")
print("=" * 70)
s1, f1 = run("① 08:40 리셋 + 포지션 잔존 (사고 재현 조건)", 8, 40, True)
s2, f2 = run("② 08:40 리셋 + 포지션 없음 (정상)", 8, 40, False)
s3, f3 = run("③ 14:00 늦은 기동 + 포지션 보유 (장중 정상)", 14, 0, True)

print("\n" + "=" * 70)
print("판정")
print("=" * 70)
ok = []
ok.append(("① 포지션 잔존 시 경보", s1 and f1 is True))
ok.append(("② 포지션 없으면 무음", s2 and f2 is False))
ok.append(("③ 장중 기동 시 예약 안함(거짓경보 방지)", not s3))
for name, passed in ok:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
print()
print("  결론:", "전 항목 통과 — 알림 로직 정상" if all(p for _, p in ok) else "실패 항목 있음 — 수정 필요")
