"""현재 적용 중인 매매시스템 백테스트 (2026-08-13 기준).

08-11판(live_config_backtest_20260811.py)이 낡아 새로 만든다. 그 뒤로 바뀐 것:
  · 계좌      모의(예수금 10.5억, 15계약) → 실계좌(1,495만원, 1계약)
  · 일일한도  3% → 10%
  · 비용      슬리피지 가정 0.25pt → 실계좌 실측 0.080pt (수수료도 0.003% 실측)
  · SAR       틱마다 전진 → 5분봉당 1회(sar_update_mode=bar)

마지막 항목이 특히 중요하다. 백테스터는 처음부터 봉 단위로 SAR을 전진시켰는데
라이브는 틱마다 전진시켜 트레일링이 과도하게 조여졌다(라이브 22건/일 vs 백테 1.4건/일).
bar 모드로 바꾼 지금에서야 **백테스트와 라이브가 같은 가정 위에 선다.**

라이브에서 SAR에 적용되지 않는 것은 백테스트에서도 끈다 — 코드로 확인했다:
  · regime_filter  era_order_manager.py 5027/5112행에 is_chandelier 게이트
  · time_stop      1999행 "샹들리에 주간 전용"
  · sl_hard_cap_pt 설정은 10pt인데 라이브 실측 손절이 71.97pt → SAR 경로 미적용

백테스터가 재현하지 못하는 라이브 설정(성적이 실제보다 좋게 나올 수 있는 방향):
  · max_trades_day=10   일일 거래수 상한. 백테스터에 없다.
  · global_cooldown_sec / reentry_cooldown_sec  방향무관 90초·동일방향 301초 쿨다운
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import json
from collections import Counter

import numpy as np

from bqa.kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

CFG = json.load(open(r"c:\Antigravity\AI_T_Agent\config\config.json", encoding="utf-8"))["futures_settings"]
CFG.update(json.load(open(r"c:\Antigravity\AI_T_Agent\config\config_local.json",
                          encoding="utf-8")).get("futures_settings", {}))
ACT = json.load(open(r"c:\Antigravity\AI_T_Agent\config\active_strategy.json", encoding="utf-8"))

POINT_VALUE = 50_000                 # 미니 승수
DEPOSIT = 14_953_780                 # 2026-08-13 실계좌 예수금
QTY = 1                              # 30% 캡으로 산정되는 계약수
LIMIT_PT = DEPOSIT * float(CFG.get("daily_loss_limit_pct", 0.10)) / (QTY * POINT_VALUE)

SLIP_LIVE = 0.080                    # 실계좌 실측(편도), 표본 10체결
SLIP_CONS = 0.250                    # 종전 보수 가정

BASE = dict(
    strategy="sar",
    Q=CFG["kf_q"], R=CFG["kf_r"], mult=CFG["kf_mult"],
    reentry_k=CFG["reentry_k"], point_value=POINT_VALUE,
    trim_std_outliers=CFG.get("std_trim_outliers", 1),
    atr_cutoff=float(CFG.get("atr_cutoff", 15.0)),
    min_std_error_entry=float(CFG.get("min_std_error_entry", 1.5)),
    consecutive_loss_limit=int(CFG.get("consecutive_loss_limit", 5)),
    entry_end_hour=CFG.get("entry_end_hour", 15),
    entry_end_minute=CFG.get("entry_end_minute", 0),
    entry_target_mode="breakout", breakout_k=float(ACT.get("best_k", 0.2)),
    ma_filter_period=int(CFG.get("ma_filter_period", 200)),
    sar_af_max=float(CFG.get("sar_af_max", 0.10)),
    daily_loss_limit_pt=LIMIT_PT,
    realistic_gap_fill=True,
    commission_rate=0.00003,
    # 라이브에서 SAR에 안 걸리는 것들 — 위 주석 참조
    regime_filter_enabled=False, time_stop_enabled=False, sl_hard_cap_pt=None,
)


def q_of(ts):
    return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def stat(tr):
    if not tr:
        return None
    p = np.array([t["pnl_pt"] for t in tr])
    w, l = p[p > 0], p[p <= 0]
    gl = -l.sum()
    eq = np.concatenate([[0.0], np.cumsum(p)])
    return dict(n=len(p), wr=len(w) / len(p) * 100,
                pf=(w.sum() / gl) if gl > 0 else float("inf"),
                pts=p.sum(), mdd=(np.maximum.accumulate(eq) - eq).max(),
                worst=p.min(), best=p.max(),
                avg_w=w.mean() if len(w) else 0, avg_l=l.mean() if len(l) else 0)


def run(df, slip):
    kw = dict(BASE)
    kw.update(slip_entry_pt=slip, slip_exit_sl_pt=slip,
              slip_exit_normal_pt=slip, slip_exit_force_pt=slip)
    return run_sar_or_bb_replica(df.copy(), **kw, return_trades=True).get("trade_log", [])


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    days = sorted(df["date_day"].unique())
    print(f"자료 10500000 | {len(df):,}봉 | {len(days)}거래일 | {days[0]} ~ {days[-1]}")
    print(f"구성 SAR breakout K={BASE['breakout_k']} | MA{BASE['ma_filter_period']} 방향필터 | "
          f"af_max={BASE['sar_af_max']} | atr_cutoff={BASE['atr_cutoff']} | "
          f"std_error>={BASE['min_std_error_entry']}")
    print(f"     연속손절 {BASE['consecutive_loss_limit']}회 | 진입종료 {BASE['entry_end_hour']}시 | "
          f"SAR 봉단위 전진(라이브 sar_update_mode=bar와 동일)")
    print(f"계좌 예수금 {DEPOSIT:,}원 | {QTY}계약 | 일일한도 {CFG.get('daily_loss_limit_pct')} "
          f"= {DEPOSIT*float(CFG.get('daily_loss_limit_pct',0.1)):,.0f}원 = {LIMIT_PT:.1f}pt")

    print(f"\n{'=' * 104}")
    print("[비용 가정별 성적] — 실측이 기준, 보수 가정은 하방 확인용")
    print("=" * 104)
    print(f"  {'편도 슬리피지':22s}{'거래':>6s}{'승률':>9s}{'PF':>8s}{'손익(pt)':>11s}{'MDD(pt)':>10s}{'최악':>9s}")
    print("  " + "-" * 100)
    store = {}
    for slip, lab in ((SLIP_LIVE, f"{SLIP_LIVE:.3f}pt 실계좌 실측"),
                      (SLIP_CONS, f"{SLIP_CONS:.3f}pt 보수 가정")):
        tr = run(df, slip)
        store[slip] = tr
        s = stat(tr)
        print(f"  {lab:22s}{s['n']:>6d}{s['wr']:>8.2f}%{s['pf']:>8.2f}"
              f"{s['pts']:>+11.1f}{s['mdd']:>10.1f}{s['worst']:>+9.1f}")

    tr = store[SLIP_LIVE]
    s = stat(tr)

    print(f"\n{'=' * 104}")
    print("[현 배포본 상세] 실측 비용 기준")
    print("=" * 104)
    print(f"  거래 {s['n']}건 | 승률 {s['wr']:.2f}% | PF {s['pf']:.2f}")
    print(f"  평균익 {s['avg_w']:+.2f}pt / 평균손 {s['avg_l']:+.2f}pt "
          f"(손익비 {abs(s['avg_w']/s['avg_l']):.2f})")
    print(f"  최대이익 {s['best']:+.1f}pt / 최악손실 {s['worst']:+.1f}pt "
          f"→ {QTY}계약 {s['worst']*QTY*POINT_VALUE:,.0f}원")
    print(f"  최대낙폭 {s['mdd']:.1f}pt → {QTY}계약 {s['mdd']*QTY*POINT_VALUE:,.0f}원 "
          f"(예수금의 {s['mdd']*QTY*POINT_VALUE/DEPOSIT*100:.1f}%)")

    byday = {}
    for t in tr:
        byday.setdefault(t["entry_time"].date(), []).append(t["pnl_pt"])
    cnt = Counter(len(v) for v in byday.values())
    print(f"\n  거래일 {len(byday)}일 / 전체 {len(days)}일 (거래 없는 날 {len(days)-len(byday)}일)")
    print(f"  하루 평균 {s['n']/len(days):.2f}건 | 거래한 날만 보면 {s['n']/len(byday):.2f}건")
    print(f"  하루 거래수 분포: " + " ".join(f"{k}건×{v}일" for k, v in sorted(cnt.items())))
    over = sum(v for k, v in cnt.items() if k > int(CFG.get("max_trades_day", 10)))
    print(f"  라이브 상한(max_trades_day={CFG.get('max_trades_day')}) 초과일: {over}일")

    hit = [(d, sum(v)) for d, v in byday.items() if sum(x for x in v if x < 0) <= -LIMIT_PT]
    print(f"  일일한도({LIMIT_PT:.1f}pt) 도달일: {len(hit)}일")
    for d, tot in sorted(hit, key=lambda x: x[1])[:5]:
        print(f"    {d}  당일 {tot:+.1f}pt")

    print(f"\n{'=' * 104}")
    print("[분기별]")
    print("=" * 104)
    qs = {}
    for t in tr:
        qs.setdefault(q_of(t["entry_time"]), []).append(t)
    print(f"  {'분기':10s}{'거래':>6s}{'승률':>9s}{'PF':>8s}{'손익(pt)':>11s}{'원화(1계약)':>16s}")
    print("  " + "-" * 100)
    for q in sorted(qs):
        st = stat(qs[q])
        print(f"  {q:10s}{st['n']:>6d}{st['wr']:>8.2f}%{st['pf']:>8.2f}{st['pts']:>+11.1f}"
              f"{st['pts']*QTY*POINT_VALUE:>+15,.0f}원")
    good = sum(1 for q in qs if stat(qs[q])["pf"] > 1.0)
    print(f"  → PF>1 분기 {good}/{len(qs)}")

    print(f"\n{'=' * 104}")
    print("[수익 환산]")
    print("=" * 104)
    yrs = len(days) / 250
    won = s["pts"] * QTY * POINT_VALUE
    print(f"  누적 {s['pts']:+.1f}pt = {won:+,.0f}원 ({yrs:.1f}년)")
    print(f"  연 환산 {won/yrs:+,.0f}원 → 예수금 {DEPOSIT:,}원 대비 {won/yrs/DEPOSIT*100:+.2f}%")
    print(f"  ※ 1계약 고정. 계약수를 늘리면 비례하나 증거금({DEPOSIT*0.30/9_870_800:.1f}계약분 가용)이 상한.")


if __name__ == "__main__":
    main()
