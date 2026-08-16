# Trading Summary for 2026-06-23

## 1. System Status (tca/system_status.json)
- **Environment**: mock
- **Trading Mode**: both
- **Stock Account**: 8128131511
- **Futures Account**: 7035625231
- **Stock Balance**: 9,651,579 KRW
- **Futures Balance**: 26,909,530 KRW
- **Daily Realized Loss**: 9,900.0 KRW
- **Monthly Realized Loss**: 9,900.0 KRW

### Stock Positions (Portfolio)
No open stock positions.

### Futures Positions
No open futures positions.

### Futures Strategy Config
- strategy_type: kalman
- K: 0.15
- prev_range: 74.6400000000001
- stop_loss_pt: 5.0
- take_profit_pt: 10.0
- day_entry_price: 0.0
- night_entry_price: 0.0
- trend_direction: UP
- night_trend_direction: NEUTRAL
- std_error: 2.7465582992337074
- night_std_error: 0.5
- kf_sl_mult: 3.4

*Last Updated: 2026-06-23 18:38:51*

## 2. Stock Signals & Reports (unified_data.db)

### Today's Stock Signals
| ID | Code | Name | Strategy | Price | Open Price | Time | Status |
|---|---|---|---|---|---|---|---|
| 198 | 053610 | 프로텍 | DAY | 90,500 | 85,200 | 2026-06-23 00:14:10 | EXECUTED |

### Today's Research Reports (Top 10)
| Code | Name | Strategy | Score | Time |
|---|---|---|---|---|
| 217190 | 제너셈 | DAY | 92 | 2026-06-23 08:51:15 |
| 053160 | 프리엠스 | DAY | 91 | 2026-06-23 08:50:53 |
| 251630 | 브이원텍 | DAY | 89 | 2026-06-23 08:51:31 |
| 307950 | 현대오토에버 | DAY | 89 | 2026-06-23 08:52:01 |
| 241790 | 티이엠씨씨엔에스 | DAY | 88 | 2026-06-23 08:51:21 |
| 001420 | 태원물산 | DAY | 86 | 2026-06-23 08:50:32 |
| 0004V0 | 엔비알모션 | DAY | 85 | 2026-06-23 08:50:22 |
| 255440 | 야스 | DAY | 83 | 2026-06-23 08:51:44 |
| 000430 | 대원강업 | DAY | 76 | 2026-06-23 08:50:19 |
| 253590 | 네오셈 | DAY | 73 | 2026-06-23 08:51:40 |

### Today's Balance History
- **Stock Total**: 9,665,458.0 KRW
- **Futures Total**: 12,236,005.0 KRW
- **Combined Total**: 21,901,463.0 KRW

## 3. Futures Signals (futures_data.db)
No futures signals found for today.

## 4. Key Log Entries for Today
No significant log entries matching filters found for today. Last 30 lines of log instead:
```
[ERA 야간선물] 18:29 세션 종료  상태 초기화
[ERA 야간선물] 18:29 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:30 세션 종료  상태 초기화
[ERA 야간선물] 18:30 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:31 세션 종료  상태 초기화
[ERA 야간선물] 18:31 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:32 세션 종료  상태 초기화
[ERA 야간선물] 18:32 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:33 세션 종료  상태 초기화
[ERA 야간선물] 18:33 세션 시작 대기  상태 초기화

=>  [선물 실계좌 동기화 TR 요청] 시각: 18:34:10
[Kiwoom Msg] [100000] 모의투자 조회완료

=>  [선물 계좌 자금]
- 선물 예수금: 26,909,530원
- 20% 캡 적용 가용금액: 5,381,906원
[Kiwoom Msg] [571578] 모의투자 해당조회내역이 없습니다.

=>  [기존 선물 포지션 실계좌 연동]
[ERA 야간선물] 18:34 세션 종료  상태 초기화
[ERA 야간선물] 18:34 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:35 세션 종료  상태 초기화
[ERA 야간선물] 18:35 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:36 세션 종료  상태 초기화
[ERA 야간선물] 18:36 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:37 세션 종료  상태 초기화
[ERA 야간선물] 18:37 세션 시작 대기  상태 초기화
[ERA 야간선물] 18:38 세션 종료  상태 초기화
[ERA 야간선물] 18:38 세션 시작 대기  상태 초기화
```