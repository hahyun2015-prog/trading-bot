# 마이그레이션 4번 — 1단계 조사 결과 (2026-08-12)

대상: `bqa/backtester.py`, `bqa/enhanced_backtester.py`, `futures_trader/backtester.py`

## 1. 사용처 조사

Python `import` 사용처: **3개 파일 모두 0건** (worktree/.claude 제외, scratch 포함해서도 0건).
그러나 **배치 파일 진입점이 2건 존재** — "완전 미사용"은 아님.

| 파일 | Python import | .bat 진입점 | 판정 |
|---|---|---|---|
| `bqa/backtester.py` | 없음 | `run_bqa.bat:54` (메뉴 선택 "2" → 선물 백테스트 실행) | **수동 실행 경로 있음** |
| `futures_trader/backtester.py` | 없음 | `futures_trader/run_backtester.bat:10` | **수동 실행 경로 있음** |
| `bqa/enhanced_backtester.py` | 없음 | 없음 | 진입점 없음 (진짜 미사용에 가까움) |

`ai_trader/백테스트_실행.bat`는 `ai_trader/backtester.py`(이번 범위 밖 다른 파일)를 가리킴.

## 2. ⚠️ 안전 이슈 — enhanced_backtester.py 의 라이브 설정 쓰기

`bqa/enhanced_backtester.py` L31 / L518-521:

```python
RESULTS_FILE = os.path.join(workspace_root, "config", "active_strategy.json")
...
os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
with open(RESULTS_FILE, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=4)
```

`config/active_strategy.json`은 `era/era_order_manager.py` L643-646에서 **런타임에 읽어 주간선물 파라미터를 오버라이드**한다.
즉 이 백테스터를 그냥 실행하면 **라이브 전략 설정 파일을 덮어쓴다.**

→ 골든 캡처 시 절대 그대로 실행 금지. `RESULTS_FILE`을 임시 경로로 몽키패치한 하네스로만 실행할 것.
→ 실행 전 `config/active_strategy.json` 백업 필수.

## 3. 지표 대응표

### bqa/backtester.py = futures_trader/backtester.py (사실상 동일 파일)

두 파일은 주석·`sys.path` 삽입·DB 경로 해석 방식만 다르고 **전략/비용/지표 코드가 완전 동일**.
(`futures_trader`판은 `sqlite3.connect("futures_data.db")` 상대경로 → cwd 의존)

| 기존 | indicators.py 대응 | 일치 여부 |
|---|---|---|
| `ta.volatility.BollingerBands(close, 20, 2)` | `bollinger_series(closes, 20, 2.0, ddof=0)` | ✅ maxdiff **0.0** (ddof=0일 때만) |
| `ta.momentum.RSIIndicator(close, 14).rsi()` | `wilder_rsi(Window(closes), 14)` | ❌ **불일치, maxdiff 26.15** |
| `rolling(5).min/max` (rsi/low/high) | 대응 함수 없음 (게다가 계산 후 미사용 — 죽은 코드) | — |

### bqa/enhanced_backtester.py

| 기존 | indicators.py 대응 | 상태 |
|---|---|---|
| `BollingerBands(20,2)` | `bollinger_series(..., ddof=0)` | ✅ 일치 |
| `bb_width = (h-l)/m` | `bandwidth_series` | ⚠️ 공간태그(Series.space) 요구 — 래핑 필요 |
| `RSIIndicator(14)` | `wilder_rsi` | ❌ 불일치 |
| `AverageTrueRange(14)` | `true_range` + Wilder 평활 | ⚠️ ta 재귀식 확인 필요 |
| `MACD(26,12,9)` | **없음** | ❌ 대응 함수 부재 |
| `EMAIndicator(10)`, `EMAIndicator(34)` | **없음** | ❌ 대응 함수 부재 |

## 4. ⚠️ 핵심 수치 발견 — ta 라이브러리 시맨틱

31,797봉(`futures_data.db`, code=10100000) 실측:

### (a) 볼린저 표준편차 ddof
`ta` 소스 `BollingerBands._run()`: `.std(ddof=0)` — **모표준편차**.
`indicators.bollinger_series` 기본값은 `ddof=1`.

- `ddof=1`로 교체 시 상단밴드 maxdiff **3.195pt** → 진입 조건이 바뀜 (동작 변경)
- `ddof=0` 명시 시 maxdiff **0.0** → 완전 일치

→ 교체 시 반드시 `ddof=0`을 명시해야 함. **라이브(`era_order_manager` L984-988)는 pandas 기본 ddof=1**이므로,
이 백테스터들과 라이브는 애초에 서로 다른 볼린저를 쓰고 있었다. (신규 발견 #1)

### (b) RSI 정의 불일치
`ta` 소스 `RSIIndicator._run()`:
```python
up = diff.where(diff > 0, 0.0)          # index 0 의 NaN → 0.0 으로 치환됨
emaup = up.ewm(alpha=1/w, min_periods=w, adjust=False).mean()
rsi = np.where(emadn == 0, 100, 100 - 100/(1 + emaup/emadn))
```
- 시드: **0에서 출발하는 EWM** (Wilder의 초기 SMA 시드 아님)
- 첫 유효 인덱스: ta = **13**, `indicators.wilder_rsi` = **14**
- 실측 maxdiff **26.15** (전 구간)

→ `indicators.wilder_rsi`로 단순 교체하면 **동작이 바뀐다.** 동작 무변경을 지키려면
`indicators.py`에 ta 호환 변형(`ta_rsi_series` 등)을 추가하거나, 이 파일들은 RSI만 `ta`에 남겨야 함.

→ 라이브/기존 마이그레이션 대상들이 쓰는 RSI가 Wilder라면, **이 3개 백테스터만 다른 RSI로 돌고 있었다는 뜻**. (신규 발견 #2)

## 5. 실행 가능성 / 소요 시간

| 항목 | 결과 |
|---|---|
| DB 경로 하드코딩 | `bqa/backtester.py`: `workspace_root/futures_data.db` (상대) — shim 불필요 ✅ |
| | `futures_trader/backtester.py`: `"futures_data.db"` (cwd 상대) — cwd를 `futures_trader/`로 잡으면 자체 DB(7.2MB) 사용 |
| | `bqa/enhanced_backtester.py`: `workspace_root/futures_data.db` (상대) ✅ |
| 의존성 | `ta` 패키지 — 샌드박스에 미설치였음 → `pip install ta --break-system-packages` 로 설치 완료 |
| CLI 진입점 | 3개 모두 `if __name__ == "__main__"` 존재 |
| 데이터 | code=10100000, 31,797봉 |
| **1회 실행 시간** | `bqa/backtester.py` = **36초** (3개 run_mode) |
| | `enhanced_backtester.py` = 미측정 (K값 스윕 2회 × 11스텝 → 수 분 예상, 라이브 설정 쓰기 때문에 미실행) |

## 6. 기존 base 결과 (bqa/backtester.py, 참고용)

```
주간장 전용   : 1069거래 승률 34.3% 수익률 -269.05% MDD 276.53%
야간장 전용   :    0거래
주야간 24시간 : 1427거래 승률 33.5% 수익률 -327.51% MDD 298.25%
```
(야간장 0거래 — 진입차단 조건 `6 <= hour < 18` 과 데이터 시간대 조합 결과. 기존 동작 그대로.)

## 7. 신규 비표준·의심 지점 (기존 7건에 추가)

| # | 파일 | 내용 |
|---|---|---|
| N1 | 3개 전부 | `ta` 볼린저는 **ddof=0**(모표준편차). 라이브 `era_order_manager`는 ddof=1. 서로 다른 밴드를 쓰고 있음 |
| N2 | 3개 전부 | `ta` RSI는 Wilder RSI가 아님 (0 시드 EWM, 첫 유효 idx 13). 실측 최대 26.15 차이 |
| N3 | `bqa/backtester.py` L47-50, `futures_trader/backtester.py` L43-46 | `rsi_min_5`/`rsi_max_5`/`low_min_5`/`high_max_5` 계산 후 **어디서도 사용 안 함** (죽은 코드) |
| N4 | `bqa/enhanced_backtester.py` L518-521 | 백테스터가 **라이브 설정 `config/active_strategy.json`을 덮어씀** |
| N5 | `futures_trader/backtester.py` L7 | DB 경로가 cwd 상대 → 실행 위치에 따라 **다른 DB**(7.2MB vs 53MB)를 읽음 |
| N6 | `bqa/backtester.py` vs `futures_trader/backtester.py` | 216행 중 ~200행이 **완전 중복** |
| N7 | `bqa/backtester.py` L68/74 | `for i in range(10, ...)` 안에서 `if i >= 10` — 항상 참인 죽은 분기 |
