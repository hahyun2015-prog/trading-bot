"""해외선물 봇의 레짐 전환 전략을 코스피200 미니선물에 이식해 평가한다 (2026-08-11).

이식 대상: c:\\Antigravity\\해외주식 의 futures_bot
    RegimeDetector   ADX(14) > 30 → TREND, < 25 → RANGE (히스테리시스 25~30)
    TrendFollowing   SMA(20)/SMA(50) 교차 진입, ATR(14)×2.5 트레일링 스탑
    MeanReversion    RSI(21) <30 롱 / >70 숏, 스탑=볼린저(30,2.0) 밴드, 익절=중심선
    RiskManager      1회 위험 1%, 사이즈 = 위험액 / 스탑거리

AMATS와의 근본적 차이:
    AMATS는 국면과 무관하게 한 전략(칼만밴드 돌파 + 트레일링)만 돌린다.
    해외 봇은 ADX로 국면을 판정해 **추세추종과 평균회귀를 갈아끼운다.**

    이게 중요한 이유는 2026-08-11 분기별 분해 결과에 있다. 2025-Q1~Q3은 일중 레인지가
    4~6pt인 횡보 국면이었고, 그 구간에서 AMATS의 모든 조합이 PF 0.02~0.05로 무너졌다.
    추세추종만 돌리니 당연한 결과다. 그 구간에 평균회귀를 넣으면 달라지는지가 핵심 질문이다.

체결 판정:
    해외 엔진은 스탑을 갭으로 지나치면 min(stop, bar.open)으로 체결한다 — AMATS가
    realistic_gap_fill=True로 뒤늦게 고친 것과 같은 처리가 처음부터 들어 있다.
    진입도 신호 발생 봉의 종가 체결이라 유령체결 여지가 없다.

비교 가능성을 위한 조정:
    - 사이즈는 1계약 고정. PF·승률은 사이즈에 무관하므로 신호 품질만 본다.
      (해외 봇의 max_net_exposure_pct=5%는 명목가 기준이라 이 계좌에서는 항상 0계약이
       되어 그대로 쓸 수 없다 — 아래 6절 참조)
    - 비용은 AMATS 실측치로 맞춘다: 슬리피지 1.0pt(왕복 2.0pt 상당),
      수수료 왕복 3,000원/계약 (0.0030% × 1,000pt × 50,000 × 2)
    - 장마감 강제청산 15:35 (AMATS와 동일)
"""
import sys
import sqlite3
from datetime import datetime, time

sys.path.insert(0, r"c:\Antigravity\해외주식\src")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from futures_bot.types import Bar, Direction
from futures_bot.regime import Regime, RegimeDetector
from futures_bot.strategies.trend_following import TrendFollowingStrategy
from futures_bot.strategies.mean_reversion import MeanReversionStrategy
from futures_bot.risk import RiskManager
from futures_bot.backtest.engine import run_backtest, BacktestConfig

DB = r"c:\Antigravity\AI_T_Agent\futures_data.db"
POINT_VALUE = 50_000          # 코스피200 미니선물 1pt
SLIPPAGE_PT = 1.0
COMMISSION_KRW = 3_000        # 왕복


class FixedSizeRisk(RiskManager):
    """사이즈만 1계약으로 고정. 나머지(연속손절 쿨다운·일일손실한도)는 원본 그대로 둔다.

    원본의 max_net_exposure_pct는 `equity * 5% / (price * tick_value)`로 계약수를 내는데,
    이는 증거금이 아니라 **명목가** 기준이다. 코스피200 미니 1계약의 명목가는
    1,000pt × 50,000 = 5,000만원이라, 5,000만 계좌에서는 5%(250만원)로 1계약도 못 든다.
    해외 NQ에서도 같은 문제가 생긴다(명목가 4만달러 vs 10만달러 계좌의 5% = 5천달러).
    신호 품질만 보려는 목적이므로 여기서는 1계약 고정으로 우회한다.
    """

    def evaluate(self, signal, equity, current_net_size):
        d = super().evaluate(signal, equity, current_net_size)
        if d.reason in ("cooldown_active", "daily_loss_limit_reached", "invalid_stop_distance"):
            return d
        from futures_bot.types import RiskDecision
        return RiskDecision(True, 1, signal.stop_price, "fixed_size_1")


def load_bars(code="10500000"):
    rows = sqlite3.connect(DB).execute(
        "SELECT date, open, high, low, close, volume FROM futures_ohlcv WHERE code=? ORDER BY date",
        (code,)).fetchall()
    bars = []
    for d, o, h, l, c, v in rows:
        bars.append(Bar(datetime.strptime(d, "%Y%m%d%H%M%S"), o, h, l, c, v or 0))
    return bars


def stats(trades, start_equity):
    if not trades:
        return None
    pnls = np.array([t.pnl for t in trades])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    eq = start_equity + np.cumsum(pnls)
    peaks = np.maximum.accumulate(np.concatenate([[start_equity], eq]))
    mdd = ((peaks - np.concatenate([[start_equity], eq])) / peaks * 100).max()
    return dict(
        trades=len(trades),
        win_rate=len(wins) / len(trades) * 100,
        pf=(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        mdd=mdd,
        final=start_equity + pnls.sum(),
        avg_win_pt=(wins.mean() / POINT_VALUE) if len(wins) else 0.0,
        avg_loss_pt=(losses.mean() / POINT_VALUE) if len(losses) else 0.0,
    )


def run(bars, start_equity=50_000_000.0, **over):
    cfg = BacktestConfig(
        starting_equity=start_equity,
        tick_value=POINT_VALUE,
        slippage=SLIPPAGE_PT,
        commission_per_contract=COMMISSION_KRW,
        adx_period=14,
        daily_close_time=time(15, 35),
    )
    trades, _ = run_backtest(
        bars,
        RegimeDetector(trend_threshold=over.get("trend_th", 30.0),
                       range_threshold=over.get("range_th", 25.0)),
        TrendFollowingStrategy(fast_period=over.get("fast", 20), slow_period=over.get("slow", 50),
                               atr_mult=over.get("atr_mult", 2.5)),
        MeanReversionStrategy(rsi_period=over.get("rsi", 21), bb_period=over.get("bb", 30)),
        FixedSizeRisk(tick_value=POINT_VALUE),
        cfg,
    )
    return trades


def line(label, s):
    if s is None:
        return f"  {label:26s} 거래 없음"
    return (f"  {label:26s} 거래{s['trades']:>5d} 승률{s['win_rate']:6.2f}% PF{s['pf']:7.2f} "
            f"MDD{s['mdd']:7.2f}% 익{s['avg_win_pt']:+6.2f}/손{s['avg_loss_pt']:+6.2f}pt "
            f"자본{s['final']:>15,.0f}")


def quarter(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def main():
    bars = load_bars()
    print(f"자료: 10500000 {len(bars):,}봉 | {bars[0].timestamp} ~ {bars[-1].timestamp}")
    print(f"비용: 슬리피지 {SLIPPAGE_PT}pt(편도) + 수수료 {COMMISSION_KRW:,}원(왕복) | 1계약 고정")
    print(f"강제청산 15:35 | 초기자본 5,000만")

    print(f"\n{'=' * 124}")
    print("[전체기간]")
    print("=" * 124)
    trades = run(bars)
    print(line("해외봇 레짐전환 (원설정)", stats(trades, 50_000_000.0)))

    by_strat = {}
    for t in trades:
        by_strat.setdefault(t.strategy_name, []).append(t)
    for name, ts_ in sorted(by_strat.items()):
        print(line(f"  └ {name}", stats(ts_, 50_000_000.0)))

    print(f"\n{'=' * 124}")
    print("[분기별] — AMATS는 7개 분기 모두 PF < 1.0 이었다")
    print("=" * 124)
    print(f"  {'분기':10s}{'거래':>6s}{'승률':>9s}{'PF':>8s}   {'추세추종':>22s}   {'평균회귀':>22s}")
    print("  " + "-" * 118)
    qs = sorted({quarter(t.entry_time) for t in trades})
    above = 0
    for q in qs:
        sel = [t for t in trades if quarter(t.entry_time) == q]
        s = stats(sel, 50_000_000.0)
        tf = stats([t for t in sel if t.strategy_name == "trend_following"], 50_000_000.0)
        mr = stats([t for t in sel if t.strategy_name == "mean_reversion"], 50_000_000.0)
        if s and s["pf"] > 1.0:
            above += 1
        f = lambda x: f"PF{x['pf']:6.2f}({x['trades']:>4d})" if x else "        없음"
        print(f"  {q:10s}{s['trades']:>6d}{s['win_rate']:>8.2f}%{s['pf']:>8.2f}   "
              f"{f(tf):>22s}   {f(mr):>22s}")
    print("  " + "-" * 118)
    print(f"  PF > 1.0 인 분기: {above}/{len(qs)}")


if __name__ == "__main__":
    main()
