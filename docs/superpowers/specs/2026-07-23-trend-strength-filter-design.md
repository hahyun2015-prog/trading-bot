# 횡보장 회피 필터 통합 설계

- 작성일: 2026-07-23
- 상태: **폐기됨 (2026-07-23)** — 7절 검증 계획대로 백테스트한 결과 baseline 대비 전 구간·전
  파라미터 조합에서 성과가 나빠 도입하지 않기로 결정. 상세 결과는
  [`횡보장_회피_필터_백테스트_비교보고서.md`](../../../횡보장_회피_필터_백테스트_비교보고서.md) 참고.
  실험용 코드는 검증 후 원상복구했으며 실전 코드에는 반영되지 않았다.

## 1. 배경 / 문제 정의

선물 매매 엔진(`era/era_order_manager.py`)과 그 백테스트 재현체(`bqa/kalman_backtester.py`)는
"장기 칼만 추세 필터"라는 이름으로 진입 방향(UP/DOWN/NEUTRAL)을 걸러내고 있지만, 실제 구현은
아래 한 줄이 전부다 (4곳에 동일 코드 중복):

```python
slope = kf_long[-1] - kf_long[-2]          # 15분(주간)/60분(야간)봉 칼만 스무딩 값의 "직전 1-step" 차이
trend = "UP" if slope > 0.01 else ("DOWN" if slope < -0.01 else "NEUTRAL")
```

- `era/era_order_manager.py:1060-1067` (주간/야간 공용, `update_kalman_targets`)
- `bqa/kalman_backtester.py:709-710` (`run_kalman_live_replica`)
- `bqa/kalman_backtester.py:1041-1042` (`run_kalman_night_replica`)
- `bqa/kalman_backtester.py:1284-1285` (`run_kalman_live_replica_oc`)

문제점:
1. **N봉에 걸친 추세를 보지 않는다** — 직전 두 값만 비교하는 1-step delta라 노이즈에 취약하다.
2. **변동성 정규화가 없다** — `0.01pt`라는 고정 절대값 임계치라, 시장 변동성 국면과 무관하게 항상
   같은 기준으로 UP/DOWN을 가른다. 사실상 NEUTRAL(횡보 판정)이 거의 나오지 않는다.
3. **동일 로직이 4곳에 중복**되어 있어 향후 수정 시 어긋날 위험이 있다(라이브/백테스트 싱크는
   이미 수동 관리 구조 — `kalman_backtester.py` 주석에 "era_order_manager.py 실전 로직을 최대한
   동일하게 재현"이라 명시됨).

별도로 존재하는 BB 스퀴즈 필터(`era_order_manager.py:893-929, 4193-4257`)는 `futures_strategy_type
== "parabolic_sar"`일 때만 게이트가 걸리는데, 현재 실전 전략은 `config.json`의
`futures_strategy_type: "chandelier"`라 **사실상 비활성 상태**다.

## 2. 목표

위 4곳의 1-step delta 판정을, 이미 계산 중인 `kf_long` 배열을 그대로 재사용하는 **N봉 순추세
강도 지표(Efficiency Ratio 방식) 하나**로 교체한다. 새 지표가 "방향(부호) + 강도(변동성 대비
정규화)"를 동시에 표현하므로, 별도 역할이던 BB 스퀴즈 필터도 흡수해 제거한다.

## 3. 범위 밖 (Non-goals)

- `market_regime_monitor.py`(일별 ADX+BBW 전략 자동전환)는 현재 가동 중이 아니며, 이번 통합
  대상에서 제외한다. 별도 트랙으로 남긴다.
- `enable_reentry_filter`(재진입 차단, 0.35×ATR 근접 진입 금지)는 이미 별도 백테스트
  (`휩소방지_필터_백테스트_비교보고서.md`)로 검증된 독립 메커니즘이라 건드리지 않는다.

## 4. 알고리즘

`update_kalman_targets` 및 3개의 `kalman_backtester.py` 함수에서 이미 만들고 있는
`kf_long` (칼만 스무딩된 15분/60분봉 종가 시퀀스) 배열을 그대로 사용한다.

```python
N = trend_strength_n           # 기본 후보값 8 (튜닝 대상, 아래 6절 참고)
threshold = trend_strength_th  # 기본 후보값 0.35 (튜닝 대상)

if len(kf_long) >= N + 1:
    net_move = kf_long[-1] - kf_long[-1 - N]
    path_len = sum(abs(kf_long[i] - kf_long[i-1]) for i in range(-N + 1, 1))  # 최근 N구간 총 변동폭
    strength = net_move / path_len if path_len > 1e-9 else 0.0

    if strength > threshold:
        trend = "UP"
    elif strength < -threshold:
        trend = "DOWN"
    else:
        trend = "NEUTRAL"
else:
    trend = "NEUTRAL"   # 데이터 부족 시 안전측(횡보 취급)
```

- `strength` 범위는 항상 [-1, 1]. 1에 가까울수록 "한 방향으로 노이즈 없이 밀고 간" 순수 추세,
  0에 가까울수록 오르내림만 반복하는 횡보.
- `path_len`이 0(완전 무변동 구간)일 때 0으로 나누는 걸 방지하는 `1e-9` 가드 포함.

## 5. 적용 범위 / 코드 변경 지점

| 파일 | 위치 | 변경 내용 |
|---|---|---|
| `era/era_order_manager.py` | 1060-1067 (`update_kalman_targets`) | 1-step delta → N봉 strength 계산으로 교체 |
| `bqa/kalman_backtester.py` | 709-710 (`run_kalman_live_replica`) | 동일 교체 |
| `bqa/kalman_backtester.py` | 1041-1042 (`run_kalman_night_replica`) | 동일 교체 |
| `bqa/kalman_backtester.py` | 1284-1285 (`run_kalman_live_replica_oc`) | 동일 교체 |
| `era/era_order_manager.py` | 893-929, 4193-4257 (BB 스퀴즈 필터) | 제거 (신규 지표가 역할 흡수) |

4곳 모두 같은 계산식을 쓰므로, 중복을 줄이기 위해 공용 헬퍼 함수(예:
`_calc_trend_strength(kf_long, n, threshold)`)로 뽑아내는 걸 구현 단계에서 검토한다. 다만
`era_order_manager.py`와 `bqa/kalman_backtester.py`는 별도 모듈/실행 경로라 import 구조를
먼저 확인해야 한다 (공용 모듈로 뽑을지, 각자 파일에 동일 함수를 유지하되 리뷰 시 diff로
동일성만 보장할지는 구현 단계 판단 사항).

## 6. 파라미터 / Config

`config/config.json`의 `futures_settings`에 다음 키 추가:

```json
"trend_strength_n": 8,
"trend_strength_threshold": 0.35
```

기존 `0.01` 하드코딩 임계값과 1-step 비교는 제거한다. 기본값 8/0.35는 구현 시작점이며,
7절의 백테스트로 그리드서치해 확정한다.

## 7. 검증 계획

- `bqa/batch_optimizer.py`의 그리드서치 프레임워크로 `trend_strength_n` ×
  `trend_strength_threshold` 조합을 스캔한다.
- **레짐 과최적화 방지** ([[백테스트 레짐 과최적화 주의]] 메모 반영): 최근 30거래일 단일
  구간만으로 판단하지 않는다. 최소 아래 두 구간에서 각각 성과(승률/손익/거래횟수)를 비교해
  양쪽 다 개선되거나 최소한 악화되지 않는 조합만 채택한다.
  1. 전체 가용 기간(가능한 한 길게)
  2. 최근 1개월(현재 레짐)
- 비교 기준선(baseline)은 "현재 1-step delta + 0.01 임계값" 그대로 유지한 케이스로 잡는다.
- 통과 기준: 두 구간 모두에서 (a) 순손익 저하 없음, (b) MDD 악화 없음, (c) 거래 횟수가
  과도하게 줄어 통계적 유의성을 잃지 않을 것.

## 8. 리스크

- **라이브/백테스트 불일치**: 4곳 수동 동기화 구조상, 한 곳만 고치고 나머지를 놓치면 라이브와
  백테스트가 다시 어긋난다. 구현 시 4곳 모두 동시 diff로 확인한다.
- **파라미터 민감도**: N/threshold가 특정 구간에만 맞춰져 있으면 [[백테스트 레짐 과최적화 주의]]에서
  지적된 것과 같은 함정에 빠질 수 있다 — 7절의 2구간 교차검증으로 방어한다.
- **NEUTRAL 급증 가능성**: 기존 로직은 사실상 NEUTRAL이 거의 안 나왔는데, 새 로직은 진짜 횡보를
  잡아내므로 거래 횟수가 유의미하게 줄어들 수 있다. 이는 의도된 동작이지만, 거래량이 과도하게
  줄어 수익 기회 자체가 사라지는지 7절 백테스트로 확인 필요.
