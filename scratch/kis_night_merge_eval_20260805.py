"""KIS 야간데이터 병합이 아침 첫 타점을 실제로 개선하는가 (2026-08-05).

배경:
    era_order_manager.py:974-998은 주간 타점 계산 시 KIS로 수집한 야간 1분봉을
    5분으로 리샘플링해 칼만 입력에 합친다. 도입(2026-07-17) 근거는 아침 3건 관찰이었고
    (한 건이 71.66pt→8.73pt), 그 이후로 재검증한 적이 없다.

    KIS 수집이 2026-07-13부터라 전체 구간 재현은 불가능하지만, 그 이후 구간만으로
    표본을 3건에서 20일 수준으로 늘릴 수는 있다.

방법:
    각 거래일 D의 개장(08:45) 직전을 기준시각으로 잡고, 그 시점에 ERA가 계산했을
    타점을 두 가지로 재현한다.
      미병합 : 주간 5분봉 최근 300개 (date <= 08:44:59)
      병합   : 위 + KIS 야간 1분봉(기준시각-36h ~ 기준시각)을 5분 리샘플링해 concat
    era_order_manager.py의 계산을 그대로 옮긴다 — concat 후 date 중복 제거(주간 우선),
    내림차순 300개, 시간순 반전, 최근 40봉 칼만, 잔차 20개에서 |최대| 1개 trim.

평가:
    기준은 D의 08:45 봉 시가(실제 개장가)다.
      오차      = |KF 기준선 − 개장가|
      즉시신호  = 개장가가 [KF−band, KF+band] 밖인가 (밖이면 개장 직후 진입 트리거)
    타점이 밤새 흐름을 못 따라가면 기준선이 어제 종가 근처에 머물러, 갭이 뜬 날
    개장가가 밴드를 크게 벗어나며 곧바로 진입한다. 그 진입이 갭 되돌림에 물리는 것이
    이 병합이 막으려던 문제다.
"""
import sys, os, json, sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.stdout.reconfigure(encoding='utf-8')

import numpy as np
import pandas as pd

DB = r"c:\Antigravity\AI_T_Agent\futures_data.db"
FMT = "%Y%m%d%H%M%S"

cfg = json.load(open(r"c:\Antigravity\AI_T_Agent\config\config.json", encoding="utf-8"))["futures_settings"]
Q = cfg.get("kf_q", 0.0001)
R = cfg.get("kf_r", 0.5)
MULT = cfg.get("kf_mult", 1.0)
TRIM = cfg.get("std_trim_outliers", 0)

DAY_CODE = "A0568000"    # 실거래 미니 근월물 — ERA의 real_day_code
NIGHT_CODE = cfg.get("kis_night_code", "A05608")


def kalman_targets(df):
    """era_order_manager.py:1006-1082 그대로. (kf_price, std_error, band, trend) 반환."""
    if df.empty or len(df) < 40:
        return None

    df = df.iloc[::-1].reset_index(drop=True)          # 시간순
    closes_short = df.tail(40).reset_index(drop=True)['close'].values

    kf_prices, x, P = [], None, 1.0
    for z in closes_short:
        if x is None:
            x = z
        else:
            P = P + Q
            K = P / (P + R)
            x = x + K * (z - x)
            P = (1 - K) * P
        kf_prices.append(x)
    kf_prices = np.array(kf_prices)
    errors = closes_short - kf_prices

    std_slice = errors[-20:]
    if TRIM > 0 and len(std_slice) > TRIM:
        order = np.argsort(np.abs(std_slice))
        std_slice = std_slice[order[:-TRIM]]
    std_error = np.std(std_slice)
    if pd.isna(std_error) or std_error <= 0:
        std_error = 0.5

    kf_price = kf_prices[-1]

    trend = "NEUTRAL"
    t = df.copy()
    t['dt'] = pd.to_datetime(t['date'], format=FMT, errors='coerce')
    m15 = t.dropna(subset=['dt']).set_index('dt')['close'].resample('15Min').last().dropna()
    if len(m15) >= 5:
        kf_long, x_l, P_l = [], None, 1.0
        for z_l in m15.values:
            if x_l is None:
                x_l = z_l
            else:
                P_l = P_l + 0.001
                K_l = P_l / (P_l + 1.0)
                x_l = x_l + K_l * (z_l - x_l)
                P_l = (1 - K_l) * P_l
            kf_long.append(x_l)
        if len(kf_long) >= 2:
            slope = kf_long[-1] - kf_long[-2]
            trend = "UP" if slope > 0.01 else ("DOWN" if slope < -0.01 else "NEUTRAL")

    return kf_price, std_error, std_error * MULT, trend


def build(conn, cutoff, merge):
    """기준시각 cutoff에서 ERA가 보유했을 입력 프레임. merge=True면 KIS 야간 병합."""
    df = pd.read_sql(
        "SELECT date, close FROM futures_ohlcv WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 300",
        conn, params=(DAY_CODE, cutoff))
    if merge:
        lo = (datetime.strptime(cutoff, FMT) - timedelta(hours=36)).strftime(FMT)
        night = pd.read_sql(
            "SELECT date, close FROM futures_ohlcv WHERE code = ? AND date > ? AND date <= ? ORDER BY date ASC",
            conn, params=(NIGHT_CODE, lo, cutoff))
        if not night.empty:
            night['dt'] = pd.to_datetime(night['date'], format=FMT, errors='coerce')
            night = night.dropna(subset=['dt']).set_index('dt')
            n5 = night['close'].resample('5min').last().dropna().reset_index()
            n5['date'] = n5['dt'].dt.strftime(FMT)
            df = pd.concat([df, n5[['date', 'close']]], ignore_index=True)
            df = (df.drop_duplicates(subset='date')
                    .sort_values('date', ascending=False).head(300).reset_index(drop=True))
    return df


WEEK = ["월", "화", "수", "목", "금", "토", "일"]

# 평가 시점 — 08:45 개장, 09:10 진입 개시(era_order_manager.py:4534 is_after_910), 이후 감쇠 추적
CHECKPOINTS = [("084500", "08:45 개장"), ("091000", "09:10 진입개시"),
               ("093000", "09:30"), ("100000", "10:00")]


def evaluate(conn, days, hhmmss):
    """지정 시각에서 두 변형을 재현. 기준값은 그 시각 봉의 시가."""
    rows = []
    for d in days:
        bar = conn.execute(
            "SELECT date, open FROM futures_ohlcv WHERE code=? AND date>=? AND date<? ORDER BY date LIMIT 1",
            (DAY_CODE, d + hhmmss, d + "160000")).fetchone()
        if not bar:
            continue
        ref = bar[1]
        cutoff = (datetime.strptime(bar[0], FMT) - timedelta(seconds=1)).strftime(FMT)

        a = kalman_targets(build(conn, cutoff, merge=False))
        b = kalman_targets(build(conn, cutoff, merge=True))
        if not a or not b:
            continue

        rows.append(dict(
            day=d, wd=WEEK[datetime.strptime(d, "%Y%m%d").weekday()], ref=ref,
            kf_a=a[0], kf_b=b[0], err_a=abs(a[0] - ref), err_b=abs(b[0] - ref),
            out_a=not (a[0] - a[2] <= ref <= a[0] + a[2]),
            out_b=not (b[0] - b[2] <= ref <= b[0] + b[2]),
            trend_a=a[3], trend_b=b[3]))
    return pd.DataFrame(rows)


def main():
    conn = sqlite3.connect(DB)

    nights = {d for (d,) in conn.execute(
        "SELECT DISTINCT substr(date,1,8) FROM futures_ohlcv WHERE code=?", (NIGHT_CODE,))}
    days = sorted(d for (d,) in conn.execute(
        "SELECT DISTINCT substr(date,1,8) FROM futures_ohlcv WHERE code=?", (DAY_CODE,))
        if d >= min(nights))

    print(f"KIS 야간 수집일 {len(nights)}일 | 평가 대상 주간 거래일 {len(days)}일: {days[0]} ~ {days[-1]}")
    print(f"칼만 파라미터: q={Q} r={R} mult={MULT} trim={TRIM} | 주간코드={DAY_CODE} 야간코드={NIGHT_CODE}")

    # ── 상세: 개장 시점 ──────────────────────────────────────────────
    r0 = evaluate(conn, days, "084500")
    print("\n" + "=" * 122)
    print("[08:45 개장 시점 상세]")
    print("=" * 122)
    print(f"{'일자':10s}{'요일':>4s} {'개장가':>9s} | {'미병합 KF':>10s} {'오차':>7s} {'밴드밖':>6s} {'추세':>8s} |"
          f" {'병합 KF':>10s} {'오차':>7s} {'밴드밖':>6s} {'추세':>8s} | {'개선':>8s}")
    print("-" * 122)
    for _, x in r0.iterrows():
        print(f"{x.day:10s}{x.wd:>4s} {x.ref:>9.2f} | {x.kf_a:>10.2f} {x.err_a:>7.2f} "
              f"{'YES' if x.out_a else '-':>6s} {x.trend_a:>8s} |"
              f" {x.kf_b:>10.2f} {x.err_b:>7.2f} {'YES' if x.out_b else '-':>6s} {x.trend_b:>8s} |"
              f" {x.err_a-x.err_b:>+8.2f}")

    # ── 시점별 감쇠 ─────────────────────────────────────────────────
    print("\n" + "=" * 122)
    print("[시점별] 주간봉이 쌓이면서 병합 이득이 어떻게 줄어드는가")
    print("=" * 122)
    print(f"{'시점':>14s} | {'평균오차 미병합':>15s} {'병합':>9s} {'변화':>9s} | "
          f"{'중앙값 미병합':>14s} {'병합':>8s} | {'개선/악화/동일':>14s} | {'추세상이':>8s}")
    print("-" * 122)
    for hh, label in CHECKPOINTS:
        r = evaluate(conn, days, hh)
        if r.empty:
            continue
        chg = (r.err_b.mean() / r.err_a.mean() - 1) * 100
        cnt = f"{int((r.err_b<r.err_a).sum())}/{int((r.err_b>r.err_a).sum())}/{int((r.err_b==r.err_a).sum())}"
        print(f"{label:>14s} | {r.err_a.mean():>15.2f} {r.err_b.mean():>9.2f} {chg:>+8.1f}% | "
              f"{r.err_a.median():>14.2f} {r.err_b.median():>8.2f} | {cnt:>14s} | "
              f"{int((r.trend_a!=r.trend_b).sum()):>8d}")

    # ── 개장 시점 요약 ───────────────────────────────────────────────
    r = r0
    up = r[r.err_b < r.err_a]
    dn = r[r.err_b > r.err_a]
    print(f"\n[08:45 요약] {len(r)}일")
    print(f"  평균 오차   {r.err_a.mean():.2f} → {r.err_b.mean():.2f}pt "
          f"({(r.err_b.mean()/r.err_a.mean()-1)*100:+.1f}%) | 최대 {r.err_a.max():.2f} → {r.err_b.max():.2f}pt")
    print(f"  개선 {len(up)}일(합 {up.err_a.sub(up.err_b).sum():+.1f}pt) / "
          f"악화 {len(dn)}일(합 {dn.err_a.sub(dn.err_b).sum():+.1f}pt) / 동일 {int((r.err_b==r.err_a).sum())}일")
    print(f"  → 이득이 손실의 {up.err_a.sub(up.err_b).sum()/max(abs(dn.err_a.sub(dn.err_b).sum()),1e-9):.1f}배")
    print(f"  개장가가 밴드 밖(즉시 트리거 조건)  {int(r.out_a.sum())}일 → {int(r.out_b.sum())}일")
    same = r[r.err_a == r.err_b]
    print(f"  효과 없는 날 {len(same)}일: 전부 {'/'.join(sorted(set(same.wd)))}요일 "
          f"— 일요일 밤 세션이 없어 병합할 야간 데이터 자체가 없음")

    w = r.reindex(r.err_a.sub(r.err_b).abs().sort_values(ascending=False).index).head(5)
    print(f"\n[영향이 큰 날 상위 5]")
    for _, x in w.iterrows():
        print(f"  {x.day}({x.wd}) 개장 {x.ref:8.2f} | 미병합 KF {x.kf_a:8.2f}(오차 {x.err_a:6.2f}) "
              f"→ 병합 KF {x.kf_b:8.2f}(오차 {x.err_b:6.2f})  {x.err_a-x.err_b:+7.2f}pt")


if __name__ == '__main__':
    main()
