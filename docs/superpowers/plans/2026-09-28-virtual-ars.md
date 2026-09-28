# 가상 ARS 회선 Implementation Plan

> **For agentic workers:** Execute inline. Steps use checkbox (`- [x]`) syntax.

**Goal:** ClawOps 없이 실제 Live-1과 대화하는 독립 로컬 ARS 회선과 디버그 웹을 실행한다.

**Architecture:** 가상 ARS 서버(9020)는 HTTP 통화 제어와 G.711 WebSocket 음성을 제공한다.
별도 디버그 백엔드(9011)와 웹(5181)은 기존 승인·Live·위임·전사·청취·종료 경로를 재사용한다.
운영 백엔드와 DB·세션 쿠키를 분리하며 실제 통화 공급자로 전환하는 fallback은 없다.

**Tech Stack:** 기존 FastAPI, httpx, websockets, Python 3.12, macOS Yuna/afconvert, React/Vite.

**Spec:** 아래 승인된 범위와 인터페이스를 이 단계의 설계 원본으로 사용한다.

상태: complete
승인: 가상 ARS와 실제 모델을 연결하는 설계를 제시한 뒤 사용자가
“clawOps와 별개로 가상의 ARS라인도 구축해줘. 이거는 따로 api가 필요한가 그러면?”이라고 요청했다.
이번은 고정 ARS 1개와 전체 흐름 연결까지다. 동적 상담원·잡음/회선 지연 시나리오는 후속 단계다.

## 범위와 인터페이스

- 가상 시험 번호 `01000000001`, 기타 안내 4번 → 진료 시간 2번. 가상 데이터는
  평일 오전 9시~오후 6시, 점심 오후 12시~1시, 주말·공휴일 휴진으로 고정한다.
- 안내는 macOS 로컬 음성으로 생성·캐시한다. 유료 ARS/TTS API나 외부 가입이 필요 없다.
  Live-1과 메인 모델 호출에는 기존 API 설정을 사용한다.
- `GET /health`, `GET /scenarios`, `POST /calls`, `GET /calls/{id}`,
  `POST /calls/{id}/hangup`, `WS /calls/{id}/media`.
- REST·WS 모두 자동 생성한 로컬 bearer 키로 인증한다. loopback에만 바인딩한다.
- 음성은 8kHz mono G.711 mu-law, 20ms 프레임으로 실제 시간에 맞춰 전달한다.
  모델 음성 재생 후 mark ACK를 내고 clear는 대기 음성을 지운다.
- DTMF 선택·상태·진료 안내 전달 완료·재생량·종료 이유를 기록한다.
  메뉴를 고른 것과 안내를 끝까지 재생한 것은 별도 증거로 구분한다.
- 잘못된 메뉴는 재안내, 무입력은 제한된 재안내 후 종료, 최대 180초.
  새 프로세스에서 과거 가상 회선은 복구해 재발신하지 않는다.
- 디버그 앱은 통화 공급자 주입으로 시뮬레이터만 사용하고 Google 연동은 비활성화한다.
  모델이 실제 번호를 요청해도 시뮬레이터의 등록된 가상 번호만 수락한다.
- 기존 프런트엔드 스타일을 유지하고 상단에 '가상 통화 · 실제 전화 발신 없음'과 시험 번호를
  표시한다. `frontend-design` 기준으로 기존 중립색·타이포그래피·반응형 흐름을 재사용한다.

## 작업

- [x] `simulator/engine.py`, `server.py`: 메뉴·연결 상태, 실시간 음성, mark/clear, HTTP/WS.
  `tests/simulator/test_server.py`: 무인증 거부, 중복 생성, 다른 번호 거부, DTMF,
  잘못된 입력, 재생 ACK 지연과 clear, 직접 종료를 실패 검사부터 구현한다.
- [x] `calls/audio_gateway.py`, `simulator/gateway.py`, `debug_app.py`: 실제 모델 공통 경로,
  독립 앱과 쿠키, 시뮬레이터 전용 gateway. 기존 호출 API/모델 승인 권한은 유지한다.
  모사 통화로 ClawOps 생성 0회·승인 전 연결 0회·종료 동작을 검사한다.
- [x] `scripts/prepare_virtual_ars.py`, `dev_simulator.py`: 음성 생성과 세 프로세스 실행/정리,
  비공개 키·별도 DB. 키·음성·실행 기록은 Git 제외 `var/simulator/`에 둔다.
- [x] 디버그 화면 표기, 실행 안내, API 계약 문서. 기존 운영 화면/공유 주소는 유지한다.
- [x] 전체 관련 자동 검사 후 실제 Live-1로 가상 회선 연결, 4→2 도구 실행,
  안내 수신·종료/보고를 검사한다. 실패 시 실패 단계와 미검증 범위를 보고한다.
  화면에서 가상 통화 표기와 승인 카드·전사를 확인한다. 실제 전화·Google 쓰기는 실행하지 않는다.

## 완료 기준

독립 ARS API 서버가 실행되고 디버그 웹에서 승인 후 실제 Live-1과 가상 음성이 연결된다.
메뉴 선택과 재생/종료 증거를 조회할 수 있고 운영 ClawOps 경로에는 접근하지 않는다.
자동 검사·실제 모델 검증·모바일/실회선 미검증을 구분하여 기록하고 로컬 커밋한다.


## 검증 기록 — 2026-09-28

- `./scripts/check.sh`: Ruff, backend **360개**, web **55개**, TypeScript, ESLint,
  운영용 Vite 빌드 통과. 외부 서비스를 모사한 검사다.
- 브라우저 5181에서 실제 메인 모델로 승인 카드를 생성하고 클릭하기 전에는 연결하지 않음을
  확인했다. 공유 CallManager 승인 검사는 기존 자동 검사에 포함된다.
- 실제 Live-1/API + 가상 ARS 3회. 실제 전화 발신·Google 변경은 0회.
  1. 첫 회: 안내는 인식했지만 '4번이요'라고 발화하고 DTMF는 보내지 않아 무입력 종료.
     Live의 위임 지침에 ARS는 말로 답하지 않고 backend의 send_dtmf로 선택하도록 명시했다.
  2. 두 번째: 4→2 입력, 진료 안내 전체 수신과 end_call(goal_achieved) 확인.
     가상 서버가 미디어 연결 해제를 회선 종료로 취급해 already_ended가 기록됐다.
     미디어 종료와 회선 종료를 분리하고 회귀 검사를 추가했다.
  3. 최종: **4→2 입력, 안내 전체 전송, end_call(goal_achieved), 재생 ACK 대기,
     hangup API 요청과 completed 회선 확인**. 첫 선택 후 위임 ValueError가 한 번 발생했지만
     ARS 재안내 후 2번을 선택해 완료했다. 2번 입력은 연결 후 약 42.7초,
     안내 전송 완료 56.8초, API 종료 완료 72.7초였다.
- 최종 증거: `digits=["4","2"]`, `hours_delivered=true`,
  `end_reason=hangup_requested`, 앱 `end_report.reason=goal_achieved`,
  `end_report.status=audio_drained`, `carrier_action=hangup_requested`.
  기록은 Git 제외 `var/simulator/*-run.json`, `debug.sqlite3`, `result.png`에 보관한다.
- Chrome 기본 크기와 390×844에서 가상 통화 표시·전사·종료 버튼 레이아웃 확인.
  실제 모바일 기기·앱 내 브라우저 검증은 수행하지 않았다.
- 운영 9010 health와 디버그 9011/9020/5181 응답을 확인했다.
  운영 백엔드 재시작과 Cloudflare 재설정은 하지 않았다.

## 남은 제한

- 모델 동작은 결정적이지 않다. 세 시험은 신뢰도 통계가 아니며, 간헐적인 위임 실패와
  ARS에 불필요한 발화가 남아 있다. 이 환경을 사용해 후속 정교화를 반복할 수 있다.
- 기존 앱의 coarse outcome은 incomplete로 남을 수 있다. 이번 최종 시험의 성공 증거는
  독립 ARS의 메뉴·안내·종료 기록과 end_report다. 최종 자연어 보고가 종료 주체를 보수적으로
  표현한 것도 관찰했으며 보고 분류 변경은 이 회선 구축 범위에 포함하지 않았다.
- 안내는 가상의 고정 데이터다. 자유 대화 상담원과 잡음·지연 주입, 실제 PSTN 검증은 제외한다.
- 독립 ARS의 회선 기록은 재시작 시 사라진다. 앱의 대화/활동은 별도 디버그 DB에 저장한다.

실행: `backend/.venv/bin/python scripts/dev_simulator.py`.
사용 및 API: [가상 ARS 안내](../../virtual-ars.md).
