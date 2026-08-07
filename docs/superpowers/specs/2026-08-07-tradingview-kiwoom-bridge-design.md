# TradingView ↔ 키움 주간선물 자동매매 연동 설계

- 작성일: 2026-08-07
- 상태: 설계 승인 대기

## 1. 배경 / 문제 정의

`tradingview_bridge/` 모듈은 TradingView 얼럿을 수신해 세후/실체결 비용을 평가하고 로그로
남기는 부분(`webhook_receiver.py`, `cost_evaluator.py`, `kiwoom_executor.py`)까지는
구현돼 있지만, 실제 키움 주문 전송 경로는 미완성이다.

- `kiwoom_executor.py:79-91`의 `if live_trading:` 분기는 `sys.path.append`만 하고 실제
  주문 함수 호출이 없는 플레이스홀더다. `config.json`도 `enable_live_trading: false`,
  `simulation_mode: true`로 현재는 시뮬레이션만 동작한다.
- 실제 라이브 주문 로직은 `era/era_order_manager.py`가 이미 보유하고 있다(Kiwoom OpenAPI+
  QAxWidget, 32비트 Python 전용). `webhook_receiver.py`(FastAPI, 통상 64비트)에서 직접
  import해 호출할 수 없다 — 프로세스 경계를 넘는 연동이 필요하다.
- **더 중요한 문제**: ERA는 이미 그 자체로 자율 전략(칼만/샹들리에/파라볼릭 SAR 등,
  `futures_strategy_type`이 항상 어떤 값이든 선택되어 있고 "끄기" 옵션이 없음)을 내장해
  주간선물(코드 `10100000`)에 상시 자동매매 중이다(`era_order_manager.py:4580-4718`
  `_execute_futures_direct` 호출부). TradingView 신호를 단순히
  `_poll_futures_signals`(TCA 수동명령 처리용 보조 경로, `era_order_manager.py:5984`)에
  얹으면, 이미 ERA 자체전략이 들고 있는 포지션과 물리적으로 충돌한다 — `poll_signals`는
  포지션 존재/방향을 확인하지 않고 무조건 신규주문(`ord_kind=1`)을 쏘기 때문이다.

## 2. 목표

- TradingView Pine 전략의 매매 신호(진입/청산)를 키움 주간선물 실계좌 주문으로 안전하게
  연결한다.
- ERA의 기존 자체전략(칼만/샹들리에 등, 이미 튜닝된 파라미터)과 TradingView 전략을
  **간단한 설정 하나로 선택**할 수 있게 한다 — 동시에 같은 포지션을 두 "두뇌"가 건드리는
  일이 없어야 한다.
- TradingView가 담당하는 동안에도 실제 체결 포지션은 ERA의 기존 안전장치(서킷브레이커,
  자금관리 30% 캡, 월간 MDD 킬스위치, 장마감 강제청산)의 보호를 그대로 받는다.
- TradingView와의 연결이 끊겨도(ngrok 장애, 웹훅 서버 다운 등) 포지션이 무방비로 방치되지
  않는다.

## 3. 범위 밖 (Non-goals)

- **주식 연동**: 이번 통합은 선물 전용이다. `_poll_stock_signals` 경로는 건드리지 않는다.
- **야간선물**: TradingView 전략은 주간선물(`10100000`)만 대상으로 한다. 야간선물
  (`10500000`)은 이번 통합에서 완전히 제외 — ERA 자체전략이 지금처럼 그대로 운용한다.
  `config/active_strategy.json`이 애초에 "주간선물만 오버라이드"하는 파일이라
  (`era_order_manager.py:1283` 주석) 이 제약과 자연스럽게 맞는다.
- **라이브 주문 즉시 활성화**: 이번 구현은 구조(신호 큐잉 + ERA 처리)까지 만들고,
  `config.json`의 `enable_live_trading`은 계속 `false`로 유지한다. 시뮬레이션으로 먼저
  검증한 뒤 별도로 라이브 전환을 결정한다.

## 4. 신호 소스 설정

당초 `config/active_strategy.json`에 키 하나만 얹는 안을 검토했으나, 이 파일은
`bqa/batch_optimizer.py:227-239`가 매주 자동 실행 후 **완전히 새 딕셔너리로 덮어쓴다**
(`best_k`/`stop_loss_pt`/`take_profit_pt`/`top_strategies`만 포함). 여기에 `signal_source`를
두면 주말 최적화가 돌 때마다 조용히 사라지고 기본값으로 되돌아간다 — TradingView로
전환해둔 상태를 사용자 모르게 되돌리는 사고 지점이 되므로 채택하지 않는다.

대신 신규 소형 파일 `config/signal_source.json`을 둔다. batch_optimizer.py는 이 파일을
전혀 알지 못하므로 건드릴 일이 없다.

```json
{ "signal_source": "ERA_STRATEGY" }
```

- `"ERA_STRATEGY"` (기본값, 하위호환): 지금처럼 ERA 자체전략이 진입·청산 모두 담당. 변경 없음.
- `"TRADINGVIEW"`: 주간선물 신규 진입에서 ERA 자체전략(`_execute_futures_direct` 진입 판단
  블록)이 멈추고, TradingView Pine 알림이 진입·청산을 모두 담당. 단, 7절의 워치독이 연결
  끊김 시 방어 개입한다.

`load_config()`(`era_order_manager.py:1123`)와 `_on_morning_prep_finished`
(`era_order_manager.py:2721`)에서 `active_strategy.json`을 읽는 것과 같은 지점에 이 파일도
함께 읽는 코드를 몇 줄 추가한다 — 재시작/장준비 시 반영되는 동작(3절 non-goal과 일치)은
동일하게 유지하되, 자동 최적화 파이프라인의 덮어쓰기 영향권 밖에 둔다.

`.gitignore:15`에 `config/active_strategy.json`이 이미 PC별(선물/주식) 고유 설정 유지
목적으로 등록돼 있다. `config/signal_source.json`도 같은 이유로 `.gitignore`에 추가한다 —
선물 PC(NUCBOX_M8)에서만 의미 있는 설정이라 저장소에 커밋해 양쪽 PC가 공유할 필요가 없다.

### 4.1 전환 명령 `!소스전환` (신규 텔레그램 명령)

`load_config()`는 프로세스 시작(`__init__`)과 매일 아침 장전준비 완료
(`_on_morning_prep_finished`, `era_order_manager.py:2721`) 시점에만 호출된다 — 매 폴링
사이클마다 재로드되는 게 아니므로, `config/signal_source.json` 값만 고쳐서는 당일 장중에
반영되지 않는다. 파일을 직접 편집하게 두는 대신, TCA에 전용 명령을 신설해 "설정 저장 +
즉시 반영"을 한 번에 처리한다.

- **명령**: `!소스전환 트레이딩뷰` / `!소스전환 자체전략`
- **동작**:
  1. `config/signal_source.json`에 `TRADINGVIEW` 또는 `ERA_STRATEGY` 값을 기록한다.
  2. 곧바로 `!재연동`/`!시스템재시작`(`tca_controller.py:979-998`)과 **동일한 안전 재시작
     시퀀스**를 내부적으로 호출한다 — 작업 스케줄러(`AMATS AutoStart`) 일시 비활성화 →
     기존 ERA/키움 프로세스 정리 → 키움 서버 세션·소켓 쿨타임 60초 대기 → 스케줄러 태스크
     (`AMATS ERA Reconnect`) 트리거로 재기동. 새 핸들러를 따로 구현하지 않고 `!재연동`
     처리 블록을 함수로 뽑아 공유한다.
  3. `_open_positions_warning()`(`tca_controller.py:260-275`)을 그대로 재사용해 포지션
     보유 중 실행 시 "ERA가 꺼져 있는 동안 손절/익절/트레일링이 전혀 동작하지 않는다"는
     경고를 응답 메시지에 자동으로 포함한다 — `!재연동`이 이미 이 경고를 붙이는 것과 같은
     이유(재시작 쿨타임 동안 감시 공백)가 여기도 그대로 적용되기 때문이다.
- **결과**: 명령 한 번으로 그 자리에서 전환이 완료된다. 다만 포지션을 보유한 채로
  실행하면 재시작 쿨타임(약 1분) 동안 해당 포지션이 무방비 상태가 되므로, 이 시간대에
  실행하는 걸 권장하지 않는다는 점을 경고 메시지로 알린다(강제 차단은 하지 않음 —
  `!재연동`도 동일하게 경고만 하고 차단하지는 않는 기존 관례를 따른다).
- **`!도움말`**(`tca_controller.py:1584-1660`)의 `control_items`(선물 항목,
  `show_futures` 블록)에 `!소스전환` 항목을 추가한다.

파일만 수동으로 고쳐두는 경로(재시작 없이 다음 거래일 장전준비 때 자동 반영)도 여전히
유효하다 — 급하지 않을 때는 `!소스전환` 없이 파일만 바꿔두면 재시작 부담 없이 다음날
자연스럽게 전환된다. 다만 기본 운영 방법은 `!소스전환` 명령이다.

참고(이번 스코프 밖, 기록만): `!계약수량` 명령의 텔레그램 안내 문구("다음 사이클에
자동으로 로드하여 즉시 적용")는 위 재로드 시점과 맞지 않아 보인다 — `config_local.json`도
동일한 `load_config()` 경로를 타므로 실제로는 재시작/장전준비 전까지 반영되지 않는다.
`!소스전환`은 이 문제를 애초에 "재시작을 실제로 트리거"하는 방식으로 설계해 피해간다.

## 5. 아키텍처 & 데이터 흐름

```
TradingView Pine (주간선물 전략)
  매매신호: {signal_type: LONG_ENTER|SHORT_ENTER|LONG_EXIT|SHORT_EXIT, code:"10100000", price}
  하트비트: {type: heartbeat, code:"10100000"}   ← 매 봉마감마다 별도 전송
        │  HTTPS POST (ngrok)
        ▼
webhook_receiver.py  /api/tv-alert
  - secret_key 검증(기존)
  - signal_type 4종 검증, code=="10100000" 검증 (신규, 야간 코드 오입력 방지)
  - signal_type → BUY/SELL 매핑 후 cost_evaluator.evaluate_order() 호출 (세후 비용 평가, 기존 로직)
        ▼
kiwoom_executor.py
  - tv_bridge.db에 감사로그 기록 (기존, era_signal_id 컬럼 추가)
  - 매매신호: futures_data.db.signals 에 INSERT (status='PENDING')      ← 신규
  - 하트비트: futures_data.db.bridge_heartbeat 갱신                      ← 신규
        │
        ▼ (era_order_manager.py 기존 폴링 주기, 수정 없음)
era_order_manager.py
  - 주간 진입 판단 블록(_execute_futures_direct 호출부): signal_source=="TRADINGVIEW"면
    조기 return으로 자체전략 신규 진입 skip
  - _poll_futures_signals: signal_source=="TRADINGVIEW"일 때만 code=10100000 신호 처리
    · 진입: 기존 서킷브레이커 체크 통과 후 처리 (기존 로직 재사용)
    · 청산: 실제 포지션 존재 확인 후에만 실행 (신규 가드)
  - 하트비트 워치독(신규, 30초 주기 QTimer — 기존 `_futures_eod_fast_sync_tick`과 동일 패턴):
    신호소스 TRADINGVIEW + 포지션 보유 + 하트비트 15분 이상 유실 → 7절 fallback 발동
```

## 6. 신호 처리 세부 규칙

### 6.1 signal_type ↔ 주문 방향 매핑

ERA의 기존 매핑(`era_order_manager.py:6073-6080`)을 그대로 따른다.

| signal_type | 의미 | cost_evaluator용 action |
|---|---|---|
| LONG_ENTER | 롱 진입 | BUY |
| SHORT_ENTER | 숏 진입 | SELL |
| LONG_EXIT | 롱 청산 | SELL |
| SHORT_EXIT | 숏 청산 | BUY |

`cost_evaluator.py:39`의 `action.upper() == "BUY"` 분기 판정 때문에, signal_type 원문
(`"LONG_ENTER"` 등)을 그대로 넘기면 매수 신호가 매도로 오판되어 슬리피지 방향이 뒤집힌다.
`webhook_receiver.py`에서 위 표대로 매핑한 BUY/SELL을 `cost_evaluator`에 넘기고,
`kiwoom_executor`에는 원본 signal_type을 그대로 전달해 `futures_data.db.signals`에 저장한다
(ERA가 이해하는 값은 signal_type 원문이어야 하므로).

### 6.2 EXIT 신호 안전 가드 (신규)

`_poll_futures_signals`는 현재 EXIT 신호도 포지션 존재 여부와 무관하게 신규 매도/매수
주문을 쏜다. 포지션이 없는데 TradingView가 `LONG_EXIT`을 보내면(연결 재개 후 중복 발송,
타이밍 오차 등) 의도치 않은 반대 포지션이 새로 생긴다. TradingView 경로로 들어온 EXIT
신호는 `self.futures_positions`에 해당 pos_key 포지션이 실제로 있을 때만 실행하고, 없으면
`SKIPPED_NO_POSITION` 상태로 기록한다.

### 6.3 signal_source 불일치 시 처리

`signal_source`가 `ERA_STRATEGY`인 상태에서 TradingView 발 신호가 들어오는 경우(예: 설정
전환을 깜빡하고 TradingView 얼럿을 계속 켜둔 채로 방치)도 명시적으로 처리한다.
`_poll_futures_signals`는 이 경우 신호를 조용히 무시하지 않고 `status`를
`SKIPPED_SOURCE_NOT_ACTIVE`로 기록한다 — 침묵 처리하면 "신호가 도착했는지"와 "받아들여
지지 않았는지"를 나중에 구분할 수 없어 11절 마지막 리스크(오설정 방치)를 조기에 발견하기
어려워지기 때문이다.

## 7. 하트비트 워치독 & Fallback

`futures_data.db`에 `bridge_heartbeat(session_code TEXT PRIMARY KEY, last_seen_ts TEXT)`
테이블을 신설한다. 별도 DB를 새로 폴링시키지 않고 ERA가 이미 열고 있는 DB를 그대로 쓴다.

- **1단계 (15분 = 5분봉 하트비트 주기의 3배 무응답)**: signal_source가 TRADINGVIEW인 채로
  포지션이 열려 있는데 하트비트가 끊기면 fallback 발동.
  - 이 포지션에 대해 ERA의 기존 트레일링/손절 로직을 인수시킨다.
  - **주의**: 트레일링 로직이 참조하는 `futures_day_entry_price` / `futures_day_peak` /
    `futures_day_entry_atr` 등은 원래 `_execute_futures_direct` 진입 시에만 세팅된다
    (`era_order_manager.py:4622-4632`). TradingView가 연 포지션은 이 경로를 타지 않으므로
    비어있거나 이전 거래의 낡은 값일 수 있다 — fallback 발동 즉시 실계좌 동기화값
    (`self.futures_positions[pos_key]['price']`, 브로커 평단가)으로 진입가를 재초기화하고
    `futures_day_peak`도 현재가로 리셋한다.
  - 신규 진입은 재개하지 않는다 — fallback은 방치된 포지션 방어 전용이며, ERA 자체전략이
    그 세션의 신규 매매 주체로 복귀하는 것이 아니다.
  - `notifier.py`(기존 텔레그램 알림 모듈)로 즉시 알림.
- **2단계 (60분 무응답 지속)**: 트레일링 관리만으로는 불충분하다고 보고 무조건 강제청산한다
  (기존 "장마감 전 무조건 강제 청산", `era_order_manager.py:4082-4100`과 동일한 철학).
- 포지션이 flat이 되면 fallback 플래그를 해제하고, 다음 진입부터 signal_source 설정에 따라
  정상 라우팅으로 복귀한다.

## 8. 컴포넌트별 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `tradingview_bridge/webhook_receiver.py` | `code` 필드 추가(주간 `10100000` 고정 검증), `signal_type` 4종 검증, heartbeat 타입 분기 처리, action→BUY/SELL 매핑 후 cost_evaluator 호출 |
| `tradingview_bridge/kiwoom_executor.py` | `_dispatch_to_era()`(signals INSERT), `_update_heartbeat()`(bridge_heartbeat 갱신) 신규 메서드. `tv_alerts` 테이블에 `era_signal_id` 컬럼 추가 |
| `config/signal_source.json` (신규 파일) | `signal_source` 값 저장. batch_optimizer.py 영향권 밖 |
| `era/era_order_manager.py` | ① `load_config()`/`_on_morning_prep_finished`에서 `signal_source.json` 로드 추가 ② 주간 진입 블록에 signal_source 가드 ③ `_poll_futures_signals`에 소스 필터 + EXIT 포지션 존재 가드 ④ 하트비트 워치독 QTimer + fallback 로직(진입가/피크 재초기화 포함) 신규 |
| `tca/tca_controller.py` | ① `!소스전환 트레이딩뷰`/`!소스전환 자체전략` 명령 핸들러 신규 — `signal_source.json` 기록 후 `!재연동` 재시작 시퀀스 호출(공용 함수로 추출) + `_open_positions_warning()` 재사용 ② `!도움말`의 `control_items`(선물 항목)에 명령 설명 추가 |

**변경 없음**: 야간선물 로직 전체, `_poll_stock_signals`, ERA의 트레일링/손절/서킷브레이커/
자금관리/EOD청산 로직 자체(재사용만 함), `kiwoom_executor.py`의 시뮬레이션 로그 기록 로직,
`!재연동`의 기존 재시작 시퀀스 자체(그대로 호출만 함).

## 9. 안전장치 요약

1. 시뮬레이션 모드로 먼저 검증 (`enable_live_trading=false` 유지, 8절 구현 완료 후에도
   기본값은 그대로 둔다).
2. 서킷브레이커·자금관리·EOD강제청산: 출처(ERA 자체전략/TradingView) 무관 항상 적용.
3. EXIT 신호는 실제 포지션 존재 확인 후에만 실행 (6.2절).
4. 하트비트 워치독 2단계 방어 (15분: 트레일링 인수 / 60분: 강제청산).
5. fallback 발동·해제 시 텔레그램 즉시 알림.
6. code 필드는 주간(`10100000`)만 허용 — 야간 코드가 잘못 들어오면 즉시 거부.
7. `!소스전환` 명령은 포지션 보유 중 실행 시 `_open_positions_warning()`으로 재시작 공백
   경고를 자동 표시(차단은 하지 않음, `!재연동`과 동일한 기존 관례).

## 10. 검증 계획

- `tradingview_bridge/test_tv_bridge.py`에 시뮬레이션 모드 테스트(기존 유지) + 라이브 모드
  mock 테스트 추가: 실제 `futures_data.db`에 신호가 올바른 스키마로 INSERT되는지 검증
  (ERA 프로세스 자체는 실행하지 않고 DB 상태만 확인).
- signal_type→action 매핑 단위 테스트 (6.1절 표 4가지 케이스 모두).
- EXIT 포지션 가드 단위 테스트: 포지션 없는 상태에서 EXIT 신호 → `SKIPPED_NO_POSITION`
  확인.
- 하트비트 워치독은 `last_seen_ts`를 과거로 강제 조작한 뒤 워치독 tick을 수동 호출해
  fallback 진입가/피크 재초기화 및 상태 전이(1단계→2단계)를 검증.
- 수동 통합 검증: `tv_mcp_server.py`의 `trigger_simulated_alert`류 함수로 실제
  `futures_data.db`에 테스트 신호를 넣고, ERA를 모의투자(`environment != "live"`) 상태로
  기동해 신호가 폴링되어 `EXECUTED`로 바뀌는지 눈으로 확인.
- [[백테스트는 항상 보고서로 남길 것]] 메모에 따라, 라이브 전환 전 시뮬레이션 검증 결과는
  `.md` 보고서로 정리하고 커밋한다.
- `!소스전환` 명령 수동 검증: (a) `signal_source.json`이 올바른 값으로 기록되는지,
  (b) `!재연동` 재시작 시퀀스가 실제로 트리거되는지(스케줄러 태스크 호출 로그 확인),
  (c) 포지션 보유 상태를 흉내낸 상태에서 실행 시 `_open_positions_warning()` 경고 문구가
  응답에 포함되는지, (d) `!도움말` 출력에 새 명령이 나타나는지.

## 11. 리스크

- **32비트/64비트 프로세스 경계**: SQLite 파일 큐(WAL 모드)로 우회하므로 직접적인 문제는
  없으나, 두 프로세스가 동시에 같은 DB에 쓸 때의 락 경합은 WAL 모드가 이미 완화한다
  (`era_order_manager.py`가 이미 WAL로 열고 있음, `_poll_futures_signals` 확인됨).
- **fallback 인수 시 진입가 재초기화 누락**: 7절에서 지적한 스냅샷 재초기화를 빠뜨리면
  트레일링 스탑이 엉뚱한 기준가로 작동해 오히려 위험해진다 — 구현 시 반드시 확인.
- **하트비트 임계치(15분/60분)의 적정성**: 5분봉 기준 3배/12배로 잡았으나 실제 TradingView
  얼럿 전송 지연이나 ngrok 재연결 시간 특성에 따라 오탐(정상인데 fallback 발동)이나
  미탐(끊겼는데 늦게 발동)이 있을 수 있다 — 초기 운용 중 실측치로 조정 필요.
- **signal_source 오설정 상태로 방치**: `TRADINGVIEW`로 설정해두고 TradingView 쪽 얼럿을
  실제로는 설정하지 않으면 주간선물이 아예 매매되지 않는 상태로 조용히 방치될 수 있다.
  export_status() 등 기존 상태 보고 경로에 현재 signal_source 값을 노출하는 것을 구현 시
  검토한다.
