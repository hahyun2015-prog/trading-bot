"""2026-08-10 실거래가 0건으로 끝난 지점을 게이트별로 짚는다.

era_order_manager.py:4597-4700의 진입 게이트 순서를 그대로 옮겨, 당일 83봉이
어느 단계에서 얼마나 걸러지는지 센다. 마지막에 std_error만 실거래 값(0.5 고정)에서
정상 계산값으로 바꿔, 그 하나가 원인인지 확인한다.

실거래에서 확인된 사실:
    - 당일 로그에 '[주간선물(SAR)]' 문자열이 0건 → 진입 평가 자체에 도달 못 함
    - futures_atr_14 = 81.89pt (로그: 동적 Kalman ATR)
    - futures_std_error = 0.5 (update_kalman_targets가 SAR에서 호출되지 않아 초기값)
    - min_std_error_entry = 1.5, atr_cutoff = 15.0
    - day_consecutive_losses = 0, 재진입 청산가 0.0 (직전 거래 없음)
"""
import sys, json

sys.path.insert(0, r"c:\Antigravity\AI_T_Agent")
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import sqlite3

DB = r"c:\Antigravity\AI_T_Agent\futures_data.db"
CFG = json.load(open(r"c:\Antigravity\AI_T_Agent\config\config.json", encoding="utf-8"))["futures_settings"]
try:
    CFG.update(json.load(open(r"c:\Antigravity\AI_T_Agent\config\config_local.json",
                              encoding="utf-8")).get("futures_settings", {}))
except Exception:
    pass

ATR_LIVE = 81.89          # 당일 로그 실측
STD_LIVE = 0.5            # SAR에서 갱신되지 않는 초기값
ATR_CUTOFF = float(CFG.get("atr_cutoff", 15.0))
STD_GATE = float(CFG.get("min_std_error_entry", 1.5))
TARGET_LONG, TARGET_SHORT = 998.52, 980.96      # 당일 로그 실측
Q, R, MULT = CFG["kf_q"], CFG["kf_r"], CFG["kf_mult"]
TRIM = CFG.get("std_trim_outliers", 1)


def kalman_std_error(closes):
    """update_kalman_targets(1009-1044행)와 같은 방식으로 잔차 표준편차를 낸다."""
    x, P, path = None, 1.0, []
    for z in closes:
        if x is None:
            x = z
        else:
            P += Q
            K = P / (P + R)
            x = x + K * (z - x)
            P = (1 - K) * P
        path.append(x)
    errs = np.array(closes) - np.array(path)
    sl = errs[-20:]
    if TRIM > 0 and len(sl) > TRIM:
        sl = sl[np.argsort(np.abs(sl))[:-TRIM]]
    se = float(np.std(sl))
    return se if np.isfinite(se) and se > 0 else 0.5


def main():
    db = sqlite3.connect(DB)
    # 칼만 40봉 창을 채우려면 전일 이전 봉도 필요하다
    hist = db.execute(
        "SELECT date, close FROM futures_ohlcv WHERE code='A0568000' AND date<'20260810' "
        "ORDER BY date DESC LIMIT 300").fetchall()[::-1]
    today = db.execute(
        "SELECT date, open, high, low, close FROM futures_ohlcv WHERE code='A0568000' "
        "AND date LIKE '20260810%' ORDER BY date").fetchall()

    closes = [c for _, c in hist]
    print(f"당일 {len(today)}봉 | 직전 이력 {len(closes)}봉")
    print(f"게이트: atr_cutoff={ATR_CUTOFF} / min_std_error_entry={STD_GATE}")
    print(f"실거래 값: futures_atr_14={ATR_LIVE} / futures_std_error={STD_LIVE} (고정)")
    print()

    for mode, label in ((STD_LIVE, "실거래 그대로 (std_error 0.5 고정)"),
                        (None, "std_error를 정상 계산했다면")):
        cnt = dict(bars=0, time=0, consec=0, atr=0, std=0, target=0)
        se_samples = []
        buf = list(closes)
        for date, o, h, l, c in today:
            cnt["bars"] += 1
            hh, mm = int(date[8:10]), int(date[10:12])
            buf.append(c)

            # ① 진입 시간대 (09:10 시작 / 15:00 종료)
            if not ((hh, mm) >= (9, 10) and (hh, mm) < (15, 0)):
                continue
            cnt["time"] += 1

            # ② 연속손절 한도 — 당일 0건이라 통과
            cnt["consec"] += 1

            # ③ ATR 컷오프
            if ATR_LIVE < ATR_CUTOFF:
                continue
            cnt["atr"] += 1

            # ④ std_error 게이트
            se = mode if mode is not None else kalman_std_error(buf[-40:])
            se_samples.append(se)
            if se < STD_GATE:
                continue
            cnt["std"] += 1

            # ⑤ 목표가 도달
            if h >= TARGET_LONG or l <= TARGET_SHORT:
                cnt["target"] += 1

        print(f"[{label}]")
        print(f"  전체 봉            {cnt['bars']:>4}")
        print(f"  ① 진입시간대       {cnt['time']:>4}")
        print(f"  ② 연속손절 통과    {cnt['consec']:>4}")
        print(f"  ③ ATR컷오프 통과   {cnt['atr']:>4}   ({ATR_LIVE} >= {ATR_CUTOFF})")
        print(f"  ④ std_error 통과   {cnt['std']:>4}", end="")
        if se_samples:
            print(f"   (std_error {min(se_samples):.2f}~{max(se_samples):.2f} vs 문턱 {STD_GATE})")
        else:
            print()
        print(f"  ⑤ 목표가 도달      {cnt['target']:>4}  ← 여기까지 와야 SAR/BB 필터가 평가된다")
        print()


if __name__ == "__main__":
    main()
