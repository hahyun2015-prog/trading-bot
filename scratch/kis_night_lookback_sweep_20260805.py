"""야간 조회 범위를 넓히면 월요일 공백이 메워지는가 (2026-08-05).

문제:
    `era_order_manager.py:983`은 야간 데이터를 **최근 36시간**에서만 가져온다.
    월요일 08:45 기준 36시간 전은 토요일 20:45다. 그런데 직전 야간 세션은
    금 18:00 ~ 토 05:00에 끝나 있으므로 창 밖으로 잘려 나간다.
    → 월요일은 병합이 통째로 비고, 주 5일 중 하루가 무방비로 개장한다.
    (2026-08-05 검증에서 효과 0인 4일이 전부 월요일로 확인됨)

가설:
    창을 "직전 개장일의 야간까지" 닿도록 넓히면 월요일도 병합된다.
    금요일 밤은 월요일 아침 기준 ~52시간 전이지만, 금요일 주간 종가(15:45)보다는
    9시간 더 최근이고 미국·유럽 장 반응을 담고 있다.

주의할 점:
    칼만은 최근 40봉만 본다. 야간 세션 하나가 11시간 = 132개 5분봉이므로,
    병합이 걸리는 날은 40봉이 **전부 야간봉**이 된다. 즉 병합은 "야간을 조금
    보태는 것"이 아니라 **기준선 계산을 야간 구간으로 갈아끼우는 것**이다.
    창을 넓혀도 더 오래된 봉이 최근 40봉을 밀어내지는 않지만(정렬 후 최신순),
    화~금에 부작용이 없는지는 실측으로 확인한다.
"""
import sys, os, sqlite3
import importlib.util

sys.path.insert(0, os.path.dirname(__file__))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "kis_eval", os.path.join(os.path.dirname(__file__), "kis_night_merge_eval_20260805.py"))
kis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kis)

DB = kis.DB
LOOKBACKS = [36, 48, 60, 72, 96]   # 36=현행, 60·72=금요일 밤 포섭, 96=연휴 대응


def main():
    conn = sqlite3.connect(DB)

    nights = sorted({d for (d,) in conn.execute(
        "SELECT DISTINCT substr(date,1,8) FROM futures_ohlcv WHERE code=?", (kis.NIGHT_CODE,))})
    days = sorted(d for (d,) in conn.execute(
        "SELECT DISTINCT substr(date,1,8) FROM futures_ohlcv WHERE code=?", (kis.DAY_CODE,))
        if d >= nights[0])

    print(f"야간 수집일 {len(nights)}일 | 주간 거래일 {len(days)}일")
    print(f"야간 데이터가 있는 날짜: {' '.join(nights)}\n")

    for label, hh in (("09:10 진입개시", "091000"), ("08:45 개장", "084500")):
        print("=" * 108)
        print(f"[{label}] 야간 조회 범위 스윕")
        print("=" * 108)
        print(f"{'창(시간)':>9s} | {'전체 평균오차':>13s} {'중앙값':>9s} {'개선/악화/동일':>14s} | "
              f"{'월요일 평균오차':>15s} {'월 개선분':>10s}")
        print("-" * 108)

        base = None
        for hours in LOOKBACKS:
            r = kis.evaluate(conn, days, hh, hours=hours)
            if r.empty:
                continue
            mon = r[r.wd == "월"]
            cnt = (f"{int((r.err_b<r.err_a).sum())}/{int((r.err_b>r.err_a).sum())}"
                   f"/{int((r.err_b==r.err_a).sum())}")
            mon_gain = (mon.err_a - mon.err_b).sum()
            tag = " ← 현행" if hours == 36 else ""
            print(f"{hours:>9d} | {r.err_b.mean():>13.2f} {r.err_b.median():>9.2f} {cnt:>14s} | "
                  f"{mon.err_b.mean():>15.2f} {mon_gain:>+10.2f}{tag}")
            if hours == 36:
                base = r

        # 화~금이 창 확대로 손상되지 않는지 확인
        wide = kis.evaluate(conn, days, hh, hours=96)
        nonmon = base.wd != "월"
        same = (base.loc[nonmon, 'err_b'].round(6).values ==
                wide.loc[nonmon, 'err_b'].round(6).values).all()
        print(f"\n  화~금 {int(nonmon.sum())}일: 36h와 96h 결과 {'완전 동일 — 부작용 없음' if same else '상이(확인 필요)'}")

        mb, mw = base[base.wd == "월"], wide[wide.wd == "월"]
        print(f"  월요일 {len(mb)}일 상세:")
        for (_, x), (_, y) in zip(mb.iterrows(), mw.iterrows()):
            print(f"    {x.day}  기준 {x.ref:8.2f} | 미병합 {x.err_a:6.2f}pt "
                  f"| 36h {x.err_b:6.2f}pt | 96h {y.err_b:6.2f}pt  ({x.err_b-y.err_b:+.2f})")
        print()


if __name__ == '__main__':
    main()
