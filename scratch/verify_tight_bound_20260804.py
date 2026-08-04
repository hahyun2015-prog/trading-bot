"""1안(타이트 폭을 트리거에 종속) 역검증 — 2026-08-04 실거래 9건을 실제 5분봉 경로로 재생.

1안:
    tight = min(profit_lock_mult * ATR14, profit_lock_trigger_pt * BOUND)
    현행은 tight = 0.10 * ATR14 뿐이라, ATR이 80pt를 넘으면 tight > trigger가 되어
    "8pt 잠그고 9.55pt 되뱉는" 상태가 된다(2026-08-04 ATR 95.5pt에서 실제 발생).

주의:
    진입가·관측고점만으로 재계산하면 결과가 부풀려진다. 폭이 좁아지면 관측된 고점에
    도달하기 전에 먼저 청산됐을 수 있기 때문이다. 그래서 실제 5분봉을 순서대로 훑어
    "먼저 걸리는 쪽"으로 판정한다.
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

ATR = 95.5
CH_MULT, HARD_CAP = 0.30, 60.0
CAP_MULT = 0.50
TRIG, LOCK_MULT, BE_BUF = 8.0, 0.10, 1.0
BE_MOVE_TRIG, BE_STAGE_BUF = 4.0, 0.0
PV, QTY = 50_000, 15

# 2026-08-04 실제 체결 (로그 기준): (진입시각HHMM, 방향, 진입가, 실제청산가, 실제손익pt)
TRADES = [
    ("0909", "LONG",  1002.98,  975.34, -27.64),
    ("0934", "SHORT",  975.90,  963.12, +12.78),
    ("1014", "SHORT",  962.02,  988.66, -26.64),
    ("1049", "LONG",   989.24,  990.22,  +0.98),
    ("1129", "SHORT",  975.10,  974.12,  +0.98),
    ("1229", "SHORT",  972.04,  972.08,  -0.04),
    ("1259", "SHORT",  970.26,  970.40,  -0.14),
    ("1309", "SHORT",  963.42,  989.80, -26.38),
    ("1434", "LONG",   991.30,  991.24,  -0.06),
]


def load_bars():
    c = sqlite3.connect(os.path.join(os.path.dirname(__file__), '..', 'futures_data.db')).cursor()
    rows = c.execute(
        "SELECT date, open, high, low, close FROM futures_ohlcv "
        "WHERE code='A0568000' AND date LIKE '20260804%' ORDER BY date").fetchall()
    return [(r[0][8:12], r[1], r[2], r[3], r[4]) for r in rows]


def simulate(bars, entry_hm, direction, entry, bound, lock_on=True):
    """bound=None이면 현행(상한 없음). lock_on=False면 이익보전 전체 해제(2026-07-30 이전 방식).
    진입 봉 다음 봉부터 순서대로 훑는다."""
    idx = next((i for i, b in enumerate(bars) if b[0] >= entry_hm), None)
    if idx is None:
        return None, "봉없음"
    day_hi = max(b[2] for b in bars[:idx + 1])
    day_lo = min(b[3] for b in bars[:idx + 1])
    peak = entry
    for b in bars[idx + 1:]:
        hm, o, hi, lo, cl = b
        day_hi, day_lo = max(day_hi, hi), min(day_lo, lo)
        peak = max(peak, hi) if direction == "LONG" else min(peak, lo)
        mfe = (peak - entry) if direction == "LONG" else (entry - peak)

        dist = min(CH_MULT * ATR, HARD_CAP)
        rng = day_hi - day_lo
        if rng > 0:
            dist = min(dist, CAP_MULT * rng)

        floor = None
        if lock_on:
            if mfe >= BE_MOVE_TRIG:
                floor = entry + BE_STAGE_BUF if direction == "LONG" else entry - BE_STAGE_BUF
            if mfe >= TRIG:
                tight = LOCK_MULT * ATR
                if bound is not None:
                    tight = min(tight, TRIG * bound)
                dist = min(dist, tight)
                floor = entry + BE_BUF if direction == "LONG" else entry - BE_BUF

        if direction == "LONG":
            stop = peak - dist
            if floor is not None:
                stop = max(stop, floor)
            if lo <= stop:
                return stop - entry, hm
        else:
            stop = peak + dist
            if floor is not None:
                stop = min(stop, floor)
            if hi >= stop:
                return entry - stop, hm
        if hm >= "1545":
            break
    return (cl - entry) if direction == "LONG" else (entry - cl), "마감"


def main():
    bars = load_bars()
    print(f"[데이터] 2026-08-04 5분봉 {len(bars)}개 ({bars[0][0]}~{bars[-1][0]})")
    print(f"[설정] ATR={ATR}pt → 현행 tight={LOCK_MULT*ATR:.2f}pt (트리거 {TRIG}pt보다 큼)")
    print()
    print(f"{'진입':>6s} {'방향':>6s} {'진입가':>9s} | {'실제':>9s} | {'재생(현행)':>13s} | {'이익보전 OFF':>14s} | {'1안 bound0.5':>14s}")
    print("-" * 98)
    tot = {'real': 0.0, 'cur': 0.0, 'off': 0.0, 'b50': 0.0}
    for hm, d, e, xr, real in TRADES:
        cur, t0 = simulate(bars, hm, d, e, None)
        off, t3 = simulate(bars, hm, d, e, None, lock_on=False)
        b50, t1 = simulate(bars, hm, d, e, 0.5)
        tot['real'] += real; tot['cur'] += cur or 0; tot['off'] += off or 0; tot['b50'] += b50 or 0
        print(f"{hm:>6s} {d:>6s} {e:>9.2f} | {real:>+9.2f} | {cur:>+9.2f}({t0:>4s}) | {off:>+9.2f}({t3:>4s}) | {b50:>+9.2f}({t1:>4s})")
    print("-" * 98)
    print(f"{'합계':>6s} {'':>6s} {'':>9s} | {tot['real']:>+9.2f} | {tot['cur']:>+13.2f} | {tot['off']:>+14.2f} | {tot['b50']:>+14.2f}")
    print()
    print("원화 환산 (15계약 × 50,000원):")
    for k, lab in (('real', '실제'), ('cur', '재생(현행)'), ('off', '이익보전 OFF'), ('b50', '1안 bound0.5')):
        print(f"  {lab:14s} {tot[k]*QTY*PV:>+16,.0f}원")
    print()
    print("※ 재생(현행)이 실제와 크게 다르면 이 시뮬레이션 자체를 신뢰할 수 없다는 뜻이다.")


if __name__ == '__main__':
    main()
