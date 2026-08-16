# 마이그레이션 4번 — 골든 등가성 검증 결과 (2026-08-12)

대상: `bqa/backtester.py` · `futures_trader/backtester.py` · `bqa/enhanced_backtester.py`
(지표 단일화 교체는 21:52~22:07에 이미 적용돼 있었고, **검증만 비어 있던 상태**였다)

> **결론: 3개 파일 전부 전 필드 일치. 불일치 0건. 음성 대조 13/13 검출.**

이번 세션은 **소스를 한 줄도 수정하지 않았다.** 캡처·대조만 수행했다.

---

## 0. 안전 조건 준수

| 항목 | 상태 |
|---|---|
| ERA 기동 | 하지 않음. `era/era.pid` 부재 유지 |
| `era/era_order_manager.py` | 미열람·미수정 |
| `system_stopped.flag` / `emergency_kill.flag` | 그대로 존재 |
| `config/active_strategy.json` | **md5 무변경 확인** (`016c05107c9effe5b0d3e972d47719f1`) |
| 교체 대상 소스 5종 mtime | 무변경 (21:53 / 21:56 / 21:59 / 22:05 / 17:26) |
| TCA · KIS 야간수집기 | 미접촉 |

이번 세션이 쓴 것: `scratch/_golden_bt/`, `scratch/_golden_enh/`, 이 보고서뿐.

샌드박스에 `ta` 패키지가 없어 `pip install ta`를 했다(샌드박스 한정, 사용자 PC 무관).

---

## 1. 전제 조건 — ta 호환 래퍼 검증 (`test_ta_compat_20260812.py`)

18:11 조사 보고서가 지적한 RSI 불일치(maxdiff 26.15)·MACD/EMA 부재는 21:53에 추가된
`indicators.ta_*` 래퍼로 해소됐고, 이번에 실행해 실측 확인했다. n=31,797봉.

| 대조 | 최대오차 |
|---|---|
| `BollingerBands` mavg / hband / lband vs `bollinger_series(ddof=0)` | **0.0** (3건) |
| `RSIIndicator(14)` vs `ta_rsi_series` | **0.0** |
| `EMAIndicator(10)` / `(34)` vs `ta_ema_series` | **0.0** (2건) |
| `MACD` macd / signal / diff vs `ta_macd_series` | **0.0** (3건) |
| `AverageTrueRange(14)` vs `ta_atr_series` | **0.0** |

양성 10/10 통과. 음성 5/5 검출(1e-9 섭동, `wilder_rsi`와의 정의 차이 5.37,
`ddof=1` 볼린저 3.195pt 등) — 대조기 자체가 유효함을 확인.

**주의**: `wilder_rsi`는 `ta`의 RSI와 정의가 다르다(음성 대조에서 5.37 차이로 검출).
ta 호환이 필요한 곳에는 반드시 `ta_rsi_series`를 써야 한다.

---

## 2. `bqa/backtester.py`

base = `bqa/backtester.py.bak_20260812_215222`, new = 현재 파일.
소스에 동일한 계측 훅 3종을 주입한 사본을 exec 해 거래·지표를 뽑고, **동일한 직렬화
코드**를 통과시켜 비교했다(어느 쪽도 정답으로 가정하지 않음).

| 항목 | 결과 |
|---|---|
| 체결 | DayOnly 1,069 / NightOnly 0 / 24H 1,427 = **2,496건 전수 일치** |
| 지표 스칼라 | bb_m·bb_h·bb_l·rsi **127,112개 전수 일치** |
| stdout 원문 | 일치 |
| base/new JSON 크기 | 1,874,710 bytes로 동일 |

음성 대조 4/4 검출(체결 자본 +1e-6, 체결 1건 삭제, 지표 +1e-9, stdout 1글자).

---

## 3. `futures_trader/backtester.py`

`bqa/backtester.py`와 사실상 동일 파일이나 별도로 전 과정을 반복했다.

| 항목 | 결과 |
|---|---|
| 체결 | 2,496건 전수 일치 |
| 지표 스칼라 | 127,112개 전수 일치 |
| stdout 원문 | 일치 |

음성 대조 4/4 검출.

---

## 4. `bqa/enhanced_backtester.py` (라이브 설정 위험 파일)

이 파일의 `main()`은 `config/active_strategy.json`(= `era_order_manager.py`가 런타임에
읽는 라이브 전략 설정)을 덮어쓴다. 하네스가 `__name__`을 `'__golden__'`으로 넣어
`main()` 자체를 실행하지 않고, 추가로 `RESULTS_FILE`을 임시 경로로 무력화한다.
실행 전후 **md5 동일** 확인 완료 — 라이브 설정은 훼손되지 않았다.

| 항목 | 결과 |
|---|---|
| 지표 | 13종 **412,867 스칼라 전수 일치** |
| 조합별 요약지표 | 28개 조합 전 필드 일치 |
| 체결 | **11,169건 전수 일치** |
| base/new JSON 크기 | 5,946,924 bytes로 동일 |

음성 대조 4/4 검출(지표 +1e-9, 요약 trades +1, 체결 자본 +1e-6, 체결 1건 삭제).

---

## 5. 누적 현황

| 단계 | 대상 | 상태 |
|---|---|---|
| A | `kalman_backtester.py` `return_trades` 추가 | 완료 (no-op 9/9) |
| B~D | `kalman_backtester.py` 지표 단일화 | 완료 (62조합 전수 일치) |
| 마이그 4 | `bqa/backtester.py` | **완료 (이번 검증)** |
| 마이그 4 | `futures_trader/backtester.py` | **완료 (이번 검증)** |
| 마이그 4 | `bqa/enhanced_backtester.py` | **완료 (이번 검증)** |

## 6. 남은 것 / 다음 후보

- `bqa/enhanced_backtester.py`의 `config/active_strategy.json` 덮어쓰기는 **여전히 살아
  있는 위험**이다. 이번엔 하네스로 우회했을 뿐, 사람이 그냥 실행하면 라이브 설정이 바뀐다.
  드라이런 플래그나 출력 경로 인자화를 별도 작업으로 검토할 만하다.
- `run_bqa.bat:54`, `futures_trader/run_backtester.bat:10`의 수동 실행 경로는 그대로 유효.
- `bqa/kalman_backtester.py`의 `run_kalman_night_replica`는 DB에 야간 세션 봉이 없어
  실데이터 대조가 불가능하다(A단계 보고서 참조). 야간 데이터 수집 전까지는 미검증 구간.
