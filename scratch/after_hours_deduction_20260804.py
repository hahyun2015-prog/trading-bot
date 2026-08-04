"""장 마감 후 예수금 차감 산식 규명 — 2026-08-04.

미해결 항목:
    2026-08-04 17:19에 거래도 포지션도 없는데 예수금이 -418,260원 변했다.
    금액은 작지만 산식을 모르면 실계좌 전환 시 손익 대사가 어긋난다.

방법:
    로그 전체에서 '16시 이후 · 포지션 flat' 상태의 예수금 변동을 날짜별로 뽑고,
    같은 날의 거래량(주문 건수 · 계약 수)과 대조해 비례 관계를 찾는다.
"""
import re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

LOG = os.path.join(os.path.dirname(__file__), '..', 'era', 'era_order_manager.log')
lines = open(LOG, encoding='utf-8', errors='replace').read().splitlines()

p_time = re.compile(r'동기화 TR 요청\] 시각: (\d+):(\d+):(\d+)')
p_bal = re.compile(r'선물 예수금: ([\d,]+)원')
p_pos = re.compile(r'- \[선물\] \S+ \| (LONG|SHORT) \| (\d+)계약')
p_flat = re.compile(r'기존 선물 포지션 실계좌 연동')
p_order = re.compile(r'\[주간선물 주문\] (LONG|SHORT) (진입|청산)\s+\| ([0-9.]+)pt \| (\d+)계약')
p_fill = re.compile(r'\[주간선물 실체결 확정\].*?\| ([0-9.]+) \| (\d+)계약')
p_date = re.compile(r'daily_balance_history 기록 완료: (\d{4}-\d{2}-\d{2})')
p_pid = re.compile(r'PID \d+ 기록 완료')

# 세션(기동) 단위로 날짜를 추정하며 훑는다
cur_date = None
cur_hh = cur_mm = None
prev_bal = None
has_pos = False
day_orders = {}      # date -> [(방향,행위,가격,수량)]
day_fills = {}       # date -> [(가격,수량)]
events = []          # (date, hhmm, delta, flat여부)

for l in lines:
    md = p_date.search(l)
    if md:
        cur_date = md.group(1)
    mt = p_time.search(l)
    if mt:
        cur_hh, cur_mm = int(mt.group(1)), int(mt.group(2))
        has_pos = False
        continue
    if p_flat.search(l):
        has_pos = False
    if p_pos.search(l):
        has_pos = True
    mo = p_order.search(l)
    if mo and cur_date:
        day_orders.setdefault(cur_date, []).append(
            (mo.group(1), mo.group(2), float(mo.group(3)), int(mo.group(4))))
    mf = p_fill.search(l)
    if mf and cur_date:
        day_fills.setdefault(cur_date, []).append((float(mf.group(1)), int(mf.group(2))))
    mb = p_bal.search(l)
    if mb:
        bal = int(mb.group(1).replace(',', ''))
        if prev_bal is not None and bal != prev_bal and cur_hh is not None:
            events.append((cur_date, f"{cur_hh:02d}:{cur_mm:02d}", bal - prev_bal, not has_pos))
        prev_bal = bal

print("=" * 104)
print("장 마감 후(16시 이후) · 포지션 flat 상태의 예수금 변동")
print("=" * 104)
after = [e for e in events if e[1] >= "16:00" and e[3]]
if not after:
    print("  해당 이벤트 없음")
for d, hm, dv, _ in after:
    orders = day_orders.get(d, [])
    fills = day_fills.get(d, [])
    n_ord = len(orders)
    n_ctr = sum(q for *_, q in orders)
    fill_ctr = sum(q for _, q in fills)
    notional = sum(px * q for px, q in fills) * 50_000
    print(f"  {d} {hm}  {dv:>+12,}원 | 주문 {n_ord:>2d}건 {n_ctr:>3d}계약 | 체결 {fill_ctr:>3d}계약 | 체결명목 {notional:>16,.0f}원")
    if notional > 0:
        print(f"{'':>28s}→ 명목 대비 {abs(dv)/notional*100:.6f}%   계약당 {abs(dv)/max(1,fill_ctr):>10,.1f}원   주문당 {abs(dv)/max(1,n_ord):>12,.0f}원")

print()
print("=" * 104)
print("참고 — 백테스터가 쓰는 수수료율로 계산하면")
print("=" * 104)
for d, hm, dv, _ in after:
    fills = day_fills.get(d, [])
    notional = sum(px * q for px, q in fills) * 50_000
    if notional <= 0:
        continue
    print(f"  {d}: 체결명목 {notional:>16,.0f}원")
    for rate, lab in ((0.000065, 'commission_rate 0.0065% (백테스터 기본)'),
                      (0.00003, '0.0030%'), (0.0000188, '0.00188%')):
        print(f"      x {lab:38s} = {notional*rate:>12,.0f}원   (실제 {abs(dv):,}원)")
