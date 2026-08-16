# C단계 보고 — `bqa/kalman_backtester.py` 지표 계산부 `indicators.py` 교체

작성 2026-08-12 · 대상 `bqa/kalman_backtester.py` (2063행 → 1947행, **-116행**)
설계 근거 `지표계산_단일화_설계안_20260812.md`

> **이번 세션 범위**: 교체 + 문법 검사 + 백업본 대비 국소 동일성 확인까지.
> new 골든 캡처와 base 대조는 **다음 세션**.

---

## 1. 백업

| 파일 | 시점 | 비고 |
|---|---|---|
| `bqa/kalman_backtester.py.bak_20260812_164702` | A단계 종료 시점 | 기존 |
| `bqa/kalman_backtester.py.bak_C_20260812_172302` | **C단계 착수 직전** | 이번 세션 추가 |

전체 diff: `scratch/C_stage_kalman_backtester_20260812.diff` (444행 / 삭제 212 · 추가 96)

---

## 2. 교체 내역

파일 상단에 `sys.path` 삽입 후 `import indicators as I` 추가.
4중복 블록을 흡수할 모듈 레벨 헬퍼 `_daily_kf_atr(df)` 신설(1소스).

| # | 대상 | 교체 전 | 교체 후 | 중복 해소 |
|---|---|---|---|---|
| 1 | 일봉 TR | 인라인 `np.maximum(...).fillna(H−L)` ×4 | `I.true_range()` (헬퍼 내 1회) | **4 → 1** |
| 2 | TR 칼만(Q=0.002,R=0.2) | 인라인 루프 ×4 | `I.kalman_atr(q=0.002, r=0.2)` (헬퍼 내 1회) | **4 → 1** |
| 3 | `prev_range_map` | `daily['range'].iloc[i-1]` ×3 | `I.prev_session_range(h, l, i)` | 3곳 |
| 4 | `night_prev_range_map` | `daily['range'].iloc[i]` | `I.prev_session_range(h, l, i+1)` | 1곳 |
| 5 | 창 칼만 평활 | 인라인 루프 ×4 | `I.Window.closed_upto()` + `I.kalman_residual_std()` | **4 → 1** |
| 6 | 잔차 std (trim 포함/미포함) | 인라인 ×4 | `I.kalman_residual_std(trim=…)` | **4 → 1** |
| 7 | 밴드 타점 / TP 타점 | `kf ± std*mult` ×6 | `I.kalman_band_targets()` | 6곳 |
| 8 | 추세필터 장기 칼만 | 인라인 루프 ×4 | `I.kalman_path(I.Window(long_closes), trend_q, trend_r)` | **4 → 1** |
| 9 | 돌파 타점 | `day_open ± pr*k` ×2 | `I.breakout_targets()` | 2곳 |

교체 후 파일 내 **인라인 칼만 루프는 0개** (`P = P + Q` / `Pl = Pl + trend_q` / `P_atr = P_atr + Q_atr` 전부 소멸).
유일하게 남은 `KalmanFilter1D` 클래스는 `run_kalman_breakout_fair` 전용 — 아래 §3 참조.

### 함수별 처리

| 함수 | TR/ATR | 창 칼만 | 추세 칼만 | 돌파 타점 |
|---|---|---|---|---|
| `run_production_rolling_k` | ✅ | — | — | ✅ (2곳) |
| `run_kalman_breakout_fair` | — | **무수정(의도)** | — | — |
| `run_kalman_live_replica` | — | ✅ | ✅ | — |
| `run_chandelier_live_replica` | ✅ | ✅ | ✅ | — |
| `run_kalman_night_replica` | ✅ | ✅ | ✅ | — |
| `run_kalman_live_replica_oc` | ✅ | ✅ | ✅ | — |

---

## 3. 요청하신 3가지 차이를 그대로 보존했음을 확인

**(a) 야간 인덱스 규약 `[i]` vs 주간 `[i-1]`**
`run_kalman_night_replica`는 `kf_atr_path[i]` / `prev_session_range(..., i+1)`을 그대로 씀.
헬퍼 `_daily_kf_atr`은 `daily`/`kf_atr_path`/`day_list`만 돌려주고 **atr_map 구성은 호출부에 남겼다** —
규약 차이가 헬퍼 안으로 숨어 통일돼 버리는 것을 구조적으로 막기 위함. 해당 위치에 주석 명시.

**(b) `run_kalman_breakout_fair`의 ddof=1**
`df['error'].rolling(20).std()`(pandas 기본 ddof=1)를 쓰고 창이 아닌 전 구간 칼만이라
`kalman_residual_std`(ddof=0, 창 기반)와 의미론이 다르다. **이 함수는 한 줄도 건드리지 않았다.**
백업본 대비 결과 동일함을 확인(§5).

**(c) 워밍업 인덱스 규약**
`Window.closed_upto(arr, i, size)` = `arr[i-size : i]` (배타적).
- `i >= kf_window` 가드 ↔ `closed_upto(closes, i, kf_window) is None` 조건이 **정확히 동치**임을 i=0..119 전수 확인 → `guard match: True`
- `reset_kf_daily=True` 분기의 가변 창은 `size = i - window_start`로 전달 → `closes[window_start:i]`와 동일
- `n < kf_window+10` 조기 반환 가드는 손대지 않음(그 앞단에 그대로 존재)

---

## 4. 이번에 고치지 않은 발견 목록 (그대로 둠, 주석만 보강)

1. `bucket60` 하드코딩 — oc/night는 `//3600` 고정, live/chandelier는 `trend_bar_minutes*60`
2. `trim_std_outliers` 인자가 oc/night에 없음 → 두 함수는 `trim=0`으로 명시 호출(동작 동일)
3. `run_kalman_breakout_fair`의 ddof=1 + 경미한 미래참조
4. `MARGIN_RATE = 0.10` (실제 19.8%의 절반)
5. `load_futures_data` DB 경로 하드코딩 (`c:\Antigravity\...`) — 리눅스에서 로드 불가, 검증은 경로 우회로 수행
6. `hard_stop_enabled` 분기가 `if profit_lock_enabled:` 안쪽 elif → §6에서 조합으로 대응

---

## 5. 검증 결과

### 5-1. 문법·임포트
```
python -m py_compile bqa/kalman_backtester.py scratch/golden_kalman_combos_20260812.py  → OK
import kalman_backtester  → OK (indicators.py 해석 경로 정상)
상수 무변경: POINT_VALUE 250000 / MARGIN_RATE 0.1 / MARGIN_CAP 0.3 / SLIP_FEE_PT 0.05 / INIT_CAPITAL 50000000
시그니처 무변경: A단계 return_trades 4개 함수 유지
제거 지역변수 잔존 참조 0건 (window_closes / kf_path / errs / std_slice)
```

### 5-2. 지표 함수 수치 동일성 (합성 데이터)
| 항목 | 결과 |
|---|---|
| `true_range` vs 인라인 TR (n=300) | `np.array_equal` **True** |
| `kalman_atr` vs 인라인 루프 | `np.array_equal` **True** |
| `kalman_residual_std` + `kalman_band_targets` vs 인라인 (trim 0/2 × 가변창 × 108케이스) | **불일치 0건** |
| `closed_upto` None ↔ `i < kf_window` (i=0..119) | **완전 일치** |

### 5-3. 백업본(교체 전) 대비 실데이터 동일성 — **교체한 5개 함수 전부 일치**
| 함수 | 데이터 | 결과 |
|---|---|---|
| `run_chandelier_live_replica` | 주간 2026-01-01~ (PROD_KW) | **동일** · 425거래 |
| `run_kalman_live_replica` | 주간 2026-05-01~ | **동일** · 427거래 |
| `run_kalman_live_replica_oc` | 주간 2026-05-01~ | **동일** · 396거래 |
| `run_kalman_night_replica` | A05608 2026-06-01~ | **동일** · 136거래 |
| `run_production_rolling_k` | 주간 2026-06-01~ | **동일** · 158거래 |
| `run_kalman_breakout_fair` | (무수정) | 미실행 — 코드 무변경 |

> 이 5-3은 **국소 스모크**이지 골든 대조가 아니다. 62조합 전수 대조는 다음 세션.

---

## 6. 골든 조합 보강 — `scratch/golden_kalman_combos_20260812.py`

B단계에서 base와 바이트 단위 동일해 회귀를 못 잡던 3조합을 **실제로 분기를 타는 조합으로 교체**했다.
총 조합수는 **62개 그대로**(이름 유지, 내용만 교체).

### ① `D_hard_stop`
- **전**: `_ch(hard_stop_enabled=True, hard_stop_se_mult=1.5)`
- **원인**: hard_stop 분기가 `if profit_lock_enabled:` 블록 **안쪽 elif**(kalman_backtester.py:1187/1191, 1216/1220)라 profit_lock 없이는 도달 불가 (발견 목록 6번)
- **후**: `_ch(profit_lock_enabled=True, profit_lock_trigger_pt=8.0, profit_lock_mult=0.10, profit_lock_be_buffer_pt=1.0, hard_stop_enabled=True, hard_stop_se_mult=1.5)`
- `profit_lock_be_move_trigger_pt` 기본값이 `None`이므로 `elif hard_stop_enabled`가 매 봉 평가됨. 기존 `D_profit_lock`과의 차이가 정확히 hard_stop 하나가 되어 격리도 확보.

### ② `D_margin_rate`
- **전**: `_ch(margin_rate=0.198)`
- **원인**: `dynamic_sizing=False`에서 계약수는 `max(1, min(15, safe_budget // margin_per))`로 1회만 결정. PROD_KW(`point_value=250,000`)에서는 0.10이든 0.198이든 몫이 0 → `max(1, ·)` = **둘 다 1계약**
- **후**: `_ch(**DYN, margin_rate=0.198)` (`DYN` = `dynamic_sizing=True, point_value=50_000, max_contracts=15`)
- 매 진입마다 `_margin_per(fill)`이 재평가되어 요율 차이가 계약수에 반영됨

### ③ `D_signal_only_5min`
- **전**: `_ch(signal_only_on_5min=True)`
- **원인**: 게이트가 `minute % 5 == 0`인데 `futures_ohlcv`(5분봉)는 모든 봉의 분이 5의 배수 → 항상 참 → **완전한 no-op**
- **후**: `_ch(signal_only_on_5min=True, kf_window=200, std_window=100, session_range_cap_min_bars=30)`, `table='futures_ohlcv_1m'`
- 창 파라미터는 기존 `I_1min_scaled`와 동일한 1분봉 환산값 사용

### base 골든 **재캡처 필요 목록** (다음 세션)

```
D_hard_stop
D_margin_rate
D_signal_only_5min
```

- 나머지 59조합의 `scratch/_golden_kalman/base/*.json`은 **유효하며 재캡처 불필요**
- 위 3개는 파라미터가 바뀌었으므로 기존 base JSON을 폐기하고 **A단계 백업본(`.bak_C_20260812_172302`)으로 base를 다시 떠야** new와 대조 가능
- 재캡처 후 base 누적 거래수는 51,233건에서 달라진다 (특히 `D_signal_only_5min`은 1분봉이라 크게 증가 예상)

---

## 7. 다음 세션 체크리스트

1. `.bak_C_20260812_172302`로 위 **3조합 base 재캡처**
2. 현행 코드로 **62조합 new 골든 캡처**
3. base ↔ new **바이트 단위 대조** (59조합은 기존 base 사용)
4. 불일치 시 §3의 세 보존 지점(야간 `[i]` / fair ddof=1 / 워밍업 가드)부터 확인
