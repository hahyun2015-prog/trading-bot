"""2026-07-31 시세 락아웃 사고 재현 + 수정 검증.

실제 사고: 09:05에 932.66 -> 991.50 (+6.31%) 개장갭이 발생하자
  1) 신규봉 경로가 시가를 직전 종가(932.66)로 대체
  2) 이후 모든 틱(972~1036)이 그 스테일 기준 대비 3% 밖이라 전부 거부
  3) 봉이 바뀌어도 대체가 반복되어 하루 종일 락아웃 (93,894틱 거부, 당일 체결 0건)

이 스크립트는 실제 _update_futures_ohlcv()를 그대로 호출해서
  - 복구 OFF: 락아웃이 재현되는지
  - 복구 ON : 락아웃이 풀리고 내부가격이 실제 시세를 따라가는지
를 확인한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import era.era_order_manager as eom

CODE = 'A0568000'
STALE = 932.66     # 개장 전 마지막 종가
GAP = 991.50       # 09:05 개장갭 시가 (+6.31%)
# 이후 실제 흐름 (오늘 DB 실측 종가에서 발췌)
AFTER = [999.14, 997.12, 1001.00, 1011.20, 1020.66, 1017.66, 1017.82, 1012.68,
         1023.92, 1027.62, 1034.96, 1035.98, 1033.70, 1034.06, 1031.80, 1024.60,
         1024.00, 1017.08, 1010.66, 1004.86, 1006.30, 1009.02, 1000.62, 1007.50]


def make_mgr(recovery_enabled):
    m = object.__new__(eom.ERAOrderManager)
    m._tick_reject = {}
    m._tick_feed_alerted = {}
    m.ohlcv_buffer = {}
    m.futures_tick_max_jump_pct = 0.03
    m.futures_tick_recovery_enabled = recovery_enabled
    m.futures_tick_recovery_streak = 20
    m.futures_tick_recovery_band_pct = 0.01
    m.futures_tick_health_alert = False   # notifier 호출 회피
    return m


def run(recovery_enabled, label):
    m = make_mgr(recovery_enabled)
    # 개장 전 봉을 과거 period 키로 심어둔다 (다음 틱이 '신규봉' 경로를 타게 함)
    old_key = '20260731090000'
    m.ohlcv_buffer[CODE] = {old_key: {'o': STALE, 'h': STALE, 'l': STALE, 'c': STALE, 'v': 1}}

    import io, contextlib
    logbuf = io.StringIO()
    with contextlib.redirect_stdout(logbuf):
        # 09:05 개장갭 첫 틱 + 같은 레벨의 틱들이 이어짐 (실제로도 초당 수십 틱)
        m._update_futures_ohlcv(CODE, GAP)
        for _ in range(25):
            m._update_futures_ohlcv(CODE, GAP)
        # 이후 실제 시세 흐름
        for p in AFTER:
            for _ in range(25):
                m._update_futures_ohlcv(CODE, p)
    out = logbuf.getvalue()

    buf = m.ohlcv_buffer[CODE]
    cur = buf[max(buf.keys())]
    rejects = out.count('[ERA 이상치 필터]')
    recovers = out.count('[ERA 이상치 복구]')
    total_ticks = 1 + 25 + len(AFTER) * 25

    print(f'\n=== {label} ===')
    print(f'  총 투입 틱      : {total_ticks}')
    print(f'  거부 로그       : {rejects}')
    print(f'  복구 로그       : {recovers}')
    print(f'  내부가격(종가)  : {cur["c"]:.2f}pt   (실제 마지막 시세 {AFTER[-1]:.2f}pt)')
    print(f'  내부 봉 고가/저가: {cur["h"]:.2f} / {cur["l"]:.2f}')
    tracking = abs(cur['c'] - AFTER[-1]) < 0.01
    print(f'  => 실제 시세 추종: {"OK" if tracking else "실패(락아웃)"}')
    return tracking, rejects, recovers


print('2026-07-31 개장갭 락아웃 재현 및 수정 검증')
print('=' * 60)
off_track, off_rej, _ = run(False, '복구 OFF (사고 당시 코드와 동일 동작)')
on_track, on_rej, on_rec = run(True, '복구 ON (이번 수정 적용)')

print('\n' + '=' * 60)
print('판정')
print('=' * 60)
print(f'  복구 OFF: 시세 추종 {"OK" if off_track else "실패"} / 거부 {off_rej}건  '
      f'-> 사고 재현 {"성공" if not off_track else "실패(재현 안됨)"}')
print(f'  복구 ON : 시세 추종 {"OK" if on_track else "실패"} / 거부 {on_rej}건, 복구 {on_rec}건  '
      f'-> 수정 {"유효" if on_track else "무효"}')
if (not off_track) and on_track:
    print('\n  결론: 사고가 재현되고, 수정 적용 시 락아웃이 해소됨을 확인.')
else:
    print('\n  결론: 기대와 다름 — 추가 확인 필요.')
