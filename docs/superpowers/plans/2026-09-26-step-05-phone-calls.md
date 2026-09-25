# 5단계: 채팅에서 실제 전화 실행 구현 계획

> **For agentic workers:** Execute inline by default. 직접 순차 구현한다.
> 병렬 에이전트는 별도 합의가 필요하다. 각 작업 단위는 검증 후 로컬 Git에 커밋한다.

**Goal:** 사용자가 채팅으로 명시적으로 요청한 전화를 실제로 걸고, 채팅을 계속하면서 통화 상태·결과를 확인하거나 직접 끊는다.

**Architecture:** DeepAgents의 전화 도구가 서버의 CallManager에 작업을 등록하고 즉시 call_id를 돌려준다.
CallManager는 채팅 SSE와 독립된 비동기 작업으로 ClawOps 회선과 Azure Realtime 음성을 연결한다.
SQLite에 통화 상태·소유권·발신 시도를 저장하며 웹은 인증된 상태 조회로 카드를 갱신한다.

**Tech Stack:** 기존 FastAPI·SQLite·DeepAgents·React·TypeScript, httpx, websockets 15 계열.

**Spec:** `docs/product.md`, `docs/roadmap.md`, `AGENTS.md`, 아래 설계와 승인 범위.

## 상태와 승인 기록

- 상태: `in_progress` — 승인 후 구현 진행 중.
- 계획 작성 요청: 4단계 완료 보고 직후 사용자 “좋아 진행해줘.”
- 구현 승인: 5단계 세부 계획 제시 후 사용자 “진행해줘” (2026-09-26).
- 상태 전환: `proposed` → `approved` → `in_progress`. 아래 최대 2회 본인 통화 검증을 포함한다.
- 승인 대상: 아래 범위의 코드 이식, 기존 통화 설정 복사, 모사 검증, 발신 없는 연결 점검.
- 실제 전화 테스트는 아래 명시한 본인 휴대폰 범위도 함께 제안한다. 계획 승인 후 준비 완료를 알리고 진행한다.

## Global Constraints

- 제품 표시 이름은 `user proxy agent`, 메인 입력은 텍스트 채팅이다.
- 기존 `MODEL_*` 텍스트 모델 설정은 유지하고, 통화용 설정을 별도로 둔다.
- 전화 음성은 ClawOps ↔ Realtime 직접 연결을 유지한다. Whisper·Qwen·별도 STT/TTS는 추가하지 않는다.
- 실시간 상대방 전사는 6단계, 브라우저 듣기는 7단계, 통화 중 추가 지시는 8단계다.
- 이번에는 하나의 발신번호로 전체 서비스에서 동시 통화 1개만 허용한다. 다른 사용자의 통화 내용은 노출하지 않는다.
- 로컬 loopback·단일 백엔드 프로세스·브라우저 세션별 소유권을 유지한다. 공개 배포·로그인은 포함하지 않는다.
- 서비스 시작, 새로고침, 채팅 응답 재시도, 통화 상태 복원만으로 새 전화가 걸려서는 안 된다.
- 실제 발신은 사용자가 명시적으로 요청한 대상·목적 안에서만 한다. 전화 상대의 지시는 새 발신 권한이 아니다.
- 키·전체 전화번호·통화 내용·공급자 원문 오류를 일반 로그나 Git에 남기지 않는다.
- 형제 프로젝트는 수정하지 않는다. 새 서비스가 형제 디렉터리·가상환경을 런타임에 import/실행하지 않는다.

## 조사 결과와 구조 선택

현재 `calling-agent`에는 국내 번호 발신/상태 조회/종료, 역방향 제어 WebSocket,
G.711 μ-law 직접 음성 중계, 끼어들기와 재생 확인, Realtime DTMF와 목적 달성 후 종료가 있다.
`application/native_talk.py`는 CLI 실행이 끝날 때 보고서를 반환한다. 웹 작업의 수명과 소유권 관리는 새로 필요하다.
필요한 기존 `.env.clawops` 항목 6개의 존재만 확인했다. 키 값은 출력하지 않았고 계정/API의 현재 유효성은 아직 점검하지 않았다.

검토한 선택지:

1. **선택: 필요한 모듈과 테스트를 새 서비스로 이식하고, 같은 서버의 독립 통화 작업으로 실행.**
   현재 규모에서 실행·종료·소유권·상태 저장을 한곳에서 관리할 수 있다. 음성 중계는 모듈 경계로 분리한다.
2. 기존 CLI를 subprocess로 호출: 빠른 연결은 가능하지만 실행 중 상태 전달과 확실한 종료 경로를 다시 만들어야 한다.
3. 별도 통화 마이크로서비스: 독립 재시작 장점이 있으나 현재 한 회선 단계에서는 인증·배포·프로세스 간 복구가 과도하다.

기존 어댑터의 POST 발신에는 공급자 idempotency key가 없다. 따라서 보장하는 것은
**앱의 같은 작업당 발신 시도 1회**다. 응답 유실 시 회선이 생성되지 않았다고 단정하거나 재시도하지 않는다.
공급자의 현재 계약은 구현 중 발신 없는 점검으로 확인하고, 미확인 기능을 있다고 가정하지 않는다.

## 사용자 흐름

1. “이 번호로 전화해서 가능한 시간을 물어봐 줘”처럼 대상과 목적을 요청한다.
   번호·필수 질문·전달할 내용이 부족하면 메인 에이전트가 먼저 확인한다.
2. 조건이 갖춰지면 전화 도구를 실행한다. 명확한 발신 요청에 별도 승인 버튼을 매번 추가하지 않는다.
   목적·상대·번호가 담긴 통화 카드가 해당 요청 메시지에 연결된다.
3. 준비 중 → 연결 중 → 통화 중 → 종료 확인 중 → 종료됨으로 갱신한다.
   실패와 연결/종료 확인이 필요한 상태를 별도로 표시한다.
4. 통화가 진행돼도 텍스트 채팅은 계속된다. 채팅의 ‘응답 중단’은 텍스트 생성만 중단한다.
   ‘통화 종료’는 모델 응답을 기다리지 않고 서버 제어 API로 보낸다.
5. 다른 대화로 이동하거나 새로고침해도 통화는 계속된다. 다른 대화를 보고 있을 때도
   내 진행 중 통화를 표시하는 작은 영역에서 해당 대화로 이동하거나 직접 종료할 수 있다.
6. 종료 후 카드에 회선 상태와 통화 에이전트가 정리한 결과를 보인다. 다음 채팅에서도 이 결과를 참고한다.
   통화가 끝났다는 사실만으로 목적 달성으로 표시하지 않는다.
7. 실제 카드에는 이번에 구현한 종료와 상태 확인만 제공한다. 기존 ‘화면 예시’의 듣기·전사·추가 지시는 그대로 예시로 유지한다.

### 발신 대상과 테스트 범위

- 첫 구현은 `CALL_ALLOWED_NUMBERS`의 국내 번호만 허용한다. 초기 로컬 허용 번호는 이전에 사용자가
  본인 통화 테스트에 지정한 끝자리 **9615** 휴대폰 하나다. 전체 번호는 Git 제외 `.env`에 둔다.
- 사용자의 대화에 등장한 번호를 정규화해 사용하며, 모델이 추측하거나 상대방에게 새로 들은 번호로 자동 재발신하지 않는다.
- 허용 목록 밖이면 발신하지 않고 설정이 필요함을 안내한다. 병원 등 추가 대상은 사용자가 지정할 때 목록을 확장한다.
- 실회선 검증은 위 본인 번호로 **최대 2회, 회당 최대 180초**를 제안한다.
  1회는 채팅 병행·새로고침 후 직접 종료, 1회는 간단한 질문에 대한 답을 재확인하고 자동 종료하는 시험이다.
- 시작 멘트는 “이준수님이 요청하신 통화 기능 테스트를 진행하는 AI 도우미입니다.”를 사용한다.
  목적은 “지금 통화 테스트가 가능한지 물어보고 답을 재확인하기”다. 병원·제삼자에게는 이 단계 검증으로 발신하지 않는다.
- 연결 실패·무응답을 이유로 자동 재발신하지 않는다. 추가 실통화가 필요하면 기존 2회 범위를 넘기기 전에 사용자에게 알린다.

## 서버 계약과 수명

### 메인 에이전트 도구

- `start_phone_call(destination, subject, purpose, opening_message, questions, listen_first=False)`
  → `{call_id, status, purpose}`. 필수 내용은 검증·길이 제한하며, 요청 1회에 통화 1개만 등록한다.
- `get_phone_call(call_id)` → 소유한 통화의 최신 저장 상태·결과. 임의 공급자 URL이나 계정 ID를 받지 않는다.
- `end_phone_call(call_id)` → 종료 요청 접수/현재 상태. 실제 종료 확인 전 완료라고 답하지 않는다.
- 기존 `write_todos`를 유지한다. 셸·파일·검색·메일·일정 도구는 추가하지 않는다.
- owner·conversation ID·원래 user_message_id는 서버가 실행 문맥으로 주입하고 모델 인자로 받지 않는다.
  음성 에이전트에는 `send_dtmf`, `end_call`만 제공하며 새 통화를 거는 도구는 제공하지 않는다.

### 영속 상태

- 스키마 버전 2로 마이그레이션해 `phone_calls`, `phone_call_events`를 추가한다. 기존 대화·세션은 보존한다.
- phone_calls: 내부 UUID, owner, conversation, source_user_message_id, 정규화 목적/질문/번호,
  provider_call_id, status, dial_attempted_at, stop_requested, created_at, updated_at,
  outcome, reported_summary, error_code, version을 저장한다.
- `(owner_id, source_user_message_id)`를 유일하게 한다. 같은 텍스트 응답을 재시도하거나
  모델이 같은 도구를 반복 호출해도 기존 작업을 반환한다. 인자가 바뀌면 기존 작업을 바꾸지 않고 충돌을 반환한다.
- 전체 회선의 활성 슬롯을 SQLite 트랜잭션으로 획득한다. 불확실한 발신도 슬롯을 유지한다.
  다른 세션에는 번호·목적·소유자를 보여주지 않고 ‘현재 통화가 진행 중입니다’만 반환한다.
- `preparing → dialing → connected → ending → ended`가 기본 흐름이다.
  발신 전 실패/명확한 거절은 `failed`, 발신 응답 유실은 `unknown`, 종료 미확인은 `ending`과 오류 코드로 남긴다.
  번호 미발신이 확실한 취소는 `canceled`다. UI와 DB에서 terminal 상태 목록을 공유한다.
- `outcome`은 `pending | model_reported_success | incomplete | canceled`다.
  정상적인 goal_achieved 도구 결과·마지막 인사 재생·회선 종료 확인이 모두 있어야 model_reported_success다.
  상대방 전사가 없는 이번 단계의 요약은 독립 검증된 원문으로 취급하지 않는다.

### 독립 작업과 종료

- `CallManager`는 앱 lifespan이 소유하고 작업 참조를 보관한다. 채팅 제너레이터/HTTP 요청이 소유하지 않는다.
- 등록은 DB에 먼저 기록한다. 실제 POST 직전에 dial_attempted_at을 원자적으로 기록하고, 해당 값이 있는 작업은 다시 발신하지 않는다.
- 발신 전 발신번호 소유 확인, Realtime 세션 준비, ClawOps 제어 소켓 연결을 완료한다.
  초기 연결 점검 시에만 짧은 음성 생성도 검증한다. 키가 없으면 전화 도구는 오류를 반환하고 일반 채팅은 계속 동작한다.
- 같은 발신번호를 사용하는 기존 실험 프로세스/세션과 중복 실행하지 않는다. 연결 전 현재 사용 여부를 확인하고,
  기존 통화가 있으면 이를 자동 종료하거나 번호 소켓을 탈취하지 않고 점검/발신을 보류한다.
- 통화 시작 후에는 회선/오디오/최대 시간 감시를 수행한다. 기본 제한은 발신 시도부터 180초다.
- 직접 종료 요청은 stop_requested를 먼저 저장한다. 발신 전이면 취소하고, 발신 중 응답이 나중에 도착하면 즉시 종료한다.
  통화 중이면 음성 생성·재생 큐를 정리하고 REST 종료 → 상태 조회로 확인한다.
- 목표 달성 자동 종료는 기존 마지막 인사 재생 확인과 끼어들기 취소 동작을 유지한다.
- 정상 서버 종료 때는 관리 중인 회선을 종료하고 확인 결과를 기록한다. 강제 종료 후 시작 시에는 기존 작업을 복원하되
  새 발신은 하지 않는다. provider ID가 있으면 상태 조회 후 아직 살아 있는 회선을 종료 정리한다.
- provider ID를 얻지 못한 `unknown`은 자동 복구/재발신하지 않는다. 운영자가 ClawOps에서 확인해야 하며,
  이번 UI는 이를 종료 완료로 바꾸거나 새 발신으로 우회하지 않는다. 해당 상황의 수동 정리는 별도 운영 대응으로 남긴다.
- 진행 중 회선 상태는 2초 간격으로 확인한다. 종료 요청 후 확인은 2초 간격, 최대 5회로 제한하며
  각 공급자 HTTP 요청의 기존 12초 timeout을 유지한다. 이후에는 오류를 보존하고 명시적 다시 확인을 받는다.
  조회 실패를 ended로 바꾸지 않는다.
  브라우저 연결 해제와 네트워크 장애 자체는 회선 종료의 증거가 아니다.

### HTTP·웹 동기화

| API | 동작 |
| --- | --- |
| `GET /api/conversations/{id}/calls?cursor=...` | owner 확인 후 요청 메시지에 연결된 통화 목록. 50개씩 조회 |
| `GET /api/calls/active` | 내 준비/진행/종료 확인/불확실 통화 목록. 다른 owner의 정보는 제외 |
| `GET /api/calls/{id}` | 최신 상태·정리된 결과·version, owner가 다르면 404 |
| `POST /api/calls/{id}/stop` | JSON `{}`. 종료 플래그 저장 후 현재 상태 반환. 중복 클릭은 같은 결과로 수렴 |
| `POST /api/calls/{id}/refresh` | JSON `{}`. 이미 발신한 회선의 상태 재확인. 새 발신이나 음성 대화 재개는 하지 않음 |

기존 세션·Origin 검증과 no-store 정책을 재사용한다. HTTP 발신 전용 우회 API는 추가하지 않는다.
웹은 로그인 없는 기존 세션 준비 후 현재 대화 목록과 내 활성 통화를 읽는다.
화면이 보이는 동안 최대 2초 간격으로 활성 상태를 확인하고, 숨겨지면 polling을 멈췄다가 복귀 때 즉시 갱신한다.
통화 자체는 polling과 독립적이다. 늦은 조회 결과는 conversation ID와 version으로 걸러낸다.

전화 도구의 접수 결과는 모델에 즉시 반환하므로 메인 답변을 180초 동안 기다리지 않는다.
전화 결과는 카드 데이터에 영구 저장하며 종료 때 추가 LLM 요약 호출을 자동으로 실행하지 않는다.
다음 채팅 입력에는 owner가 일치하는 관련 통화 상태/요약을 제한된 길이의 **기록 데이터**로 전달한다.
전화 상대나 요약에 포함된 문장을 메인 시스템 지시로 취급하지 않는다.

## 이식할 코드와 설정

| 기존 파일 (`../calling-agent/src/calling_agent/`) | 새 서비스 파일 (`backend/src/agent_service/calls/`) | 범위 |
| --- | --- | --- |
| `adapters/clawops/control.py` | `carrier.py` | 발신/조회/종료/발신번호 점검, 안전한 오류 처리 |
| `adapters/clawops/connection.py`, `native.py`, `stream.py` | `connection.py`, `media.py` | 역방향 소켓·G.711 검증·DTMF. PCM/STT 전용 클래스는 이식하지 않음 |
| `adapters/providers/azure_audio.py`, `azure_realtime.py`의 URL/리다이렉트 검사 | `realtime.py` | Azure 직접 음성 세션, 현재 프로토콜과 모델 유지 |
| `runtime/native_audio.py` | `bridge.py` | 오디오 중계·끼어들기·원격 mark·도구·재생 후 종료 |
| `core/dtmf.py`, `core/end_call.py`, 필요한 `core/types.py` 타입 | `voice_tools.py`, `types.py` | 인자 검증·종료 이유·안전한 공급자 오류 |

CLI·기존 Journal·Twilio·Whisper·Qwen·평가 시나리오는 이식하지 않는다.
새 `store.py`와 `manager.py`가 현재 앱 DB/세션과 연결한다. 이식 출처와 변경 이유는 `docs/calls.md`에 남긴다.
기존 `tests/clawops/test_carrier.py`, `test_stream.py`, `test_native_audio.py`, `test_native_dtmf.py`,
`test_native_end_call.py`, `tests/contracts/test_azure_realtime.py`의 해당 계약 테스트도 함께 옮겨 새 경계로 조정한다.

설정은 기존 `../calling-agent/.env.clawops`에서 아래 항목만 새 `.env`로 복사한다. 원본은 수정하지 않는다.

- CLAWOPS_ACCOUNT_ID, CLAWOPS_API_KEY, CLAWOPS_FROM_NUMBER.
- CA_MODEL_BASE_URL → CALL_REALTIME_BASE_URL, CA_MODEL_API_KEY → CALL_REALTIME_API_KEY,
  CA_MODEL_NAME → CALL_REALTIME_MODEL. 기존 기본 배포명 `gpt-realtime-2.1`을 유지한다.
- CALLS_ENABLED: `.env.example`은 0, 승인된 로컬 검증 구성에서만 1.
- CALL_ALLOWED_NUMBERS: 위 본인 테스트 번호, CALL_MAX_SECONDS: 기본/최대 180.
- ClawOps origin은 기존 검증된 HTTPS/WSS origin으로 고정한다. 공급자 키를 임의 URL로 전달하지 않는다.

## 디자인 방향

frontend-design과 Impeccable 스킬을 적용하고 기존 글꼴·중립 색상·여백을 유지한다.
카드는 사용자가 요청한 메시지 다음에 배치하고 ‘무엇을 확인하는 전화인지’와 현재 상태를 먼저 보여준다.
전화번호와 목적을 확인할 수 있고 ‘통화 종료’는 작은 보조 메뉴에 숨기지 않는다.
종료 확인 중에는 버튼 중복 실행을 막되 상태 조회 실패 시 ‘다시 확인’을 제공한다.
화면 예시의 전사·듣기 요소를 실제 통화 결과처럼 표시하지 않는다. 모바일에서도 종료 버튼은 44px 이상으로 유지한다.

## 작은 작업 단위와 검증

### 1. 통화 어댑터·음성 연결 이식

**Files:** 위 calls 패키지의 `types.py`, `carrier.py`, `connection.py`, `media.py`, `realtime.py`,
`voice_tools.py`, `bridge.py`, 신규 `settings.py`; `backend/pyproject.toml`, `backend/uv.lock`, `.env.example`;
`backend/tests/calls/test_carrier.py`, `test_media.py`, `test_bridge.py`, `test_voice_tools.py`, `test_settings.py`.

**Interfaces:** `CallSettings.load()`는 메인 모델 설정과 독립적이다.
`ClawOpsControl.dial/lookup/hangup`, `AgentConnection.media`, `NativeAudioBridge.run/report`의 필요한 계약을 유지한다.

- [x] 이식 대상 테스트를 먼저 새 패키지 경계에서 실행해 미구현 실패를 확인하고 이식한다.
  신뢰 origin, 소켓 실패, 오디오 형식, 끼어들기, 늦은 음성 무시, DTMF 중복, 인사 재생 뒤 종료를 검증한다.
  `cd backend && uv run --locked pytest -q tests/calls` 및 Ruff 통과 후 커밋한다. 여기까지는 모사만 사용한다.

### 2. 통화 저장소·CallManager·제어 API

**Files:** `calls/store.py`, `calls/manager.py`, `calls/routes.py`; 수정 `storage.py`, `main.py`;
`backend/tests/calls/test_store.py`, `test_manager.py`, `test_routes.py`.

**Interfaces:** `CallManager.start(owner, conversation_id, source_user_message_id, spec) -> CallView`,
`stop(owner, call_id) -> CallView`, `get(owner, call_id) -> CallView`, `shutdown()`.
`CallView`는 위 상태·목적·번호·결과·version을 담고 비밀값/원시 오디오를 포함하지 않는다.

- [x] 기존 DB 마이그레이션 보존, 다른 세션 차단, 동시 등록 단일 슬롯, 요청 중복/응답 유실,
  발신 도중 종료, 원격 선종료, 종료 실패, 타임아웃, 정상/강제 서버 종료 복구를 실패 테스트부터 구현한다.
  발신 HTTP 호출 횟수를 검증하고 재시작은 새 dial이 0회임을 증명한다. backend 전체 테스트·Ruff 후 커밋한다.

### 3. DeepAgents 전화 도구와 채팅 수명 분리

**Files:** `calls/tools.py`; 수정 `chat/runtime.py`, `chat/routes.py`, 필요 시 `chat/history.py`;
`backend/tests/calls/test_agent_tools.py`, 기존 `test_chat_runtime.py`, `test_chat_disconnect.py`.

**Interfaces:** `build_call_tools(manager, owner, conversation_id, source_user_message_id)`가 도구를 구성한다.
`stream_reply`에 서버 주입 실행 문맥을 전달한다. 음성 작업은 manager가 소유한다.

- [x] 공급자 모사 상태에서 실제 DeepAgents 도구 실행 경로를 시험한다. 정보가 없는 입력,
  허용 밖 번호, 일반 질문, 중복 도구 호출, 텍스트 재시도, 채팅 연결 취소가 추가 발신을 만들지 않게 한다.
  통화 진행 중에도 다음 텍스트 요청이 정상 처리되는지 검증하고 커밋한다.

### 4. 실제 통화 카드·종료·복원 화면

**Files:** 신규 `web/src/chat/PhoneCallCard.tsx`, `calls.ts`, `useCalls.ts`;
수정 `App.tsx`, `ChatView.tsx`, `MessageList.tsx`, `types.ts`, `chat.css`;
`web/e2e/phone-calls.spec.ts`, 기존 fixture와 UI 테스트.

**Interfaces:** 상태 API → CallView → PhoneCallCard. source_user_message_id로 카드 위치를 정한다.
다른 대화의 내 활성 통화는 작은 상태 영역으로 접근한다. 종료와 새로 불러오기 요청은 채팅 전송과 분리한다.

- [x] 요청 접수/연결/종료/실패/unknown, 중복 클릭, 늦은 상태, 새로고침/대화 전환,
  통화 중 채팅, 종료 후 결과 복원을 E2E로 검증한다. 예시 화면 회귀·모바일·키보드도 확인한다.
  웹 단위 테스트·타입·lint·빌드·전체 E2E 통과 후 커밋한다.

### 5. 실제 연결 검증과 기록

**Files:** `scripts/check_live_calls.py`, `docs/calls.md`, `README.md`, `docs/product.md`,
`docs/roadmap.md`, 이 계획. 개인 번호·통화 내용이 있는 증거는 Git 제외 data/var/에 둔다.

**Interfaces:** `check_live_calls.py --preflight`는 발신번호·소켓·짧은 음성 생성만 점검한다.
발신 실행은 별도 `--run --to ... --request-id ...`를 명시해야 한다. 서버 시작에 묶지 않는다.

- [ ] 자동 검증 전체 통과 후 기존 설정 복사 및 발신 없는 점검을 수행한다.
  이어 위 본인 번호의 최대 2회 시험을 채팅 UI를 통해 수행한다. 메인 LLM 도구 호출 → 회선 연결 →
  실제 음성 대화 → UI 직접 종료/목표 종료 → 공급자 종료 확인 → 결과 복원까지 구분해 기록한다.
  응답자 청취가 필요한 항목은 사용자 피드백과 시스템 계측을 구분하며, 미검증 항목을 완료라고 쓰지 않는다.
  검증 결과·남은 한계·실행 방법을 갱신하고 커밋한다.

## 완료 기준

- 명시적 채팅 요청이 실제 전화 1회로 이어지고, 통화 중에도 메인 채팅을 사용할 수 있다.
- 사용자가 UI로 직접 종료할 수 있으며 실제 회선 종료 확인 전 완료를 표시하지 않는다.
- 복원·채팅 재시도·중복 도구 호출·네트워크 응답 유실·서버 재시작이 자동 재발신을 만들지 않는다.
- 통화 상태와 모델이 보고한 업무 결과를 분리해 저장·표시하고, 다음 채팅에서 결과를 참고할 수 있다.
- 실제 모델/회선 검증과 모사 검증을 구분해 기록한다. 실통화 검증이 남으면 5단계 전체 완료로 표시하지 않는다.
- 실시간 전사·듣기·추가 지시·새 번호로 이어 걸기·공개 배포는 이번 범위에 포함하지 않는다.

## 참고와 현재 검증 상태

- 로컬 조사: `../calling-agent/docs/native-audio.md`, 위 이식 대상 코드 및 관련 테스트,
  현재 `chat/runtime.py`, `storage.py`, `CallCard.tsx`.
- OpenAI Docs 확인: [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations).
  Realtime의 음성 세션·함수 결과 전달·WebSocket 재생 중단 처리를 확인했다.
  Azure의 실제 배포 가용성과 ClawOps의 현재 계정 상태를 이 문서만으로 확정하지 않는다.
- 계획 작성 중에는 코드·키 파일을 바꾸거나 소켓/전화 연결을 실행하지 않았다.

### 작업 단위 1 검증

새 패키지가 없어 실패하는 계약 테스트부터 이식했다. 통화 모사 테스트 56개 통과.
시작 멘트 설정 회귀 테스트의 실패를 확인한 뒤 요청 멘트를 연결했다. 실제 발신/외부 소켓은 아직 실행하지 않았다.

### 작업 단위 2 검증

SQLite 스키마 2, 단일 활성 슬롯, 소유권 API와 독립 CallManager 구현. backend 전체 120개 통과, Ruff 통과.
발신 응답 유실/중복, 발신 중 종료, 원격 종료, 시간 제한, 재시작 무발신 복구,
종료 확인 실패 후 재확인, 발신 직후 DB 장애에도 회선 종료 시도를 검증했다.
목표 달성 근거와 숫자 계측은 비공개 DB 필드에 저장하며 원시 음성은 저장하지 않는다.

### 작업 단위 3 검증

DeepAgents 전화 시작/조회/종료 도구, 서버 실행 문맥, 다음 채팅의 제한된 통화 기록 주입 구현.
실제 DeepAgents 그래프 + 모사 모델/회선으로 도구 중복 실행·동시 채팅을 검증했다.
실제 로컬 HTTP 연결을 끊고 텍스트 재시도해도 발신은 1회이며 종료 API는 회선을 정리했다.
종료 API 연결이 저장 도중 끊긴 경우도 종료 작업이 계속되는 회귀 테스트를 추가했다.
backend 전체 126개·Ruff 통과. 아직 실회선 발신은 하지 않았다.

### 작업 단위 4 검증

실제 통화 카드, 다른 대화의 활성 통화 영역, 직접 종료/상태 확인과 2초 polling 구현.
웹 단위 17개, 전체 E2E 35개 및 추가 숨김/복귀 1개 통과. 타입·lint·빌드 통과.
데스크톱 1440×1000/모바일 390×844 합성 화면 검토, 종료 버튼 44px·키보드 검증 완료.
기존 레이아웃 검증의 API 허용 목록에 새 읽기 전용 /api/calls/active 경로를 추가했다.
Impeccable detect 지적 없음. 실통화 전사/청취 버튼은 실제 카드에 노출하지 않는다.
