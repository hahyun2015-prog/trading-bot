# AMATS 설치 및 운용 가이드

## 1. 폴더 구조

```
AI_T_Agent/
├── config/
│   └── config.json          ← 핵심 설정 (환경, 텔레그램, 경로 등)
├── era/                     ← 주문/리스크 엔진 (Kiwoom 연동, 32비트 전용)
├── sta/                     ← 실시간 스크리닝 (Kiwoom 연동, 32비트 전용)
├── tca/                     ← 텔레그램 관제 에이전트 (32/64비트 무관)
├── rsa/                     ← AI 종목분석 에이전트 (32/64비트 무관)
├── bqa/                     ← 백테스트/최적화 엔진 (32/64비트 무관)
├── venv32/                  ← 32비트 Python 가상환경 (setup_env.bat으로 생성)
├── futures_data.db          ← 선물 OHLCV 데이터
├── unified_data.db          ← 주식 신호/분석 통합 DB
├── setup_env.bat            ← 최초 1회 실행: venv32 자동 생성
├── run_era.bat              ← ERA 주문엔진 실행
├── run_tca.bat              ← TCA 텔레그램 관제 실행
├── run_sta.bat              ← STA 스크리너 실행
└── run_bqa.bat              ← BQA 백테스트 실행
```

---

## 2. 단일 PC 설치 순서

### 사전 요구사항
- Windows 10/11 (64비트 OS)
- 키움증권 OpenAPI+ 설치 (32비트) → [키움증권 홈페이지 다운로드]
- **32비트 Python 3.8~3.10** 별도 설치 필수
  - 다운로드: https://www.python.org/downloads/windows/
  - "Windows installer (32-bit)" 선택
- 영웅문4(HTS) 설치 및 키움 계좌 보유

### 설치 순서

```
1. setup_env.bat 실행
   → 32비트 Python 경로 입력 (예: C:\Python38-32\python.exe)
   → AI_T_Agent\venv32\ 자동 생성 및 패키지 설치

2. config\config.json 설정
   → environment: "mock" (모의) 또는 "live" (실전)
   → telegram.bot_token, allowed_chat_id 입력
   → Gemini API 키 입력 (선택)

3. 영웅문4 로그인 후 run_tca.bat 실행
   → 텔레그램에서 !시스템시작 명령 전송
```

---

## 3. 2대 PC 분산 설치

### 역할 분담

| | 컴퓨터 A (트레이딩 PC) | 컴퓨터 B (분석/관제 PC) |
|---|---|---|
| **역할** | Kiwoom API 실시간 매매 실행 | AI 분석, 텔레그램 관제, 백테스트 |
| **OS** | Windows (키움 API 필수) | Windows / Mac / Linux |
| **Python** | 32비트 필수 | 64비트 가능 |
| **실행 모듈** | ERA, STA | TCA, RSA, BQA |
| **Kiwoom 설치** | 필수 | 불필요 |

### 컴퓨터 A 설정 (트레이딩 PC)

1. 단일 PC 설치 순서와 동일
2. **AI_T_Agent 폴더를 네트워크 공유로 설정**
   ```
   탐색기 → AI_T_Agent 폴더 우클릭 → 속성 → 공유
   → 공유 이름: AMATS
   → 읽기/쓰기 권한 부여
   ```
3. `config.json`에서 네트워크 역할 설정:
   ```json
   "network": {
     "role": "computer_a",
     "shared_db_path": ""
   }
   ```

### 컴퓨터 B 설정 (분석/관제 PC)

1. AI_T_Agent 폴더 전체 복사 또는 클론
2. 컴퓨터 A의 공유폴더를 네트워크 드라이브로 연결
   ```
   탐색기 → 내 PC → 네트워크 드라이브 연결
   → 드라이브: Z:
   → 폴더: \\[컴퓨터A_IP]\AMATS
   ```
3. `config.json` 설정:
   ```json
   "network": {
     "role": "computer_b",
     "shared_db_path": "Z:\\"
   }
   ```
   - `shared_db_path`를 설정하면 TCA, RSA, BQA가 컴퓨터A의 DB를 직접 참조

4. 컴퓨터 B에서는 `run_tca.bat`, `run_bqa.bat`만 실행
   (STA, ERA는 컴퓨터 A에서만 실행)

### 데이터 흐름

```
[컴퓨터 A]                           [컴퓨터 B]
 영웅문4 로그인                        텔레그램 !상태, !현황
     ↓                                      ↑
 ERA (주문/체결)  ←─→  unified_data.db  ←─→  TCA (관제)
 STA (스크리닝)         futures_data.db       RSA (분석)
                    (네트워크 공유)           BQA (백테스트)
```

---

## 4. 일일 운용 순서

### 장 시작 전 (08:50 이전)
```
1. 영웅문4 실행 및 로그인 (컴퓨터 A)
2. run_tca.bat 실행 (컴퓨터 A 또는 B)
3. 텔레그램: !상태 → 시스템 확인
4. 텔레그램: !시스템시작 → ERA 구동
5. run_sta.bat → 1번(테마 추적) 실행
6. run_sta.bat → 2번(스윙 스크리너) 실행
```

### 장 중 (09:00~15:30)
```
- 텔레그램: !주식현황, !선물현황 → 포지션 모니터링
- 텔레그램: !매도 [종목명] → 수동 청산
- 텔레그램: !긴급정지 → 전량 청산 + ERA 종료
```

### 장 마감 후
```
- 텔레그램: !시스템종료
- run_bqa.bat → 1번(K값 최적화) 실행
- 텔레그램: !최적화결과 → 결과 확인
- 텔레그램: !전략승인 → 최적 전략 적용
```

---

## 5. 텔레그램 명령어 전체 목록

| 명령어 | 기능 |
|---|---|
| `!상태` | ERA/TCA 가동 여부 점검 |
| `!주식현황` | 보유 주식 수익률 현황 |
| `!선물현황` | 선물 포지션 현황 |
| `!시스템시작` | ERA 주문엔진 구동 |
| `!시스템종료` | ERA 종료 |
| `!매도 삼성전자` | 특정 종목 즉시 청산 |
| `!전량매도` | 보유 전 종목 시장가 청산 |
| `!긴급정지` | 전량 청산 후 ERA 강제 종료 |
| `!백테스트시작` | K값 최적화 실행 |
| `!최적화결과` | 최적화 결과 조회 |
| `!전략승인` | 최적 K값 실전 적용 |
| `!도움말` | 명령어 목록 보기 |

---

## 6. 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| ERA 구동 실패 (venv32 없음) | setup_env.bat 미실행 | `setup_env.bat` 실행 |
| 로그인 실패 -102 | Kiwoom 버전 미업데이트 | 영웅문4 재실행 → opstarter 완료 |
| 로그인 실패 -101 | 서버 접속 불가 | 인터넷/방화벽 확인 |
| mock 서버 접속 불가 | 운영 시간 외 | 모의투자 서버: 평일 08:00~18:00만 운영 |
| 텔레그램 알림 없음 | bot_token 미설정 | config.json telegram 항목 확인 |
