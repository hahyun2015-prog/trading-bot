"""실거래 슬리피지 실측 — 백테스트 가정이 맞았나 (2026-08-11).

배경:
    워크포워드 결과 OOS PF 0.77로, 사전에 고를 수 있는 파라미터로는 수익 구성을
    찾지 못했다. 남은 축 하나가 비용이다. 지금까지의 백테스트는 슬리피지를
    가정값으로 넣었다.

      run_chandelier_live_replica : 진입 1.5 / SL 3.0 / 익절 0.5 / 강제 2.0pt
      해외봇 이식                  : 편도 1.0pt (왕복 2.0pt)

    왕복 2pt는 2025년 일중 레인지(4~6pt)의 3분의 1에서 절반이다. 이 가정이 실제보다
    크면 "비용에 먹혀서 성립 불가"라는 결론 자체가 흔들린다.

측정 방법:
    ERA 로그에 주문 시점 가격과 **개별 체결가**가 모두 남는다.

      [주간선물 주문] LONG 진입  | 1226.60pt | 15계약 | A0568000
      [주간선물 실체결 확정] 미니 F 202608(0568000) | 1226.3 | 1계약 | +매수
      ... (계약수만큼 반복)

    주문 한 건에 딸린 체결들을 수량가중평균해 주문가와 비교한다.
    부호는 불리한 쪽을 양수로 잡는다.
      매수(LONG진입·SHORT청산) : 체결가 - 주문가   (비싸게 샀으면 손해)
      매도(LONG청산·SHORT진입) : 주문가 - 체결가   (싸게 팔았으면 손해)

주의:
    전 구간 모의투자다. 키움 모의 체결은 실제 호가 잔량·시장충격을 반영하지 않으므로,
    여기서 나온 값은 **실계좌 슬리피지의 하한**으로 읽어야 한다.
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

LOGS = [r"c:\Antigravity\AI_T_Agent\era\era_order_manager.log.1",
        r"c:\Antigravity\AI_T_Agent\era\era_order_manager.log"]

ORDER_RE = re.compile(r"\[주간선물 주문\]\s*(LONG|SHORT)\s*(진입|청산)\s*\|\s*([\d.]+)pt\s*\|\s*(\d+)계약")
FILL_RE = re.compile(r"\[주간선물 실체결 확정\].*?\|\s*([\d.]+)\s*\|\s*(\d+)계약\s*\|\s*([+\-])(매수|매도)")


def read_tail(path, limit=200_000_000):
    if not os.path.exists(path):
        return ""
    sz = os.path.getsize(path)
    with io.open(path, "rb") as f:
        f.seek(max(0, sz - limit))
        return f.read().decode("utf-8", errors="replace")


def collect():
    """주문 → 뒤따르는 체결들 묶음. 다음 주문을 만나면 끊는다."""
    orders = []
    for path in LOGS:
        cur = None
        for line in read_tail(path).split("\n"):
            m = ORDER_RE.search(line)
            if m:
                if cur and cur["fills"]:
                    orders.append(cur)
                side, kind, price, qty = m.group(1), m.group(2), float(m.group(3)), int(m.group(4))
                cur = dict(side=side, kind=kind, price=price, qty=qty, fills=[])
                continue
            if cur is not None:
                f = FILL_RE.search(line)
                if f:
                    cur["fills"].append((float(f.group(1)), int(f.group(2)), f.group(4)))
        if cur and cur["fills"]:
            orders.append(cur)
    return orders


def slippage(o):
    """불리한 방향을 양수로. 체결 방향(매수/매도)은 로그의 값을 그대로 쓴다."""
    tot_qty = sum(q for _, q, _ in o["fills"])
    if tot_qty == 0:
        return None
    vwap = sum(p * q for p, q, _ in o["fills"]) / tot_qty
    buy_side = o["fills"][0][2] == "매수"
    slip = (vwap - o["price"]) if buy_side else (o["price"] - vwap)
    return dict(slip=slip, vwap=vwap, order=o["price"], qty=tot_qty,
                ordered_qty=o["qty"], side=o["side"], kind=o["kind"], buy=buy_side)


def summarize(label, rows):
    if not rows:
        print(f"  {label:22s} 표본 없음")
        return
    a = np.array([r["slip"] for r in rows])
    print(f"  {label:22s} {len(a):>4d}건  평균{a.mean():+7.3f}  중앙{np.median(a):+7.3f}  "
          f"표준편차{a.std():6.3f}  최악{a.max():+7.3f}  최선{a.min():+7.3f}pt")


def main():
    orders = collect()
    rows = [r for r in (slippage(o) for o in orders) if r]
    print(f"주문 {len(orders)}건에서 슬리피지 산출 가능 {len(rows)}건\n")

    partial = [r for r in rows if r["qty"] != r["ordered_qty"]]
    if partial:
        print(f"⚠ 부분체결(주문수량≠체결수량) {len(partial)}건 — 로그 절단 가능성, 아래 통계에는 포함\n")

    print("=" * 104)
    print("[슬리피지] 양수 = 불리 (비싸게 샀거나 싸게 팔았음)")
    print("=" * 104)
    summarize("전체", rows)
    print()
    for side in ("LONG", "SHORT"):
        for kind in ("진입", "청산"):
            summarize(f"{side} {kind}", [r for r in rows if r["side"] == side and r["kind"] == kind])
    print()
    summarize("진입 전체", [r for r in rows if r["kind"] == "진입"])
    summarize("청산 전체", [r for r in rows if r["kind"] == "청산"])

    a = np.array([r["slip"] for r in rows])
    rt = a.mean() * 2
    print(f"\n{'=' * 104}")
    print("[백테스트 가정과 비교]")
    print("=" * 104)
    print(f"  실측 편도 평균      {a.mean():+.3f}pt   (왕복 {rt:+.3f}pt)")
    print(f"  불리한 쪽 비율      {(a > 0).mean() * 100:.1f}%")
    print(f"  |슬리피지| 0.5pt 초과 {(np.abs(a) > 0.5).mean() * 100:.1f}%")
    print()
    print(f"  샹들리에 백테스트 가정  진입 1.5 / SL 3.0 / 익절 0.5 / 강제 2.0pt")
    print(f"  해외봇 이식 가정        편도 1.0pt (왕복 2.0pt)")
    print(f"  → 왕복 기준 가정 2.0pt vs 실측 {rt:.3f}pt")
    if abs(rt) > 1e-9:
        print(f"  → 가정이 실측의 {2.0 / max(abs(rt), 1e-9):.1f}배")

    # 계약수별 — 시장충격이 있으면 수량이 커질수록 나빠져야 한다
    print(f"\n{'=' * 104}")
    print("[체결수량별] 수량이 늘수록 나빠지는가 (시장충격 유무)")
    print("=" * 104)
    for lo, hi in ((1, 1), (2, 5), (6, 10), (11, 15), (16, 99)):
        sel = [r for r in rows if lo <= r["qty"] <= hi]
        summarize(f"{lo}~{hi}계약", sel)


if __name__ == "__main__":
    main()
