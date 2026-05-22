"""
AMATS 종합 백테스트
  - 선물: 변동성 돌파 전략 (K값 스위프, 주간/야간 세션 분리)
  - 주식: 단타 시가돌파 전략 (intraday_ohlcv 기반)
표준 라이브러리만 사용 (pandas/numpy 불필요)
"""
import sqlite3, os, sys
from datetime import datetime, timedelta
from collections import defaultdict

workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# ═══════════════════════════════════════════════════════
# 공통 통계 유틸
# ═══════════════════════════════════════════════════════
def mdd(equity):
    peak, worst = equity[0], 0.0
    for e in equity:
        if e > peak: peak = e
        d = (peak - e) / peak * 100
        if d > worst: worst = d
    return round(worst, 2)

def cagr(start, end, days):
    if days < 1 or end <= 0: return 0.0
    return round(((end / start) ** (365 / days) - 1) * 100, 2)

def profit_factor(pnls):
    gain = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p < 0))
    return round(gain / loss, 2) if loss > 0 else 999.0

def sharpe(daily_rets, rf=0.03):
    n = len(daily_rets)
    if n < 5: return 0.0
    mu = sum(daily_rets) / n
    var = sum((r - mu) ** 2 for r in daily_rets) / (n - 1)
    std = var ** 0.5
    if std == 0: return 0.0
    return round((mu * 252 - rf) / (std * (252 ** 0.5)), 2)

def parse_dt(s):
    s = str(s)[:14]
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]),
                    int(s[8:10]), int(s[10:12]), int(s[12:14]))

# ═══════════════════════════════════════════════════════
# 선물 변동성 돌파 백테스트
# ═══════════════════════════════════════════════════════
POINT_VALUE  = 250_000   # 승수 (원/pt)
MARGIN_RATE  = 0.10      # 위탁증거금률
MARGIN_CAP   = 0.30      # ERA 30% 캡
SLIP_FEE_PT  = 0.05      # 슬리피지+수수료 편도 (pt)

def _load_futures(code):
    db = os.path.join(workspace_root, "futures_data.db")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        "SELECT date, open, high, low, close, volume "
        "FROM futures_ohlcv WHERE code=? ORDER BY date ASC", (code,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def _daily_range(rows, session_open_hour, session_open_minute=0):
    """날짜별 시초가 및 Range (전일 기준)"""
    day_data = defaultdict(lambda: {'o': None, 'h': -1e9, 'l': 1e9})
    for row in rows:
        dt = parse_dt(row[0])
        # 세션 기준일: 09시 이후면 당일, 그 전(야간 잔여)이면 전일
        if session_open_hour == 9:
            day_key = dt.date() if dt.hour >= 9 else (dt - timedelta(days=1)).date()
        else:  # 야간 18시 기준
            day_key = dt.date() if dt.hour >= 18 else (dt - timedelta(days=1)).date()
        o, h, l = row[1], row[2], row[3]
        if day_data[day_key]['o'] is None:
            day_data[day_key]['o'] = o
        if h > day_data[day_key]['h']: day_data[day_key]['h'] = h
        if l < day_data[day_key]['l']: day_data[day_key]['l'] = l
    return day_data

def run_futures_bt(rows, K, session='day'):
    """
    session='day'   : 주간 09:00 → 익일 08:45
    session='night' : 야간 18:00 → 익일 04:45
    """
    INIT = 50_000_000
    cap = float(INIT)

    if session == 'day':
        open_h, open_m   = 9, 0
        exit_h, exit_m_s = 8, 45
        exit_m_e         = 55
        def in_session(dt):
            if dt.hour == 8 and 45 <= dt.minute <= 55: return True
            return dt.hour >= 9 or (dt.hour < 9 and dt.hour >= 0 and dt.hour <= 8)
        def is_open(dt): return dt.hour == 9 and dt.minute == 0
        def is_exit(dt): return dt.hour == 8 and 45 <= dt.minute <= 55
    else:
        open_h, open_m   = 18, 0
        def in_session(dt): return dt.hour >= 18 or dt.hour <= 4
        def is_open(dt):  return dt.hour == 18 and dt.minute == 0
        def is_exit(dt):  return dt.hour == 4 and 45 <= dt.minute <= 55

    day_data = _daily_range(rows, open_h)

    pos = 0
    entry = 0.0
    day_open = 0.0
    tgt_l = float('inf')
    tgt_s = float('-inf')
    session_set = False

    equity  = [cap]
    d_rets  = []
    pnls    = []
    wins    = 0

    def close_pos(exit_p, pos, entry, cap):
        qty  = max(1, int(cap * MARGIN_CAP // (entry * POINT_VALUE * MARGIN_RATE)))
        fee  = SLIP_FEE_PT * 2
        gain = ((exit_p - entry) * pos - fee) * POINT_VALUE * qty
        return gain, qty

    for row in rows:
        dt = parse_dt(row[0])
        o, h, l, c = row[1], row[2], row[3], row[4]

        if session == 'day':
            day_key = dt.date() if dt.hour >= 9 else (dt - timedelta(days=1)).date()
        else:
            day_key = dt.date() if dt.hour >= 18 else (dt - timedelta(days=1)).date()

        sorted_days = sorted(day_data.keys())
        prev_days   = [d for d in sorted_days if d < day_key]
        if not prev_days: continue
        pd = prev_days[-1]
        pr = day_data[pd]['h'] - day_data[pd]['l']
        if pr <= 0: continue

        # 세션 외부: 포지션 강제 정리 + 상태 초기화
        if not in_session(dt):
            if pos != 0:
                gain, _ = close_pos(c, pos, entry, cap)
                cap += gain; pnls.append(gain)
                equity.append(cap)
                if len(equity) >= 2:
                    d_rets.append((equity[-1] - equity[-2]) / equity[-2])
                wins += gain > 0
            pos = 0; session_set = False
            continue

        # 세션 시초가 확정
        if is_open(dt) and not session_set:
            day_open   = o
            tgt_l      = day_open + pr * K
            tgt_s      = day_open - pr * K
            session_set = True

        if not session_set: continue

        # 세션 종료 강제 청산
        if is_exit(dt) and pos != 0:
            exit_p = c - SLIP_FEE_PT if pos == 1 else c + SLIP_FEE_PT
            gain, _ = close_pos(exit_p, pos, entry, cap)
            cap += gain; pnls.append(gain)
            equity.append(cap)
            if len(equity) >= 2:
                d_rets.append((equity[-1] - equity[-2]) / equity[-2])
            wins += gain > 0
            pos = 0; session_set = False
            continue

        # 진입
        if pos == 0 and not is_exit(dt):
            if h >= tgt_l:
                pos = 1; entry = tgt_l + SLIP_FEE_PT
            elif l <= tgt_s:
                pos = -1; entry = tgt_s - SLIP_FEE_PT

    total = len(pnls)
    if total == 0 or not rows:
        return None

    start_dt = parse_dt(rows[0][0])
    end_dt   = parse_dt(rows[-1][0])
    days     = max(1, (end_dt - start_dt).days)
    wr       = round(wins / total * 100, 1)
    ptot     = round((cap - INIT) / INIT * 100, 2)

    return {
        'K': round(K, 2), 'trades': total, 'win_rate': wr,
        'profit_pct': ptot, 'cagr': cagr(INIT, cap, days),
        'mdd': mdd(equity), 'profit_factor': profit_factor(pnls),
        'sharpe': sharpe(d_rets), 'final_capital': int(cap)
    }

def section_futures():
    print("\n" + "═"*72)
    print("  선물 변동성 돌파 전략 백테스트  (2025-01 ~ 2026-05, 약 16개월)")
    print("═"*72)

    day_rows   = _load_futures('10100000')
    night_rows = _load_futures('10500000')
    print(f"  주간 데이터: {len(day_rows):,}봉  |  야간 데이터: {len(night_rows):,}봉")

    K_LIST = [round(k * 0.1, 1) for k in range(1, 11)]

    for session, rows, label in [
        ('day',   day_rows,   '주간 세션 (09:00 → 익일 08:45)'),
        ('night', night_rows, '야간 세션 (18:00 → 익일 04:45)'),
    ]:
        print(f"\n── {label} ──")
        print(f"{'K값':>5} │ {'거래':>5} │ {'승률':>6} │ {'총수익':>8} │ {'CAGR':>7} │ {'MDD':>6} │ {'손익비':>6} │ {'샤프':>6}")
        print("─"*65)
        best = None
        results = []
        for K in K_LIST:
            r = run_futures_bt(rows, K, session)
            if r is None: continue
            results.append(r)
            flag = ""
            if best is None or r['cagr'] > best['cagr']:
                best = r; flag = " ★"
            print(f"  {r['K']:.1f} │ {r['trades']:>5} │ {r['win_rate']:>5.1f}% │ "
                  f"{r['profit_pct']:>+7.2f}% │ {r['cagr']:>+6.2f}% │ "
                  f"{r['mdd']:>5.2f}% │ {r['profit_factor']:>6.2f} │ {r['sharpe']:>6.2f}{flag}")

        if best:
            print(f"\n  ★ 최적 K={best['K']} → "
                  f"CAGR {best['cagr']:+.2f}%, 승률 {best['win_rate']}%, "
                  f"MDD {best['mdd']}%, 최종자본 {best['final_capital']:,}원")

    # 통합(주간 최적K + 야간 최적K) 요약
    print("\n── 전략 요약 비교 ──")
    print(f"{'':30} │ {'최적K':>5} │ {'CAGR':>7} │ {'승률':>6} │ {'MDD':>6} │ {'샤프':>6}")
    print("─"*68)
    for session, rows, label in [
        ('day',   day_rows,   '주간 변동성 돌파'),
        ('night', night_rows, '야간 변동성 돌파'),
    ]:
        bests = []
        for K in K_LIST:
            r = run_futures_bt(rows, K, session)
            if r: bests.append(r)
        if bests:
            b = max(bests, key=lambda x: x['cagr'])
            print(f"  {label:<28} │ {b['K']:>5.1f} │ {b['cagr']:>+6.2f}% │ "
                  f"{b['win_rate']:>5.1f}% │ {b['mdd']:>5.2f}% │ {b['sharpe']:>6.2f}")
        else:
            print(f"  {label:<28} │   N/A │ 데이터 없음 (10500000 시간대 불일치 — DB 재수집 필요)")

# ═══════════════════════════════════════════════════════
# 주식 단타 전략 백테스트
# ═══════════════════════════════════════════════════════
STOCK_INIT      = 30_000_000
STOP_LOSS_PCT   = 0.02   # -2%
TAKE_PROFIT_PCT = 0.03   # +3%
VOL_MULT        = 1.5    # 거래량 1.5배 조건
BREAKOUT_PCT    = 0.02   # 시가 +2% 돌파 조건
FEE_RATE        = 0.0015 # 증권사 수수료 + 거래세 합산

def _load_stock(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT code, date, open, high, low, close, volume "
        "FROM intraday_ohlcv ORDER BY code, date ASC"
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def run_stock_bt():
    db = os.path.join(workspace_root, "unified_data.db")
    rows = _load_stock(db)
    if not rows:
        print("  intraday_ohlcv 데이터 없음")
        return

    # 종목별, 날짜별로 분리
    by_code_day = defaultdict(lambda: defaultdict(list))
    for r in rows:
        code, dt_str, o, h, l, c, v = r
        dt = parse_dt(dt_str)
        by_code_day[code][dt.date()].append((dt, o, h, l, c, v))

    all_pnls  = []
    all_wins  = 0
    all_trades = 0
    details   = []

    for code, days in by_code_day.items():
        for day, candles in sorted(days.items()):
            if len(candles) < 5: continue
            candles.sort()

            day_open = candles[0][1]  # 첫 봉 시가
            avg_vol  = sum(b[5] for b in candles[1:]) / max(len(candles) - 1, 1)

            pos = None          # (buy_price, qty)
            traded_today = False  # 하루 1종목 1회 제한

            for dt, o, h, l, c, v in candles:
                hour = dt.hour

                # 14:50 이후 신규 진입 금지, 기존 포지션 청산
                if hour >= 14 and dt.minute >= 50:
                    if pos:
                        bp, qty = pos
                        sell_p = c * (1 - FEE_RATE)
                        pnl = (sell_p - bp) * qty
                        all_pnls.append(pnl)
                        all_wins += pnl > 0
                        all_trades += 1
                        details.append({'code': code, 'day': str(day), 'result': '종가청산',
                                        'pct': round((sell_p / bp - 1) * 100, 2), 'pnl': int(pnl)})
                        pos = None
                    continue

                if pos:
                    bp, qty = pos
                    pnl_pct = (c - bp) / bp

                    # 손절 -2%
                    if l <= bp * (1 - STOP_LOSS_PCT):
                        cut_p = bp * (1 - STOP_LOSS_PCT) * (1 - FEE_RATE)
                        pnl = (cut_p - bp) * qty
                        all_pnls.append(pnl)
                        all_trades += 1
                        details.append({'code': code, 'day': str(day), 'result': '손절',
                                        'pct': round((cut_p / bp - 1) * 100, 2), 'pnl': int(pnl)})
                        pos = None

                    # 익절 +3%
                    elif h >= bp * (1 + TAKE_PROFIT_PCT):
                        tp_p = bp * (1 + TAKE_PROFIT_PCT) * (1 - FEE_RATE)
                        pnl = (tp_p - bp) * qty
                        all_pnls.append(pnl)
                        all_wins += 1
                        all_trades += 1
                        details.append({'code': code, 'day': str(day), 'result': '익절',
                                        'pct': round((tp_p / bp - 1) * 100, 2), 'pnl': int(pnl)})
                        pos = None

                else:
                    # 진입 조건: 시가 +2% 돌파 + 거래량 1.5배 + 하루 1회 제한
                    if not traded_today:
                        breakout_p = day_open * (1 + BREAKOUT_PCT)
                        if h >= breakout_p and avg_vol > 0 and v >= avg_vol * VOL_MULT:
                            buy_p = breakout_p * (1 + FEE_RATE)
                            budget = STOCK_INIT // max(len(by_code_day), 1)
                            qty = max(1, int(budget // buy_p))
                            pos = (buy_p, qty)
                            traded_today = True

    return all_trades, all_wins, all_pnls, details

def section_stock():
    print("\n" + "═"*72)
    print("  주식 단타 전략 백테스트  (2026-05-15 ~ 2026-05-18, 9종목, 3일)")
    print("  ⚠  3일 데이터 — 방향성 확인 수준, 통계적 유의성 없음")
    print("═"*72)

    result = run_stock_bt()
    if not result:
        return

    trades, wins, pnls, details = result

    if trades == 0:
        print("  해당 기간 진입 신호 발생 없음 (돌파 조건 미충족)")
        return

    wr    = round(wins / trades * 100, 1)
    net   = sum(pnls)
    pct   = round(net / STOCK_INIT * 100, 2)
    pf    = profit_factor(pnls)
    avg_w = round(sum(p for p in pnls if p > 0) / max(wins, 1))
    avg_l = round(sum(p for p in pnls if p <= 0) / max(trades - wins, 1))

    print(f"\n  총 거래:     {trades}건")
    print(f"  승률:        {wr}%  ({wins}승 {trades - wins}패)")
    print(f"  누적 손익:   {net:+,.0f}원  ({pct:+.2f}%)")
    print(f"  손익비(PF):  {pf}")
    print(f"  평균 수익:   {avg_w:+,}원  │  평균 손실: {avg_l:+,}원")

    print(f"\n  {'종목':<8} │ {'날짜':<12} │ {'결과':<6} │ {'수익률':>7} │ {'손익':>12}")
    print("  " + "─"*56)
    for d in details:
        print(f"  {d['code']:<8} │ {d['day']:<12} │ {d['result']:<6} │ "
              f"{d['pct']:>+6.2f}% │ {d['pnl']:>+12,}원")

    # 전략 한계 설명
    print(f"""
  ─────────────────────────────────────────────────
  [데이터 한계 및 권고사항]
  • 현재 데이터: 3거래일 × 9종목 → 최소 60거래일(3개월) 이상 필요
  • 스마트머니 필터(ThemeTracker) 미적용 → 실제 진입 후보 축소 예상
  • RSA 70점 필터 미적용 → 실제 거래횟수 감소 예상
  • 종가베팅(스윙) 전략은 일봉 데이터 필요 — 별도 수집 후 검증 권장
  ─────────────────────────────────────────────────""")

# ═══════════════════════════════════════════════════════
# 선물 월별 성과 분석 (최적 K 기준)
# ═══════════════════════════════════════════════════════
def section_monthly():
    print("\n" + "═"*72)
    print("  선물 월별 수익률 분석  (주간 세션, 최적 K 기준)")
    print("═"*72)

    rows = _load_futures('10100000')
    # K 스위프로 최적 찾기
    best_k, best_cagr = 0.5, -999.0
    for ki in range(1, 11):
        K = round(ki * 0.1, 1)
        r = run_futures_bt(rows, K, 'day')
        if r and r['cagr'] > best_cagr:
            best_cagr = r['cagr']
            best_k = K

    INIT  = 50_000_000
    cap   = float(INIT)
    monthly = defaultdict(lambda: {'start': 0.0, 'end': 0.0})
    day_data = _daily_range(rows, 9)

    pos = 0; entry = 0.0
    tgt_l = float('inf'); tgt_s = float('-inf')
    session_set = False
    K = best_k

    for row in rows:
        dt = parse_dt(row[0])
        o, h, l, c = row[1], row[2], row[3], row[4]
        ym = (dt.year, dt.month)
        day_key = dt.date() if dt.hour >= 9 else (dt - timedelta(days=1)).date()

        sorted_days = sorted(day_data.keys())
        prev_days   = [d for d in sorted_days if d < day_key]
        if not prev_days: continue
        pd2 = prev_days[-1]
        pr = day_data[pd2]['h'] - day_data[pd2]['l']
        if pr <= 0: continue

        def in_s(dt): return dt.hour >= 9 or (dt.hour == 8 and dt.minute >= 45)
        def is_o(dt): return dt.hour == 9 and dt.minute == 0
        def is_e(dt): return dt.hour == 8 and 45 <= dt.minute <= 55

        if not in_s(dt):
            if pos != 0:
                qty  = max(1, int(cap * MARGIN_CAP // (entry * POINT_VALUE * MARGIN_RATE)))
                pnl  = ((c - entry) * pos - SLIP_FEE_PT * 2) * POINT_VALUE * qty
                cap += pnl
            pos = 0; session_set = False
            continue

        if is_o(dt) and not session_set:
            day_open_p = o
            tgt_l = day_open_p + pr * K
            tgt_s = day_open_p - pr * K
            session_set = True
            if monthly[ym]['start'] == 0.0:
                monthly[ym]['start'] = cap

        if not session_set: continue
        monthly[ym]['end'] = cap

        if is_e(dt) and pos != 0:
            ep = c - SLIP_FEE_PT if pos == 1 else c + SLIP_FEE_PT
            qty = max(1, int(cap * MARGIN_CAP // (entry * POINT_VALUE * MARGIN_RATE)))
            cap += ((ep - entry) * pos) * POINT_VALUE * qty
            pos = 0; session_set = False
            monthly[ym]['end'] = cap
            continue

        if pos == 0 and not is_e(dt):
            if h >= tgt_l: pos = 1; entry = tgt_l + SLIP_FEE_PT
            elif l <= tgt_s: pos = -1; entry = tgt_s - SLIP_FEE_PT

    print(f"\n  K={best_k} (최적값) 기준  |  초기자본 {INIT:,}원\n")
    print(f"  {'연월':>8} │ {'월초 자본':>14} │ {'월말 자본':>14} │ {'월 수익률':>10} │ {'누적':>8}")
    print("  " + "─"*62)
    running = INIT
    for ym in sorted(monthly.keys()):
        m = monthly[ym]
        s = m['start'] if m['start'] > 0 else running
        e = m['end']   if m['end']   > 0 else s
        pct = round((e - s) / s * 100, 2) if s > 0 else 0
        cum = round((e - INIT) / INIT * 100, 2)
        flag = " ↑" if pct > 0 else (" ↓" if pct < 0 else "")
        print(f"  {ym[0]}-{ym[1]:02d}   │ {int(s):>14,} │ {int(e):>14,} │ "
              f"{pct:>+9.2f}%{flag} │ {cum:>+7.2f}%")
        running = e

# ═══════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import io, sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print("\n" + "="*72)
    print("  AMATS 종합 백테스트 리포트")
    print(f"  실행일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*72)

    section_futures()
    section_monthly()
    section_stock()

    print("\n" + "═"*72)
    print("  백테스트 완료")
    print("═"*72 + "\n")
