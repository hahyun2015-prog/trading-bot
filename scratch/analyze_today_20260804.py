"""2026-08-04 주간선물 거래 분석 — 로그에서 주문/체결을 추출해 라운드트립 손익을 집계한다."""
import re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

LOG = os.path.join(os.path.dirname(__file__), '..', 'era', 'era_order_manager.log')
START_MARK = "PID 21320 기록 완료"   # 2026-08-04 04:05 기동 세션부터
PV = 50_000                          # 미니선물 승수

lines = open(LOG, encoding='utf-8', errors='replace').read().splitlines()
start = max(i for i, l in enumerate(lines) if START_MARK in l)
seg = lines[start:]

pat_order = re.compile(r'\[주간선물 주문\] (LONG|SHORT) (진입|청산)\s+\| ([0-9.]+)pt \| (\d+)계약')
pat_fill = re.compile(r'\[주간선물 실체결 확정\].*?\| ([0-9.]+) \| (\d+)계약 \| ([+-])(매수|매도)')
pat_time = re.compile(r'동기화 TR 요청\] 시각: (\d+:\d+:\d+)')
pat_safe = re.compile(r'\[선물 안전 청산\]')
pat_eod = re.compile(r'장마감 전 강제 청산')

events, cur_time = [], "?"
for l in seg:
    mt = pat_time.search(l)
    if mt:
        cur_time = mt.group(1)
    mo = pat_order.search(l)
    if mo:
        events.append({'kind': 'order', 'dir': mo.group(1), 'act': mo.group(2),
                       'price': float(mo.group(3)), 'qty': int(mo.group(4)), 't': cur_time})
    mf = pat_fill.search(l)
    if mf:
        events.append({'kind': 'fill', 'price': float(mf.group(1)), 'qty': int(mf.group(2)),
                       'side': mf.group(4), 't': cur_time})
    if pat_safe.search(l):
        events.append({'kind': 'note', 'txt': '08:45 안전청산 발동', 't': cur_time})
    if pat_eod.search(l):
        events.append({'kind': 'note', 'txt': '장마감 강제청산 발동', 't': cur_time})

# 주문 단위로 체결을 묶어 평균단가 산출
print("=" * 104)
print("2026-08-04 주간선물 주문·체결 내역")
print("=" * 104)
orders = []
i = 0
while i < len(events):
    e = events[i]
    if e['kind'] == 'note':
        print(f"  [{e['t']}] ── {e['txt']} ──")
        i += 1
        continue
    if e['kind'] == 'order':
        fills, j = [], i + 1
        while j < len(events) and events[j]['kind'] == 'fill':
            fills.append(events[j]); j += 1
        tot_q = sum(f['qty'] for f in fills)
        avg = (sum(f['price'] * f['qty'] for f in fills) / tot_q) if tot_q else 0.0
        orders.append({'dir': e['dir'], 'act': e['act'], 'req': e['price'],
                       'qty': tot_q, 'avg': avg, 't': e['t']})
        status = f"체결 {tot_q}계약 @ {avg:.2f}" if tot_q else "★ 체결 없음"
        print(f"  [{e['t']}] {e['dir']:5s} {e['act']} 주문 {e['price']:8.2f}pt {e['qty']:2d}계약  →  {status}")
        i = j
        continue
    i += 1

# 라운드트립 구성
print()
print("=" * 104)
print("라운드트립 손익")
print("=" * 104)
pos = None
trips, total = [], 0.0
for o in orders:
    if o['qty'] == 0:
        continue
    if o['act'] == '진입':
        pos = o
    elif o['act'] == '청산' and pos:
        pnl_pt = (o['avg'] - pos['avg']) if pos['dir'] == 'LONG' else (pos['avg'] - o['avg'])
        krw = pnl_pt * o['qty'] * PV
        trips.append((pos, o, pnl_pt, krw))
        total += krw
        pos = None
    elif o['act'] == '청산' and not pos:
        # 전일 이월 포지션 청산 (진입 로그가 이 세션에 없음)
        trips.append((None, o, None, None))

for ent, ex, pt, krw in trips:
    if ent is None:
        print(f"  [{ex['t']}] (전일 이월분) {ex['dir']} 청산 @ {ex['avg']:.2f} × {ex['qty']}계약 — 진입가는 어제 로그 기준 별도 계산")
    else:
        print(f"  {ent['t']}~{ex['t']}  {ent['dir']:5s} {ent['avg']:8.2f} → {ex['avg']:8.2f}  "
              f"{pt:+7.2f}pt × {ex['qty']}계약 = {krw:+15,.0f}원")

print()
print(f"  세션 내 라운드트립 합계: {total:+,.0f}원")
if pos:
    print(f"  ⚠️ 미청산 포지션: {pos['dir']} {pos['qty']}계약 @ {pos['avg']:.2f}")
else:
    print("  미청산 포지션 없음")
