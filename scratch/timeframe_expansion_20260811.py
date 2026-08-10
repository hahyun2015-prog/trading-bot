"""시간축 확대 — 5/15/30/60분봉에서 추세추종이 달라지는가 (2026-08-11).

배경:
    2026-08-11 분기별 분해에서 벽의 정체가 드러났다. 2025-Q1~Q3은 일중 레인지가
    4~6pt인데 왕복 비용이 2pt대다. 레인지의 절반이 비용으로 나가면 어떤 신호도
    살아남지 못한다. 실제로 그 구간 PF는 0.00~0.08이었다.

    비용은 **거래당 고정**이고 시간축과 무관하다. 반면 한 거래가 잡는 폭은 시간축이
    길어질수록 커진다. 그렇다면 봉을 키우는 것만으로 비용 비율이 개선될 수 있다.
    5분봉에서 최고였던 해외봇 추세추종(SMA 50/150 + ATR×3.0, PF 0.92)을 기준으로
    15/30/60분봉까지 넓혀 확인한다.

방법:
    - 5분봉을 시계 경계에 맞춰 리샘플링한다(30분이면 09:00, 09:30, ...)
    - 봉 개수 기반 파라미터는 시간축마다 **다시 스윕한다.** 기계적으로 환산만 하면
      그 시간축에서의 최적을 놓친다
    - 장마감 강제청산 시각은 시간축마다 다르다(60분봉의 마지막 봉은 15:00 시작).
      각 시간축의 실제 마지막 봉 시각을 찾아 쓴다
    - 비용은 시간축과 무관하게 동일: 슬리피지 1.0pt(편도) + 수수료 3,000원(왕복)
"""
import sys
import sqlite3
from collections import Counter
from datetime import datetime, time

sys.path.insert(0, r"c:\Antigravity\해외주식\src")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from futures_bot.types import Bar
from futures_bot.regime import Regime
from futures_bot.strategies.trend_following import TrendFollowingStrategy
from futures_bot.strategies.mean_reversion import MeanReversionStrategy
from futures_bot.risk import RiskManager, RiskDecision
from futures_bot.backtest.engine import run_backtest, BacktestConfig

DB = r"c:\Antigravity\AI_T_Agent\futures_data.db"
POINT_VALUE = 50_000
SLIPPAGE_PT = 1.0
COMMISSION_KRW = 3_000
START_EQUITY = 50_000_000.0


class FixedSizeRisk(RiskManager):
    """1계약 고정. 신호 품질만 비교하기 위한 것 — PF·승률은 사이즈에 무관하다."""

    def evaluate(self, signal, equity, current_net_size):
        d = super().evaluate(signal, equity, current_net_size)
        if d.reason in ("cooldown_active", "daily_loss_limit_reached", "invalid_stop_distance"):
            return d
        return RiskDecision(True, 1, signal.stop_price, "fixed_size_1")


class AlwaysTrend:
    def update(self, adx):
        return Regime.TREND


def load_frame(code="10500000"):
    rows = sqlite3.connect(DB).execute(
        "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code=? ORDER BY date",
        (code,)).fetchall()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["dt"] = pd.to_datetime(df["date"], format="%Y%m%d%H%M%S")
    return df.set_index("dt")


def resample(df, minutes):
    if minutes == 5:
        out = df[["open", "high", "low", "close", "volume"]].copy()
    else:
        out = df.resample(f"{minutes}min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["open", "close"])
    # 세션 밖(리샘플이 만든 빈 구간)은 이미 dropna로 빠진다
    return out


def to_bars(out):
    return [Bar(ts.to_pydatetime(), r.open, r.high, r.low, r.close, r.volume or 0)
            for ts, r in out.iterrows()]


def last_bar_time(out):
    """각 거래일의 마지막 봉 시각 중 최빈값 — 강제청산 기준으로 쓴다."""
    times = out.groupby(out.index.date).apply(lambda g: g.index[-1].time())
    return Counter(times).most_common(1)[0][0]


def stats(trades):
    if not trades:
        return None
    pnls = np.array([t.pnl for t in trades])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    gl = -losses.sum()
    eq = np.concatenate([[START_EQUITY], START_EQUITY + np.cumsum(pnls)])
    mdd = ((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq) * 100).max()
    return dict(trades=len(trades), win_rate=len(wins) / len(trades) * 100,
                pf=(wins.sum() / gl) if gl > 0 else float("inf"), mdd=mdd,
                final=START_EQUITY + pnls.sum(),
                avg_win=(wins.mean() / POINT_VALUE) if len(wins) else 0.0,
                avg_loss=(losses.mean() / POINT_VALUE) if len(losses) else 0.0)


def run(bars, close_t, fast, slow, am):
    cfg = BacktestConfig(starting_equity=START_EQUITY, tick_value=POINT_VALUE,
                         slippage=SLIPPAGE_PT, commission_per_contract=COMMISSION_KRW,
                         adx_period=14, daily_close_time=close_t)
    trades, _ = run_backtest(bars, AlwaysTrend(),
                             TrendFollowingStrategy(fast_period=fast, slow_period=slow, atr_mult=am),
                             MeanReversionStrategy(), FixedSizeRisk(tick_value=POINT_VALUE), cfg)
    return trades


def quarter(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


GRID = [(10, 30), (20, 50), (20, 100), (50, 150), (50, 200)]
AMS = (2.0, 3.0, 4.0)


def main():
    df = load_frame()
    print(f"원자료: 10500000 5분봉 {len(df):,}개 | {df.index[0]} ~ {df.index[-1]}")
    print(f"비용: 슬리피지 {SLIPPAGE_PT}pt(편도) + 수수료 {COMMISSION_KRW:,}원(왕복), 1계약 고정\n")

    best_overall = []
    for tf in (5, 15, 30, 60):
        out = resample(df, tf)
        bars = to_bars(out)
        close_t = last_bar_time(out)
        per_day = len(out) / out.index.normalize().nunique()

        print("=" * 122)
        print(f"[{tf}분봉] {len(bars):,}봉 | 하루 평균 {per_day:.1f}봉 | 강제청산 {close_t}")
        print("=" * 122)
        print("      " + "".join(("am=%.1f" % a).rjust(20) for a in AMS))

        best = None
        for f, sl in GRID:
            if len(bars) < sl + 10:
                continue
            row = ("  %d/%-4d" % (f, sl)).ljust(6)
            for am in AMS:
                st = stats(run(bars, close_t, f, sl, am))
                row += (("PF%.2f(%d)" % (st["pf"], st["trades"])) if st else "none").rjust(20)
                if st and st["trades"] >= 20 and (best is None or st["pf"] > best[0]):
                    best = (st["pf"], f, sl, am, st)
            print(row)

        if best:
            _, f, sl, am, st = best
            print(f"\n  최고: SMA {f}/{sl} · ATR×{am}  →  거래{st['trades']:>4d} 승률{st['win_rate']:6.2f}% "
                  f"PF{st['pf']:6.2f} MDD{st['mdd']:7.2f}% 익{st['avg_win']:+6.2f}/손{st['avg_loss']:+6.2f}pt "
                  f"자본{st['final']:>14,.0f}")
            best_overall.append((tf, f, sl, am, st, bars, close_t))
        print()

    # 시간축별 최고 조합의 분기별 성적
    print("=" * 122)
    print("[분기별 PF] 각 시간축의 최고 조합 — 5분봉 AMATS 전략들은 0/7이었다")
    print("=" * 122)
    hdr = f"  {'분기':10s}{'레인지':>9s}"
    for tf, f, sl, am, *_ in best_overall:
        hdr += f"{('%dm %d/%d' % (tf, f, sl)):>18s}"
    print(hdr)
    print("  " + "-" * 118)

    qmap = {}
    for tf, f, sl, am, st, bars, close_t in best_overall:
        trades = run(bars, close_t, f, sl, am)
        for t in trades:
            qmap.setdefault(quarter(t.entry_time), {}).setdefault(tf, []).append(t)

    rng = df.groupby(df.index.date).apply(lambda g: g["high"].max() - g["low"].min())
    rng.index = pd.to_datetime(rng.index)
    counts = {tf: 0 for tf, *_ in best_overall}
    for q in sorted(qmap):
        y, qq = q.split("-Q")
        m0 = (int(qq) - 1) * 3 + 1
        sel = rng[(rng.index.year == int(y)) & (rng.index.month.isin([m0, m0 + 1, m0 + 2]))]
        line = f"  {q:10s}{np.median(sel):>8.1f}pt"
        for tf, *_ in best_overall:
            st = stats(qmap[q].get(tf, []))
            if st:
                if st["pf"] > 1.0:
                    counts[tf] += 1
                line += f"{('%.2f(%d)' % (st['pf'], st['trades'])):>18s}"
            else:
                line += f"{'없음':>18s}"
        print(line)
    print("  " + "-" * 118)
    tail = f"  {'PF>1 분기':10s}{'':>9s}"
    for tf, *_ in best_overall:
        tail += f"{('%d/%d' % (counts[tf], len(qmap))):>18s}"
    print(tail)


if __name__ == "__main__":
    main()
