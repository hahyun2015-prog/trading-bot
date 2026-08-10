"""백테스트 전수 감사 — 어떤 실행이 realistic_gap_fill 없이 돌았나 (2026-08-10).

배경:
    bqa/kalman_backtester.py의 재현 함수들은 `realistic_gap_fill=False`가 기본값이다.
    False면 봉이 목표가를 갭으로 관통해도 목표가에 체결된 것으로 처리한다(유령체결).
    2026-08-10 실측: 같은 자료·같은 설정에서 플래그 하나로
        False → 거래 1382 승률 81.26% PF 28.58 자본 +130.0억
        True  → 거래 1170 승률 21.97% PF  0.33 자본  -9,721만
    승률 81%가 통째로 허수였다. 이 플래그 없이 돌아간 실행의 결론은 신뢰할 수 없다.

    [[feedback_backtester_gapfill_bug]]에 "명시적으로 넘겨야 한다"고 적혀 있었으나
    2026-08-04~05 작업이 이를 놓쳤다. 어느 실행이 오염됐는지 기계적으로 가려낸다.

방법:
    scratch/*.py에서 재현 함수 호출을 찾아, 같은 파일에 realistic_gap_fill 지정이
    있는지 본다. 호출과 지정이 떨어져 있을 수 있어(BASE dict 등) 파일 단위로 판정하고,
    판정 근거를 함께 남긴다.
"""
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"c:\Antigravity\AI_T_Agent"
SCRATCH = os.path.join(ROOT, "scratch")

# 갭 체결 처리를 하는 재현 함수들
REPLICAS = ("run_chandelier_live_replica", "run_kalman_live_replica",
            "run_sar_or_bb_replica", "run_kalman_night_replica",
            "run_kalman_live_replica_oc", "run_kalman_breakout_fair")

CALL_RE = re.compile(r"\b(" + "|".join(REPLICAS) + r")\s*\(")
FLAG_RE = re.compile(r"realistic_gap_fill\s*=\s*(True|False)")


def main():
    rows = []
    for fn in sorted(os.listdir(SCRATCH)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(SCRATCH, fn)
        try:
            src = open(path, encoding="utf-8").read()
        except Exception:
            continue

        calls = CALL_RE.findall(src)
        if not calls:
            continue

        flags = FLAG_RE.findall(src)
        if not flags:
            verdict, note = "오염", "플래그 지정 없음 → 기본값 False(유령체결)"
        elif "False" in flags and "True" not in flags:
            verdict, note = "오염", "명시적으로 False"
        elif "True" in flags and "False" not in flags:
            verdict, note = "정상", "True 지정"
        else:
            verdict, note = "혼재", f"True/False 모두 등장({len(flags)}곳) — 개별 확인 필요"

        rows.append(dict(file=fn, verdict=verdict, note=note,
                         calls=len(calls), funcs=sorted(set(calls)),
                         mtime=time.strftime("%m-%d", time.localtime(os.path.getmtime(path)))))

    order = {"오염": 0, "혼재": 1, "정상": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], r["mtime"], r["file"]))

    print(f"재현 함수를 호출하는 scratch 스크립트: {len(rows)}개\n")
    tally = {}
    for r in rows:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("판정 요약: " + " / ".join(f"{k} {v}개" for k, v in
                                 sorted(tally.items(), key=lambda x: order[x[0]])))
    print()

    cur = None
    for r in rows:
        if r["verdict"] != cur:
            cur = r["verdict"]
            print(f"\n{'=' * 118}\n[{cur}]\n{'=' * 118}")
        print(f"  {r['mtime']}  {r['file']:52s} 호출{r['calls']:>3}  {r['note']}")

    # 보고서 쪽도 훑는다 — 어느 문서가 어느 스크립트를 근거로 삼았는지
    print(f"\n\n{'=' * 118}")
    print("[보고서 → 재현 스크립트 연결]")
    print("=" * 118)
    verdict_by_file = {r["file"]: r["verdict"] for r in rows}
    for fn in sorted(os.listdir(ROOT)):
        if not fn.endswith(".md"):
            continue
        try:
            doc = open(os.path.join(ROOT, fn), encoding="utf-8").read()
        except Exception:
            continue
        refs = sorted(set(re.findall(r"scratch/([A-Za-z0-9_\-]+\.py)", doc)))
        if not refs:
            continue
        marks = []
        for ref in refs:
            v = verdict_by_file.get(ref, "미확인")
            marks.append(f"{ref}({v})")
        worst = "정상"
        for ref in refs:
            v = verdict_by_file.get(ref, "미확인")
            if v in ("오염", "혼재"):
                worst = v if worst != "오염" else "오염"
        flag = "⚠" if worst in ("오염", "혼재") else " "
        print(f" {flag} {fn}")
        for m in marks:
            print(f"      - {m}")


if __name__ == "__main__":
    main()
