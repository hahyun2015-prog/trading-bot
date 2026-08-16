# A단계 — kalman_backtester.py `return_trades` 인자 추가 (no-op 검증 완료)

작업일: 2026-08-12 / 대상: `bqa/kalman_backtester.py`
백업: `bqa/kalman_backtester.py.bak_20260812_164702`

## 변경 내용 (순수 가산)

세 함수에 `return_trades=False`를 **시그니처 맨 끝**에 추가했다(위치 인자 호출이 깨지지 않도록).

| 함수 | 시그니처 | trade_log 초기화 | append | 반환부 |
|---|---|---|---|---|
| `run_kalman_live_replica` | 맨 끝 추가 | `entry_time=None`, `trade_log=[]` | 청산 블록 | `result` dict + 조건부 `trade_log` |
| `run_kalman_night_replica` | 맨 끝 추가 | 동일 | 동일 | 동일 |
| `run_kalman_live_replica_oc` | 맨 끝 추가 | 동일 | 동일 | 동일 |

trade_log 항목 형식(chandelier와 동일 + `reason` 추가):

```
entry_time, exit_time, direction('LONG'/'SHORT'), entry_price, exit_price,
pnl_pt, is_force, is_sl, reason('SL'|'FORCE'|'TP_TRAIL'), contracts, gain_krw
```

전략 로직·지표 계산·비용 모델은 한 줄도 수정하지 않았다. diff 총 189행이며 전부
인자 추가 / 변수 초기화 / `if return_trades:` 블록 / 반환 dict 재작성뿐이다.

## 검증 결과 — 세 함수 모두 `ALL_IDENTICAL = True`

원본 `.bak` 모듈과 패치본 모듈을 각각 import 해서 같은 DataFrame으로 돌린 뒤,
결과 dict를 canonical 직렬화(float은 repr로 전 자리 보존)해 비교했다.
`return_trades=True`로도 한 번 더 돌려 `trade_log`를 제외한 summary가 동일한지 확인했다.

| 함수 | 케이스 | 거래수 | 기본호출 동일 | return_trades=True 동일 | trade_log 건수 |
|---|---|---|---|---|---|
| live | 기본값 | 1924 | O | O | 1924 |
| live | mult=1.2, kf_sl_mult=2.5 | 1753 | O | O | 1753 |
| live | atr_cutoff=1.0, disable_trend_filter=True | 2429 | O | O | 2429 |
| oc | 기본값 | 1622 | O | O | 1622 |
| oc | mult=1.2, kf_sl_mult=3.0 | 1592 | O | O | 1592 |
| oc | atr_cutoff=1.0, hard_cap=20.0 | 1608 | O | O | 1608 |
| night(합성) | 기본값 | 1314 | O | O | 1314 |
| night(합성) | mult=1.2, kf_sl_mult=6.0 | 1245 | O | O | 1245 |
| night(합성) | atr_cutoff=1.0, hard_cap=20.0 | 1307 | O | O | 1307 |

`trade_log` 건수가 summary의 `trades`와 정확히 일치 → 거래 기록 누락/중복 없음.

### night 함수에 대한 주의 (다음 단계에서 중요)

`futures_data.db`의 `futures_ohlcv` / `futures_ohlcv_1m` 두 테이블 모두 **야간 세션
(18:00~04:45) 봉이 한 개도 없다**(시간대 분포: 08~16시만 존재). 따라서
`run_kalman_night_replica`는 실데이터로 돌리면 항상 `None`을 반환하며, 실거래 경로가
전혀 실행되지 않는다. 등가성 검증을 위해 주간 봉의 타임스탬프를 +9시간 30분 시프트한
합성 야간 데이터로 코드 경로를 실제로 태워 확인했다(가격 현실성은 no-op 검증에 무관).

→ B단계 이후 골든 캡처에서도 night 조합은 실데이터로는 의미 있는 대조가 불가능하다.

## 호출부 영향

`bqa/` 내 호출부 3곳(2010, 2019, 2028행 CLI 분기)은 전부 키워드 인자 호출.
`scratch/`에도 위치 인자를 4개 넘게 넘기는 호출은 없다. 기존 호출부 영향 0.

## 산출물

- `scratch/A_stage_diff_20260812.txt` — 전체 diff
- `scratch/A_verify_live.json`, `A_verify_oc.json`, `A_verify_night_synth.json` — 케이스별 원시 결과
- 검증 스크립트: `scratch/verify_A_noop.py`, `scratch/verify_A_night_synth.py`
