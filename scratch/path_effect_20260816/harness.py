# -*- coding: utf-8 -*-
"""청산 로직 연구 v2 하네스 — 확정 라이브 기준선(2026-08-14 PF불일치 보고서 S4).
측정 전용. 리포 내 파일 무수정. bt_exit.py는 scratch 원본의 사본이다."""
import os, sys, json, sqlite3
import numpy as np, pandas as pd

ROOT = r'c:\Antigravity\AI_T_Agent'
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, os.path.join(ROOT, 'bqa'), os.path.join(ROOT, 'scratch'), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from kalman_backtester import clean_ohlcv_outliers          # noqa: E402
import indicators as I                                       # noqa: E402
from bt_exit import run_sar_or_bb_replica                    # noqa: E402

POINT_VALUE = 50_000
DEPOSIT = 23_967_550          # 정정된 예수금
SLIP = 0.103                  # 실측 편도 (확정 기준선)

def load_df(code='10500000', table='futures_ohlcv'):
    conn = sqlite3.connect(os.path.join(ROOT, 'futures_data.db'))
    df = pd.read_sql_query(f"SELECT date,open,high,low,close,volume FROM {table} "
                           f"WHERE code='{code}' ORDER BY date ASC", conn)
    conn.close()
    df = clean_ohlcv_outliers(df.reset_index(drop=True))
    df['date_day'] = df['date'].str[:8]
    df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d%H%M%S', errors='coerce')
    return df.set_index('dt')

def _cfg():
    c = json.load(open(os.path.join(ROOT, 'config/config.json'), encoding='utf-8'))['futures_settings']
    c.update(json.load(open(os.path.join(ROOT, 'config/config_local.json'),
                            encoding='utf-8')).get('futures_settings', {}))
    return c

CFG = _cfg()
ACT = json.load(open(os.path.join(ROOT, 'config/active_strategy.json'), encoding='utf-8'))

BASE = dict(
    strategy='sar', Q=CFG['kf_q'], R=CFG['kf_r'], mult=CFG['kf_mult'],
    reentry_k=CFG['reentry_k'], point_value=POINT_VALUE,
    trim_std_outliers=CFG.get('std_trim_outliers', 1),
    atr_cutoff=float(CFG.get('atr_cutoff', 15.0)),
    min_std_error_entry=float(CFG.get('min_std_error_entry', 1.5)),
    consecutive_loss_limit=int(CFG.get('consecutive_loss_limit', 5)),
    entry_end_hour=CFG.get('entry_end_hour', 15),
    entry_end_minute=CFG.get('entry_end_minute', 0),
    entry_target_mode='breakout', breakout_k=float(ACT.get('best_k', 0.2)),
    ma_filter_period=int(CFG.get('ma_filter_period', 200)),
    sar_af_max=float(CFG.get('sar_af_max', 0.10)),
    sar_init_mult=float(CFG.get('sar_init_mult', 1.0)),
    daily_loss_limit_pt=None,
    realistic_gap_fill=True, commission_rate=0.00003,
    regime_filter_enabled=False, time_stop_enabled=False, sl_hard_cap_pt=None,
    slip_entry_pt=SLIP, slip_exit_sl_pt=SLIP,
    slip_exit_normal_pt=SLIP, slip_exit_force_pt=SLIP,
)

def run(df, **over):
    kw = dict(BASE); kw.update(over)
    r = run_sar_or_bb_replica(df.copy(), **kw, return_trades=True)
    return (r or {}).get('trade_log', [])

# ---- ATR 정규화 (진입 시점에 알 수 있는 전일까지 일중레인지 칼만 ATR) ----
def atr_by_day(df):
    d = df.groupby('date_day').agg(high=('high','max'), low=('low','min')).reset_index()
    rng = I.intraday_range(d['high'], d['low'])
    path = I.kalman_atr(np.asarray(rng, dtype=float), q=0.002, r=0.2)
    out = {}
    for i, k in enumerate(d['date_day'].tolist()):
        v = path[i-1] if i > 0 else np.nan
        out[k] = float(v) if (i > 0 and pd.notna(v) and v > 0) else np.nan
    return out

def q_of(ts):
    return f"{ts.year}-Q{(ts.month-1)//3+1}"

def evaluate(trades, amap):
    """건당 ATR배수 수익 + 거래일 클러스터 로버스트 SE."""
    rows = []
    for t in trades:
        dk = t['entry_time'].strftime('%Y%m%d')
        a = amap.get(dk, np.nan)
        if not np.isfinite(a) or a <= 0:
            continue
        rows.append((dk, t['pnl_pt'], t['pnl_pt']/a))
    if not rows:
        return dict(n=0)
    dk = np.array([r[0] for r in rows]); pt = np.array([r[1] for r in rows])
    ra = np.array([r[2] for r in rows])
    w, l = pt[pt > 0], pt[pt <= 0]; gl = -l.sum()
    eq = np.concatenate([[0.0], np.cumsum(pt)])
    days = sorted(set(dk))
    daysum = np.array([ra[dk == d].sum() for d in days])
    daycnt = np.array([(dk == d).sum() for d in days])
    m = ra.mean(); N = len(ra)
    se = np.sqrt((((daysum - m*daycnt))**2).sum())/N
    return dict(n=N, days=len(days), pts=float(pt.sum()),
                pf=float(w.sum()/gl) if gl > 0 else float('inf'),
                wr=float((pt > 0).mean()*100),
                mdd=float((np.maximum.accumulate(eq)-eq).max()),
                worst=float(pt.min()),
                atr_mean=float(m), atr_sd=float(ra.std(ddof=1)), atr_se=float(se),
                t=float(m/se) if se > 0 else 0.0,
                per_day=N/len(days), _ra=ra, _dk=dk)

def fmt(s, label=''):
    return (f"{label:28s} n={s['n']:3d} d={s['days']:3d} pt={s['pts']:+8.1f} PF={s['pf']:5.3f} "
            f"WR={s['wr']:5.2f}% MDD={s['mdd']:6.1f} worst={s['worst']:+6.2f} "
            f"ATR={s['atr_mean']:+.4f}±{s['atr_se']:.4f} t={s['t']:+.2f}")

if __name__ == '__main__':
    df = load_df(); amap = atr_by_day(df)
    days = sorted(df['date_day'].unique())
    tr = run(df)
    print(f"봉 {len(df):,} | DB거래일 {len(days)} | {days[0]}~{days[-1]}")
    print(fmt(evaluate(tr, amap), 'BASELINE'))
    import collections
    print(collections.Counter(t['reason'] for t in tr))
    print('sample trade keys:', sorted(tr[0].keys()))
