"""변동성 게이트 — "레인지가 얇으면 아예 쉰다"가 성립하는가 (2026-08-11).

배경:
    2026-08-11까지의 검증에서 반복 확인된 것:
      · 유령체결을 걷어내면 어떤 전략도 2025년(일중 레인지 4~6pt)을 넘지 못한다
      · 5/15/30/60분봉 어디로 잘라도 분기별 PF>1은 정확히 2/7이고, 넘긴 분기는
        예외 없이 2026-Q1 이후(레인지 22pt 이상)다
      · 시간축 확대는 거래 수만 줄일 뿐 승률을 올리지 못했다

    남는 가설은 하나다 — 전략을 바꾸는 게 아니라 **못 버는 국면에 아예 안 들어가는 것**.

임계값을 후행으로 고르지 않기 위해:
    결과를 보고 고르면 2025년을 알고 자르는 셈이라 의미가 없다. 비용에서 유도한다.

      왕복 비용 = 슬리피지 1.0pt × 2 + 수수료 3,000원(= 0.06pt 상당) ≈ 2.06pt

    추세추종이 잡는 폭은 대략 ATR 규모다. "비용이 기대 이익의 N분의 1 이하일 때만
    진입한다"를 규칙으로 두면 문턱은 ATR >= N × 2.06pt가 된다.
      N=5  → 10.3pt    비용이 기대폭의 20%
      N=10 → 20.6pt    비용이 기대폭의 10%
      N=15 → 30.9pt    비용이 기대폭의 6.7%
    참고로 2025년 일중 레인지 중앙값은 4.4~6.0pt, 2026-Q1부터 22pt 이상이다.

    ATR(14)은 과거 봉만 쓰므로 미래참조가 없다. 실거래에 그대로 옮길 수 있다.
"""
import sys
import sqlite3
from collections import Counter
from dataclasses import dataclass

sys.path.insert(0, r"c:\Antigravity\해외주식\src")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from futures_bot.types import Bar
from futures_bot.regime import Regime
from futures_bot.incremental import IncrementalATR, _BarWindowCache
from futures_bot.strategies.trend_following import TrendFollowingStrategy
from futures_bot.strategies.mean_reversion import MeanReversionStrategy
from futures_bot.risk import RiskManager, RiskDecision
from futures_bot.backtest.engine import run_backtest, BacktestConfig

DB = r"c:\Antigravity\AI_T_Agent\futures_data.db"
POINT_VALUE = 50_000
SLIPPAGE_PT = 1.0
COMMISSION_KRW = 3_000
START_EQUITY = 50_000_000.0
ROUND_TRIP_PT = SLIPPAGE_PT * 2 + COMMISSION_KRW / POINT_VALUE


class FixedSizeRisk(RiskManager):
    def evaluate(self, signal, equity, current_net_size):
        d = super().evaluate(signal, equity, current_net_size)
        if d.reason in ("cooldown_active", "daily_loss_limit_reached", "invalid_stop_distance"):
            return d
        return RiskDecision(True, 1, signal.stop_price, "fixed_size_1")


class AlwaysTrend:
    def update(self, adx):
        return Regime.TREND


@dataclass
class VolGated:
    """진입만 변동성으로 막는다. 청산·트레일링은 원본 그대로 통과시킨다.

    보유 중 변동성이 떨어져도 청산을 막지 않는다 — 게이트는 '새로 들어가지 않는다'는
    뜻이지 '못 나온다'가 아니다.
    """
    inner: object
    min_atr: float
    atr_period: int = 14

    def __post_init__(self):
        self.name = self.inner.name
        self._cache = _BarWindowCache({"atr": IncrementalATR(self.atr_period)})
        self.blocked = 0
        self.passed = 0

    def entry_signal(self, bars):
        self._cache.sync(bars)
        atr = self._cache.trackers["atr"].value
        if atr is None or atr < self.min_atr:
            sig = self.inner.entry_signal(bars)
            if sig is not None:
                self.blocked += 1
            return None
        sig = self.inner.entry_signal(bars)
        if sig is not None:
            self.passed += 1
        return sig

    def should_exit(self, bars, position):
        return self.inner.should_exit(bars, position)

    def update_stop(self, bars, position):
        return self.inner.update_stop(bars, position)


def load_frame(code="10500000"):
    rows = sqlite3.connect(DB).execute(
        "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code=? ORDER BY date",
        (code,)).fetchall()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["dt"] = pd.to_datetime(df["date"], format="%Y%m%d%H%M%S")
    return df.set_index("dt")


def resample(df, minutes):
    if minutes == 5:
        return df[["open", "high", "low", "close", "volume"]].copy()
    return df.resample(f"{minutes}min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "close"])


def to_bars(out):
    return [Bar(ts.to_pydatetime(), r.open, r.high, r.low, r.close, r.volume or 0)
            for ts, r in out.iterrows()]


def last_bar_time(out):
    times = out.groupby(out.index.date).apply(lambda g: g.index[-1].time())
    return Counter(times).most_common(1)[0][0]


def stats(trades):
    if not trades:
        return None
    p = np.array([t.pnl for t in trades])
    w, l = p[p > 0], p[p <= 0]
    gl = -l.sum()
    eq = np.concatenate([[START_EQUITY], START_EQUITY + np.cumsum(p)])
    mdd = ((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq) * 100).max()
    return dict(trades=len(trades), win_rate=len(w) / len(trades) * 100,
                pf=(w.sum() / gl) if gl > 0 else float("inf"), mdd=mdd,
                final=START_EQUITY + p.sum(),
                avg_win=(w.mean() / POINT_VALUE) if len(w) else 0.0,
                avg_loss=(l.mean() / POINT_VALUE) if len(l) else 0.0)


def run(bars, close_t, fast, slow, am, min_atr):
    tf_strat = TrendFollowingStrategy(fast_period=fast, slow_period=slow, atr_mult=am)
    gated = VolGated(tf_strat, min_atr) if min_atr > 0 else tf_strat
    cfg = BacktestConfig(starting_equity=START_EQUITY, tick_value=POINT_VALUE,
                         slippage=SLIPPAGE_PT, commission_per_contract=COMMISSION_KRW,
                         adx_period=14, daily_close_time=close_t)
    trades, _ = run_backtest(bars, AlwaysTrend(), gated, MeanReversionStrategy(),
                             FixedSizeRisk(tick_value=POINT_VALUE), cfg)
    return trades


def quarter(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


CANDIDATES = [(5, 50, 150, 3.0), (30, 50, 150, 4.0)]
GATES = [0.0, 5.0, 10.3, 15.0, 20.6, 30.9, 40.0]


def main():
    df = load_frame()
    print(f"왕복 비용 {ROUND_TRIP_PT:.2f}pt (슬리피지 {SLIPPAGE_PT}×2 + 수수료 {COMMISSION_KRW:,}원)")
    print(f"게이트 후보는 비용의 배수로 잡는다: 5배={5*ROUND_TRIP_PT:.1f} 10배={10*ROUND_TRIP_PT:.1f} 15배={15*ROUND_TRIP_PT:.1f}pt\n")

    for tf, fast, slow, am in CANDIDATES:
        out = resample(df, tf)
        bars = to_bars(out)
        close_t = last_bar_time(out)
        print("=" * 126)
        print(f"[{tf}분봉 SMA {fast}/{slow} · ATR×{am}] 변동성 게이트 스윕")
        print("=" * 126)
        print(f"  {'게이트':>10s}{'거래':>7s}{'승률':>9s}{'PF':>8s}{'MDD':>9s}"
              f"{'평균익':>9s}{'평균손':>9s}{'최종자본':>16s}{'거래일수':>10s}")
        print("  " + "-" * 120)
        for g in GATES:
            trades = run(bars, close_t, fast, slow, am, g)
            st = stats(trades)
            if st is None:
                print(f"  {g:>9.1f}pt {'거래 없음':>20s}")
                continue
            tdays = len({t.entry_time.date() for t in trades})
            label = f"{g:.1f}pt" if g > 0 else "없음"
            print(f"  {label:>10s}{st['trades']:>7d}{st['win_rate']:>8.2f}%{st['pf']:>8.2f}"
                  f"{st['mdd']:>8.2f}%{st['avg_win']:>+8.2f}{st['avg_loss']:>+9.2f}"
                  f"{st['final']:>16,.0f}{tdays:>10d}")
        print()

    # 비용 10배(20.6pt) 게이트를 적용했을 때 분기별로 무엇이 남는가
    print("=" * 126)
    print("[분기별] 게이트 20.6pt(비용 10배) 적용 — 무엇이 남고 무엇이 잘리는가")
    print("=" * 126)
    rng = df.groupby(df.index.date).apply(lambda g: g["high"].max() - g["low"].min())
    rng.index = pd.to_datetime(rng.index)

    for tf, fast, slow, am in CANDIDATES:
        out = resample(df, tf)
        bars, close_t = to_bars(out), last_bar_time(out)
        base = run(bars, close_t, fast, slow, am, 0.0)
        gated = run(bars, close_t, fast, slow, am, 20.6)
        qb, qg = {}, {}
        for t in base:
            qb.setdefault(quarter(t.entry_time), []).append(t)
        for t in gated:
            qg.setdefault(quarter(t.entry_time), []).append(t)
        print(f"\n  ▶ {tf}분봉 SMA {fast}/{slow}")
        print(f"    {'분기':10s}{'레인지':>9s}{'게이트없음':>18s}{'게이트20.6pt':>18s}")
        cnt_b = cnt_g = 0
        for q in sorted(qb):
            y, qq = q.split("-Q")
            m0 = (int(qq) - 1) * 3 + 1
            sel = rng[(rng.index.year == int(y)) & (rng.index.month.isin([m0, m0 + 1, m0 + 2]))]
            sb, sg = stats(qb.get(q, [])), stats(qg.get(q, []))
            if sb and sb["pf"] > 1:
                cnt_b += 1
            if sg and sg["pf"] > 1:
                cnt_g += 1
            f = lambda s: f"{s['pf']:.2f}({s['trades']})" if s else "거래없음"
            print(f"    {q:10s}{np.median(sel):>8.1f}pt{f(sb):>18s}{f(sg):>18s}")
        tot_b, tot_g = stats(base), stats(gated)
        print(f"    {'PF>1 분기':10s}{'':>9s}{f'{cnt_b}/{len(qb)}':>18s}{f'{cnt_g}/{len(qg)}':>18s}")
        print(f"    {'전체':10s}{'':>9s}{f'{tot_b[chr(112)+chr(102)]:.2f}({tot_b[chr(116)+chr(114)+chr(97)+chr(100)+chr(101)+chr(115)]})':>18s}"
              f"{f'{tot_g[chr(112)+chr(102)]:.2f}({tot_g[chr(116)+chr(114)+chr(97)+chr(100)+chr(101)+chr(115)]})':>18s}")


if __name__ == "__main__":
    main()
