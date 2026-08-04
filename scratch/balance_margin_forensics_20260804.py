"""예수금 변동 규명 + 증거금률 실측 — 2026-08-04.

두 가지 미해결 항목:
  P2. 15:04→15:09에 거래 없이 예수금이 +5,182만원 늘었고, 17:19에 -418,260원 추가 변동.
  P3. 코드는 MARGIN_RATE=0.10을 가정하는데, 청산 시 반환액을 보면 약 13.9%로 보인다.

방법:
  로그에서 (시각, 예수금, 포지션 유무/방향/수량/평단)을 시계열로 뽑고,
  진입·청산 이벤트 전후의 예수금 점프를 분해한다.
      진입 시  : 예수금 감소분 = 증거금
      청산 시  : 예수금 증가분 = 증거금 반환 + 실현손익
  실현손익은 체결가로 따로 계산되므로, 증거금률을 역산할 수 있다.
"""
import re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

LOG = os.path.join(os.path.dirname(__file__), '..', 'era', 'era_order_manager.log')
START = "PID 21320 기록 완료"
PV = 50_000

lines = open(LOG, encoding='utf-8', errors='replace').read().splitlines()
start = max(i for i, l in enumerate(lines) if START in l)
seg = lines[start:]

p_time = re.compile(r'동기화 TR 요청\] 시각: (\d+:\d+:\d+)')
p_bal = re.compile(r'선물 예수금: ([\d,]+)원')
p_pos = re.compile(r'- \[선물\] \S+ \| (LONG|SHORT) \| (\d+)계약 \| 평단: ([0-9.]+)pt \(현재가: ([0-9.]+)pt\)')
p_flat = re.compile(r'기존 선물 포지션 실계좌 연동')
p_order = re.compile(r'\[주간선물 주문\] (LONG|SHORT) (진입|청산)\s+\| ([0-9.]+)pt \| (\d+)계약')

# 시각별 스냅샷 구성
snaps = []       # (시각, 예수금, 포지션or None)
cur_t, cur_bal, pending_pos, saw_flat = None, None, None, False
for l in seg:
    m = p_time.search(l)
    if m:
        if cur_t and cur_bal is not None:
            snaps.append((cur_t, cur_bal, pending_pos))
        cur_t, cur_bal, pending_pos, saw_flat = m.group(1), None, None, False
        continue
    b = p_bal.search(l)
    if b and cur_bal is None:
        cur_bal = int(b.group(1).replace(',', ''))
    if p_flat.search(l):
        saw_flat = True
    pm = p_pos.search(l)
    if pm and saw_flat:
        pending_pos = (pm.group(1), int(pm.group(2)), float(pm.group(3)), float(pm.group(4)))
if cur_t and cur_bal is not None:
    snaps.append((cur_t, cur_bal, pending_pos))

print("=" * 118)
print("예수금 · 포지션 시계열 (변화가 있는 시점만)")
print("=" * 118)
print(f"  {'시각':>9s} {'예수금':>16s} {'증감':>16s}  포지션")
prev_bal = None
events = []
for t, bal, pos in snaps:
    d = None if prev_bal is None else bal - prev_bal
    posdesc = "flat" if pos is None else f"{pos[0]} {pos[1]}계약 @{pos[2]:.2f} (현재가 {pos[3]:.2f})"
    if d is None or d != 0:
        print(f"  {t:>9s} {bal:>16,} {(f'{d:+,}' if d is not None else '-'):>16s}  {posdesc}")
        if d is not None and d != 0:
            events.append((t, d, pos))
    prev_bal = bal

# 증거금률 역산: 청산으로 flat이 된 시점의 증감에서 실현손익을 빼면 증거금 반환액
print()
print("=" * 118)
print("증거금률 역산 — 청산 직후(flat 전환) 예수금 증가 = 증거금 반환 + 실현손익")
print("=" * 118)
TRADES = {  # 청산시각(대략) -> (방향, 진입평단, 청산평단, 수량)
    '0929': ('LONG', 1002.55, 975.49, 15),
    '1009': ('SHORT', 975.15, 963.43, 15),
    '1049': ('SHORT', 962.18, 988.87, 15),
    '1059': ('LONG', 989.29, 990.09, 15),
    '1224': ('SHORT', 975.20, 974.09, 15),
    '1249': ('SHORT', 971.98, 972.21, 15),
    '1304': ('SHORT', 970.33, 970.27, 15),
    '1434': ('SHORT', 963.52, 989.39, 15),
    '1459': ('LONG', 991.55, 991.41, 15),
}
print(f"  {'구간':>16s} {'예수금증감':>16s} {'실현손익':>15s} {'증거금반환(추정)':>18s} {'명목가치':>16s} {'증거금률':>9s}")
rates = []
for i, (t, d, pos) in enumerate(events):
    if d <= 0:
        continue
    hm = t.replace(':', '')[:4]
    # 이 증가가 어떤 청산에 대응하는지: 직전 5분 이내 청산시각 매칭
    key = None
    for k in TRADES:
        if 0 <= (int(hm[:2]) * 60 + int(hm[2:])) - (int(k[:2]) * 60 + int(k[2:])) <= 10:
            key = k
    if not key:
        continue
    dirn, ent, ex, q = TRADES[key]
    pnl = ((ex - ent) if dirn == 'LONG' else (ent - ex)) * q * PV
    margin_back = d - pnl
    notional = ent * PV * q
    rate = margin_back / notional if notional else 0
    rates.append(rate)
    print(f"  {key}청산→{hm:>4s} {d:>16,} {pnl:>+15,.0f} {margin_back:>18,.0f} {notional:>16,.0f} {rate:>8.2%}")

if rates:
    print()
    print(f"  실측 증거금률: 평균 {sum(rates)/len(rates):.2%} | 최소 {min(rates):.2%} | 최대 {max(rates):.2%}")
    print(f"  코드 가정치 : 10.00%  (era_order_manager.py 계약수 산정, bqa MARGIN_RATE)")

print()
print("=" * 118)
print("거래 없는 구간의 예수금 변동 (P2 대상)")
print("=" * 118)
for t, d, pos in events:
    hm = t.replace(':', '')[:4]
    near = any(0 <= (int(hm[:2])*60+int(hm[2:])) - (int(k[:2])*60+int(k[2:])) <= 10 for k in TRADES)
    if not near:
        print(f"  {t:>9s} {d:>+16,}  포지션: {'flat' if pos is None else pos[0]}")
