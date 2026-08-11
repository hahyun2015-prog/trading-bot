"""2026-08-11 최종 배포본 백테스트 — 오늘 바뀐 것이 모두 반영된 상태.

오늘 라이브에 들어간 변경 중 백테스트로 잴 수 있는 것:
  · 이익보전·하드손절 해제        (SAR 경로에는 원래 무관 — chandelier 전용)
  · 이평선 방향필터 MA200          신규
  · sar_af_max 0.20 → 0.10        신규
  · std_error 게이트 실제 작동      P1 수정 전에는 SAR에서 항상 차단됐다
  · 서킷브레이커 전 전략 적용        연속 5회. 백테스터는 원래 이 조건을 적용하고 있었으므로
                                   이 변경은 라이브를 백테스트에 맞춘 것이다
  · 일일 손실 한도 3%              신규. 백테스터에 daily_loss_limit_pt로 이식
                                   (1계약 백테스트라 원화 한도가 안 걸려 15계약 환산 pt로 변환)

백테스트로 못 재는 것(신뢰성 항목이라 성적에 안 나타난다):
  · 롤오버 가드, 진입 락 타임아웃, SAR 상태 영속화, 조용한 차단 경보

비교 기준은 "오늘 아침 상태"(이평선·af 변경 전)로 잡아, 오늘 작업의 순효과를 본다.
"""
import sys

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.path.insert(0, r"c:\Antigravity\AI_T_Agent\scratch")
sys.stdout.reconfigure(encoding="utf-8")

import json
import numpy as np
from bqa.kalman_backtester import load_futures_data
from backtest_sar_bb_20260809 import run_sar_or_bb_replica

CFG = json.load(open(r"c:\Antigravity\AI_T_Agent\config\config.json", encoding="utf-8"))["futures_settings"]
try:
    CFG.update(json.load(open(r"c:\Antigravity\AI_T_Agent\config\config_local.json",
                              encoding="utf-8")).get("futures_settings", {}))
except Exception:
    pass

POINT_VALUE = 50_000
MAX_CONTRACTS = int(CFG.get("max_contracts", 15))
DEPOSIT = 1_055_915_750           # 2026-08-11 실측 예수금
LIMIT_PT = DEPOSIT * float(CFG.get("daily_loss_limit_pct", 0.03)) / (MAX_CONTRACTS * POINT_VALUE)

BASE = dict(
    strategy="sar",
    Q=CFG["kf_q"], R=CFG["kf_r"], mult=CFG["kf_mult"],
    reentry_k=CFG["reentry_k"], point_value=POINT_VALUE,
    trim_std_outliers=CFG.get("std_trim_outliers", 1),
    atr_cutoff=float(CFG.get("atr_cutoff", 15.0)),
    min_std_error_entry=float(CFG.get("min_std_error_entry", 1.5)),
    consecutive_loss_limit=int(CFG.get("consecutive_loss_limit", 5)),
    entry_end_hour=CFG.get("entry_end_hour"), entry_end_minute=CFG.get("entry_end_minute", 0),
    entry_target_mode="breakout", breakout_k=float(CFG.get("best_k", 0.2)),
    realistic_gap_fill=True, commission_rate=0.00003,
    slip_entry_pt=0.25, slip_exit_sl_pt=0.25, slip_exit_normal_pt=0.25, slip_exit_force_pt=0.25,
)

VARIANTS = [
    ("① 오늘 아침 (이평·af 변경 전)", dict(ma_filter_period=None, sar_af_max=0.20, daily_loss_limit_pt=None)),
    ("② +이평200",                  dict(ma_filter_period=200, sar_af_max=0.20, daily_loss_limit_pt=None)),
    ("③ +af 0.10",                  dict(ma_filter_period=200, sar_af_max=0.10, daily_loss_limit_pt=None)),
    ("④ +일일한도 (현 배포본)",       dict(ma_filter_period=200, sar_af_max=0.10, daily_loss_limit_pt=LIMIT_PT)),
]


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
                worst=p.min(), avg_w=w.mean() if len(w) else 0, avg_l=l.mean() if len(l) else 0)


def main():
    df = load_futures_data("10500000", table="futures_ohlcv")
    days = sorted(df["date_day"].unique())
    print(f"자료 10500000 {len(df):,}봉 / {len(days)}거래일 | {days[0]} ~ {days[-1]}")
    print(f"설정 atr_cutoff={BASE['atr_cutoff']} std_error>={BASE['min_std_error_entry']} "
          f"연속손절={BASE['consecutive_loss_limit']}회 진입종료={BASE['entry_end_hour']}시")
    print(f"일일한도 예수금 {DEPOSIT:,}원 × {CFG.get('daily_loss_limit_pct')} "
          f"= {DEPOSIT*float(CFG.get('daily_loss_limit_pct',0.03)):,.0f}원 "
          f"→ {MAX_CONTRACTS}계약 환산 {LIMIT_PT:.1f}pt")
    print(f"비용 슬리피지 편도 0.25pt + 수수료 0.0030% | 1계약 고정")

    print(f"\n{'=' * 118}")
    print("[누적 효과] 오늘 변경을 하나씩 얹으면")
    print("=" * 118)
    print(f"  {'구성':26s}{'거래':>6s}{'승률':>9s}{'PF':>8s}{'MDD(pt)':>10s}{'손익(pt)':>11s}{'최악':>9s}   분기별 PF")
    print("  " + "-" * 114)
    store = {}
    for name, over in VARIANTS:
        tr = run_sar_or_bb_replica(df.copy(), **BASE, **over, return_trades=True).get("trade_log", [])
        store[name] = tr
        s = stat(tr)
        qs = {}
        for t in tr:
            qs.setdefault(q_of(t["entry_time"]), []).append(t)
        detail = " ".join(f"{q[2:]}:{stat(qs[q])['pf']:.2f}" for q in sorted(qs))
        print(f"  {name:26s}{s['n']:>6d}{s['wr']:>8.2f}%{s['pf']:>8.2f}{s['mdd']:>10.1f}"
              f"{s['pts']:>+11.1f}{s['worst']:>+9.1f}   {detail}")

    final = store[VARIANTS[-1][0]]
    s = stat(final)
    print(f"\n{'=' * 118}")
    print("[현 배포본 상세]")
    print("=" * 118)
    print(f"  거래 {s['n']}건 | 승률 {s['wr']:.2f}% | PF {s['pf']:.2f}")
    print(f"  평균익 {s['avg_w']:+.2f}pt / 평균손 {s['avg_l']:+.2f}pt (손익비 {abs(s['avg_w']/s['avg_l']):.2f})")
    print(f"  최악 단일손실 {s['worst']:+.1f}pt → {MAX_CONTRACTS}계약 {s['worst']*MAX_CONTRACTS*POINT_VALUE:,.0f}원")
    print(f"  최대낙폭 {s['mdd']:.1f}pt → {MAX_CONTRACTS}계약 {s['mdd']*MAX_CONTRACTS*POINT_VALUE:,.0f}원")

    # 일일 한도가 실제로 몇 번 걸렸나
    byday = {}
    for t in final:
        byday.setdefault(t["entry_time"].date(), []).append(t["pnl_pt"])
    hit = [(d, sum(v)) for d, v in byday.items() if sum(x for x in v if x < 0) <= -LIMIT_PT]
    print(f"\n  일일 손실이 한도({LIMIT_PT:.1f}pt)를 넘은 날: {len(hit)}일 / 거래일 {len(byday)}일")
    for d, tot in sorted(hit, key=lambda x: x[1])[:5]:
        print(f"    {d}  당일 합계 {tot:+.1f}pt")

    # 연간 환산
    yrs = len(days) / 250
    print(f"\n  {MAX_CONTRACTS}계약 환산 누적손익 {s['pts']*MAX_CONTRACTS*POINT_VALUE:,.0f}원 "
          f"({yrs:.1f}년) → 연 {s['pts']*MAX_CONTRACTS*POINT_VALUE/yrs:,.0f}원")
    print(f"  예수금 {DEPOSIT:,}원 대비 연 수익률 {s['pts']*MAX_CONTRACTS*POINT_VALUE/yrs/DEPOSIT*100:+.2f}%")


if __name__ == "__main__":
    main()
