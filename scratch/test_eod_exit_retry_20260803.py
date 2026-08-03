"""2026-08-03 장마감 청산 방치 사고 재현 + 수정 검증.

사고: 15:42 LONG 15계약 청산 주문 → 체결 미확인 → 유예 종료 후 is_exiting만 해제.
      그런데 실제 재주문은 _process_day_tick 재호출에 의존했고, 종가 단일가 구간이라
      틱이 들어오지 않아 재시도가 한 번도 발동하지 못함 → 15:45 창 초과 → 오버나잇 방치.

이 스크립트는 실제 _execute_futures_direct()를 호출하되
  - QTimer를 가짜로 바꿔 타이머 콜백을 수동으로 실행하고
  - 키움 dynamicCall을 가로채 실제 전송된 주문을 기록한다.
틱은 단 한 번도 주지 않는다. 그 상태에서 재청산 주문이 나가는지가 판정 기준이다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import datetime as _dt
import era.era_order_manager as eom


class FakeTimer:
    """QTimer.singleShot 대체 — 콜백을 큐에 쌓아두고 수동으로 실행한다."""
    queue = []

    @staticmethod
    def singleShot(ms, cb):
        FakeTimer.queue.append((ms, cb))

    @staticmethod
    def run_all(max_rounds=10):
        """큐에 쌓인 콜백을 지연시간 순서대로 실행(재귀적으로 쌓이는 것도 처리)."""
        rounds = 0
        while FakeTimer.queue and rounds < max_rounds:
            rounds += 1
            batch = sorted(FakeTimer.queue, key=lambda x: x[0])
            FakeTimer.queue = []
            for _ms, cb in batch:
                cb()


class FakeKiwoom:
    def __init__(self):
        self.orders = []

    def dynamicCall(self, sig, args=None):
        if "SendOrderFO" in sig:
            # [화면, 화면번호, 계좌, 코드, ord_kind, slby_tp, 호가구분, 수량, 가격, 원주문번호]
            self.orders.append({
                'ord_kind': args[4], 'slby_tp': args[5], 'qty': args[7], 'org': args[9],
            })
            return 0
        return 0


class FixedDateTime(_dt.datetime):
    """datetime.now()를 마감 창(15:42) 안으로 고정."""
    _now = _dt.datetime(2026, 8, 3, 15, 42, 30)

    @classmethod
    def now(cls, tz=None):
        return cls._now


def build(eod_window=True):
    m = object.__new__(eom.ERAOrderManager)
    m.kiwoom = FakeKiwoom()
    m.futures_account = "7036380531"
    m.futures_order_locked = False
    m.futures_night_order_locked = False
    m.futures_best_k = 0.2
    m.futures_prefix = '105'
    m.futures_available_balance = 900_000_000
    m.futures_margin_cap_ratio = 0.30
    m.futures_day_entry_price = 989.0
    m.futures_day_peak = 991.32
    m.futures_night_entry_price = 0.0
    m._futures_last_order_no = {}          # 원주문번호 미확보 = 2026-08-03 실제 상황
    m.futures_positions = {
        "KOSPI200": {'type': 'LONG', 'qty': 15, 'price': 989.28, 'code': 'A0568000'}
    }
    return m


def run(label, patch_retry):
    FakeTimer.queue = []
    m = build()

    orig_timer, orig_dt = eom.QTimer, eom.datetime
    eom.QTimer = FakeTimer
    eom.datetime = FixedDateTime
    try:
        m._execute_futures_direct("LONG_EXIT", 990.06, "A0568000", "KOSPI200")
        first_orders = len(m.kiwoom.orders)
        # 틱은 절대 주지 않는다 — 타이머만 흘려보낸다
        FakeTimer.run_all()
    finally:
        eom.QTimer, eom.datetime = orig_timer, orig_dt

    news = [o for o in m.kiwoom.orders if o['ord_kind'] == 1]
    cancels = [o for o in m.kiwoom.orders if o['ord_kind'] == 3]
    print(f"\n=== {label} ===")
    print(f"  최초 청산 주문      : {first_orders}건")
    print(f"  전체 신규주문(ord=1): {len(news)}건")
    print(f"  취소주문(ord=3)     : {len(cancels)}건")
    print(f"  포지션 잔존         : {'예' if 'KOSPI200' in m.futures_positions else '아니오(정리됨)'}")
    retried = len(news) > 1
    print(f"  => 틱 없이 재청산 주문 발생: {'예 (정상)' if retried else '아니오 (사고 재현)'}")
    return retried, len(news)


print("2026-08-03 장마감 청산 방치 — 재현 및 수정 검증")
print("=" * 66)
ok, n = run("현재 코드 (2026-08-03 수정 적용)", True)

print("\n" + "=" * 66)
print("판정")
print("=" * 66)
if ok:
    print(f"  틱을 한 번도 주지 않았는데 청산 주문이 총 {n}건 나갔다.")
    print("  => 재시도가 타이머로 자립 동작함을 확인. 사고 조건에서 수정이 유효하다.")
else:
    print("  틱 없이는 재청산이 발생하지 않음 — 수정이 유효하지 않다.")
