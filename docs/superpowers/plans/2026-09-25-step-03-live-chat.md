# 3단계: DeepAgents 실제 채팅 구현 계획

> **For agentic workers:** Execute inline by default. 이 프로젝트는 직접 순차 구현한다.
> 병렬 에이전트는 별도 합의가 필요하다. 각 체크 항목은 검증 가능한 작업 결과다.

**Goal:** 현재 채팅 화면에서 실제 모델과 문맥을 이어 대화하고, 스트리밍·중단·오류 복구를 확인한다.

**Architecture:** React가 대화 문맥을 FastAPI에 보내면 서버의 공식 DeepAgents가 모델을 호출한다.
POST 응답을 SSE로 전달하고, 브라우저는 사용자에게 보여 줄 텍스트만 점진적으로 표시한다.
현재 탭의 메모리에 문맥을 유지하며, 영구 저장은 4단계에서 구현한다.

**Tech Stack:** Python 3.12, FastAPI, deepagents, langchain-openai, React, TypeScript, fetch/ReadableStream.

**Spec:** `docs/product.md`, `docs/roadmap.md`, `AGENTS.md`

## 상태와 승인 기록

- 상태: `in_progress` — 사용자 승인 후 구현 중.
- 요청: 2단계 UI/UX 수정 완료 뒤 “좋아 일단 그 다음 단계 이어서 진행할게.”
- 승인: “모델 연결에 필요한 키 등의 정보는 이전에 쓰던 곳에서 복사해서 .env를 새로 구성해서 저장해줘. 진행해.”
- 승인에 따라 기존 설정을 새 프로젝트 `.env`로 복사한다. 원본은 수정하지 않는다.
- 이 문서 승인에는 아래 설정 재사용 및 합성 문장으로 한정한 실제 모델 검증을 포함한다.

## Global Constraints

- 제품 표시 이름은 `user proxy agent`, 내부 서비스 식별자는 `agent-service`를 유지한다.
- 기존 메신저 디자인을 유지하며 응답 상태·중단·재시도만 같은 시각 체계로 추가한다.
- 전화·Gmail·Calendar는 이번 단계에서 실행하거나 도구로 등록하지 않는다.
- 시작/재시작/health 확인은 모델을 호출하지 않는다. 메시지 전송 때만 호출한다.
- 키·모델 URL은 서버 설정이다. 브라우저 요청으로 공급자·모델·키를 바꾸지 못한다.
- 키, 공급자 오류 원문, 시스템 프롬프트, reasoning, 도구 인자를 화면·일반 로그에 노출하지 않는다.
- 현재 단계는 loopback에 바인딩한 단일 사용자 로컬 개발 서비스다. 인증 없는 공개 배포는 범위 밖이다.
- 실제 응답과 통화 화면 예시를 분리한다. 예시 메시지·조작은 모델에 전달하지 않는다.
- 구현과 관련 테스트를 먼저 작은 단위로 완료한 뒤 로컬 Git에 커밋한다.

## 조사 결과와 기술 결정

### 공식 SDK 직접 사용

기존 프로젝트는 로컬 `framework/deep-agentic` 패키지와 `agenticfw`를 통해 평가 시나리오를 실행한다.
새 서비스는 `deepagents.create_deep_agent`를 직접 사용한다. 기존 설정 규약만 참고하며,
형제 프로젝트 코드·가상환경·평가 루프를 런타임 의존성으로 추가하지 않는다.

2026-09-25 PyPI 조회 결과 `deepagents==0.7.19`, `langchain-openai==1.6.6`이 배포되어 있다.
이 버전들을 기준으로 구현 시 resolver와 테스트를 거쳐 lockfile을 고정한다.
Python 3.12는 두 패키지의 배포 메타데이터상 지원 범위 안에 있다.
이 조사에서 패키지를 프로젝트에 설치하거나 기존 lockfile을 변경하지 않았다.

공식 API는 모델 인스턴스 주입, `messages` 스트림, harness profile을 지원한다.
실제 설치 버전에서 profile과 도구 구성을 확인하는 테스트를 작성한다.
외부 업무 도구는 없으며, 내부 `write_todos`만 허용한다. 파일 도구와 `execute`를 제외하고
기본 general-purpose subagent를 비활성화한다. 실제 모델에 전달된 도구 목록이
`write_todos` 외의 항목을 포함하면 검증 실패로 처리한다.
호스트 파일시스템·셸 backend를 주입하지 않으며 checkpoint/store는 연결하지 않는다.

### 모델과 설정

기본 제안은 기존 텍스트 채팅용 설정 `gpt-5.6-sol`과 해당 `MODEL_BASE_URL`/`MODEL_API_KEY`다.
`calling-agent`의 `gpt-realtime-2.1`은 음성 통화용으로 유지한다.
현재 설정의 존재와 모델명만 확인했다. 자격증명 유효성·스트리밍 호환성은 승인 후 검증한다.

설정 로딩 규칙:

1. 기본 파일은 새 프로젝트 루트 `.env`이며 Git에서 제외한다.
2. `AGENT_SERVICE_ENV_FILE`을 명시하면 해당 파일의 허용된 설정 키만 읽는다.
   이번 로컬 검증에서는 사용자 지시에 따라 복사한 새 프로젝트 `.env`를 사용한다.
3. 프로세스 환경변수가 파일보다 우선한다. 다른 `.env`를 자동 탐색·병합하지 않는다.
4. 허용 키는 `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`, `MODEL_MAX_TOKENS`,
   `MODEL_TRUST_ENV`다. 기존 `PROXY_FAKE_MODEL` 등 평가용 설정은 읽지 않는다.
5. base URL, 모델명, 키는 필수이며 누락 시 chat 요청에 명확한 설정 오류를 반환한다.
   원격 HTTPS와 loopback HTTP를 허용한다. URL의 사용자 정보·query·fragment는 거부한다.
6. `ChatOpenAI`를 OpenAI 호환 Chat Completions 어댑터로 사용한다. model 식별자는 임의 변경하지 않는다.
   temperature는 강제하지 않는다. 기본 출력 한도 2,048 tokens, 명시적 양의 정수 설정으로 변경 가능하다.
7. 공급자 timeout 60초, 전체 실행 120초, SDK 자동 retry 0회, graph recursion limit 12를 둔다.
   명시적인 사용자 재시도로만 새 요청을 실행한다.
8. HTTP 환경 프록시 사용은 `MODEL_TRUST_ENV`로 제어한다(기본 1).
   기존 기업 gateway의 직접 연결이 필요하면 이 서비스의 HTTP 클라이언트만 0으로 설정한다.
   전역 `NO_PROXY`나 다른 프로젝트 환경은 수정하지 않는다.

키 복사는 이번 승인 범위에 포함한다. 브라우저 설정 화면·모델 선택 UI는 추가하지 않는다. 모델 변경은 서버 환경 설정과 재시작으로 한다.

## 요청·스트림 계약

`POST /api/chat` — JSON 요청, `text/event-stream` 응답. 기존 `/api/health` 계약은 유지한다.

```json
{
  "request_id": "client-generated-uuid",
  "messages": [
    {"role": "user", "content": "내일 오전에 병원에 전화하려고 해"},
    {"role": "assistant", "content": "전화로 확인할 내용을 알려 주세요."},
    {"role": "user", "content": "진료 시간과 예약이 필요한지 물어봐 줘"}
  ]
}
```

- `messages`는 user/assistant 텍스트만 허용하며 첫/마지막 항목은 user여야 한다.
  system/tool 역할, 빈 문장, 잘못된 UUID와 미정의 필드는 거부한다.
- 최대 80개 메시지, 메시지당 12,000자, 전체 60,000자, HTTP body 512KiB 제한.
  초과 시 요청을 거부하고 새 대화를 시작하도록 안내한다. 문맥을 조용히 잘라내지 않는다.
- 서버가 시스템 지침을 추가한다: 한국어의 간결한 응답, 부족한 조건의 확인,
  사용할 수 없는 전화·앱 작업을 수행했다고 말하지 않기.
- 브라우저 문맥은 신뢰하는 시스템 지침으로 승격하지 않는다.

SSE 이벤트의 `data`는 JSON이다. 공통 필드는 `request_id`와 서버 발급 `message_id`다.

| event | 추가 필드 | 의미 |
| --- | --- | --- |
| `start` | 없음 | 요청을 수락하고 assistant 응답을 시작함 |
| `delta` | `text` | 사용자에게 표시할 텍스트를 덧붙임 |
| `done` | 없음 | 정상 완료. 요청당 한 번만 전송 |
| `error` | `code`, `message`, `retryable` | 실패 종료. 이후 delta/done 없음 |

헤더 전 검증 실패는 HTTP 422/413, 설정 누락은 503 JSON 오류다.
시작 후 인증 실패·rate limit·연결 오류·timeout은 정제한 `error`로 전달한다.
오류 code는 `not_configured`, `invalid_request`, `provider_auth`, `rate_limited`,
`provider_unavailable`, `timeout`, `invalid_stream`으로 구분한다.
서버 내부 오류는 `provider_unavailable`의 일반 문구로 보고 세부 사항은 비밀 없는 코드로만 기록한다.
모델이 텍스트 없이 정상 종료해도 빈 성공 응답으로 처리하지 않고 오류로 표시한다.
서버는 대기 중 15초 간격의 SSE comment heartbeat를 보낸다.

`request_id`는 늦게 도착한 이벤트를 구분하는 식별자이며 영구 중복 실행 방지 키는 아니다.
클라이언트는 동시에 한 번만 전송할 수 있고, 재시도에는 새 ID를 쓴다.

## 중단·문맥·화면 상태

- 상태: `idle → submitting → streaming → completed | stopped | failed`.
- 전송 시 사용자 메시지는 한 번만 추가한다. 응답 대기 표시 뒤 실제 delta를 점진적으로 출력한다.
- 생성 중에는 전송 버튼을 `응답 중단`으로 바꾼다. 다음 메시지 초안은 계속 작성할 수 있다.
- 중단은 fetch AbortController로 즉시 화면 갱신을 멈추고 서버 연결을 닫는다.
  서버의 disconnect 감지는 DeepAgents 실행을 취소하고 async iterator/공급자 스트림을 닫는다.
  별도 백그라운드 실행을 남기지 않는다. 공급자 내부의 과금 취소까지 보장한다고 표현하지 않는다.
- 중단되거나 실패한 부분 응답은 화면에 보존하고 상태를 명시한다. 다음 요청 문맥에는
  완료된 assistant 응답과 사용자 메시지만 전달한다. 내부 도구 메시지는 전달하지 않는다.
- 마지막 요청의 실패/중단에는 `다시 시도`를 제공한다. 사용자 메시지를 복제하지 않고
  해당 assistant 응답 자리를 새 시도로 교체한다. 이후 새 요청을 보냈다면 과거 응답 재시도는 숨긴다.
- 오류 후에도 대화와 새 초안을 보존한다. 임의 자동 재전송은 하지 않는다.
- 통화 예시로 이동하면 진행 중 채팅을 중단하며, 메인으로 돌아왔을 때 부분 응답과 초안은 유지한다.
- 새로고침은 문맥을 초기화한다. 화면 안내는 메인에 ‘대화는 새로고침하면 사라집니다’로 바꾸고
  예시 화면에는 실제 발신·음성 재생이 아니라는 안내를 유지한다.
- 스트리밍 중 하단을 보고 있을 때만 자동 스크롤한다. 위로 스크롤한 사용자를 끌어내리지 않고
  `최신 메시지로` 버튼으로 돌아오게 한다. 전체 토큰을 매번 스크린리더로 읽지 않고 상태 변경만 알린다.
- 응답은 기존 plain text/줄바꿈으로 표시한다. Markdown 렌더링은 이번 단계에 추가하지 않는다.

## 작업 단위

### 1. 설정·DeepAgents 실행 어댑터

파일: 신규 `backend/src/agent_service/settings.py`, `chat/{__init__,runtime,schemas}.py`,
`backend/tests/test_chat_runtime.py`, `backend/tests/test_settings.py`, 루트 `.env.example`.
수정: `backend/pyproject.toml`, `backend/uv.lock`, `.gitignore`(필요 시).

인터페이스: `load_settings() -> Settings`,
`stream_reply(messages, settings) -> AsyncIterator[str]`.
설정 검증은 실제 연결을 만들지 않으며, runtime은 텍스트 chunk만 반환하고 정제 전 예외를 서버 경계로 전달한다.

- [x] 설정 우선순위·누락·잘못된 URL·secret repr 차단의 실패 테스트 후 구현한다.
- [x] 공식 DeepAgents를 실제 생성한 테스트에서 허용 도구 목록과 사용자용 텍스트 필터링을 검증한다.
  모델은 네트워크 없는 테스트 모델로 주입한다. tool/reasoning chunk는 반환되지 않아야 한다.
- [x] `.env.example`에 빈 키와 등록·변경 방법을 적고 관련 테스트 통과 후 커밋한다.

검증: `cd backend && uv run pytest tests/test_settings.py tests/test_chat_runtime.py -q`.

### 2. 스트리밍 API와 종료 수명

파일: 신규 `backend/src/agent_service/chat/routes.py`, `backend/tests/test_chat_api.py`,
`backend/tests/test_chat_disconnect.py`; 수정 `backend/src/agent_service/main.py`.

인터페이스: 위 `/api/chat` 계약. runtime dependency를 교체할 수 있게 하여 오류·지연·중단을 재현한다.

- [x] 검증 실패가 모델을 호출하지 않는지, start/delta/done 순서와 오류 종료를 테스트한 후 구현한다.
- [x] UTF-8 한국어, newline/JSON escaping, heartbeat, 빈 응답, 출력 도중 오류와 전체 timeout을 검증한다.
- [x] 실제 임시 Uvicorn 서버에 연결한 HTTP 클라이언트를 중단해 iterator가 닫히고 작업이 정리되는지
  검증한다. 테스트 runtime 기준 disconnect 후 1초 이내 취소를 확인한다.
- [x] 기존 health/서버 수명 테스트를 함께 확인하고 커밋한다.

검증: `cd backend && uv run pytest tests/test_chat_api.py tests/test_chat_disconnect.py tests/test_health.py tests/test_dev.py -q`.

### 3. 채팅 화면 스트리밍·중단·재시도

파일: 신규 `web/src/chat/{stream.ts,stream.test.ts,useChat.ts}`, `web/e2e/live-chat.spec.ts`.
수정: `App.tsx`, `chat/{types,ChatView,Composer,MessageList}.tsx/ts`, `chat/chat.css`, `web/package.json`.

인터페이스: `streamChat(request, signal, onEvent): Promise<void>`와 위 상태 모델의 `useChat`.
통화 예시는 현재 로컬 상태를 계속 사용한다. 프론트에서 공급자 API를 직접 호출하지 않는다.

- [x] 분리된 TCP chunk/다중 SSE 이벤트/UTF-8 경계/EOF(done 누락)/잘못된 이벤트의 parser 테스트를 작성한다.
- [x] 대기·부분 응답·완료·실패·중단 상태와 중복 전송 방지를 구현한다.
- [x] 같은 탭 문맥, 부분 응답 제외, 재시도 시 사용자 메시지 중복 없음, 다음 초안 보존을 검증한다.
- [x] 최신 메시지로 이동, 위로 읽기 유지, IME·키보드·모바일 조작을 검증한다.
- [x] 기존 UI 테스트는 메인 채팅 요청을 fixture로 모사하도록 수정하고 통화 예시 검증은 유지한다.
  새 테스트는 네트워크 요청이 `/api/health`와 `/api/chat`에만 발생하는지 확인한다.
- [x] 브라우저 검증 후 UI 캡처와 함께 커밋한다.

검증: `cd web && npm test && npm run typecheck && npm run lint && npm run build`;
`PLAYWRIGHT_CHANNEL=chrome npm run test:e2e`.

### 4. 실제 모델 검증과 단계 완료

파일: 신규 `scripts/check_live_chat.py`, 수정 README·로드맵·이 문서.
실제 검증은 명시적으로 실행한 경우에만 동작한다. 일반 테스트/개발 서버 시작에서 호출하지 않는다.

- [ ] 승인된 기존 모델 설정을 명시적으로 로드해 합성 문장으로 검증한다.
  한국어 첫 응답, 임의의 식별 단어를 기억하는 후속 응답, 긴 응답 중단 후 새 요청을 확인한다.
- [ ] 브라우저에서 token 단위 표시·중단·새 메시지·새로고침 초기화를 직접 확인한다.
- [ ] 모사 오류 검증과 실제 모델 호출 결과를 별도로 기록한다. 실제 호출을 못 했다면
  이유를 적고 3단계를 완료로 표시하지 않는다.
- [ ] `./scripts/check.sh`와 전체 E2E를 통과한 뒤 단계 완료 기록을 커밋한다.

## 완료 기준

실제 브라우저–FastAPI–DeepAgents–설정한 모델 경로로 한국어 문맥 대화가 가능하다.
응답이 점진적으로 나타나고, 중단/오류 후 사용자가 대화를 이어갈 수 있다.
중단한 요청의 늦은 이벤트가 새 응답을 오염시키지 않고, 서버 실행도 정리된다.
실제 호출과 모사 테스트의 증거가 구분되어 있으며, 예시 통화는 실제 호출과 섞이지 않는다.

## 근거

- [DeepAgents 모델·미들웨어 구성](https://docs.langchain.com/oss/python/deepagents/customization)
- [텍스트 스트림과 이벤트 출처](https://docs.langchain.com/oss/python/deepagents/streaming)
- [도구 제외·기본 subagent 비활성화](https://docs.langchain.com/oss/python/deepagents/profiles)
- [deepagents 배포 메타데이터](https://pypi.org/pypi/deepagents/0.7.19/json)
- [langchain-openai 배포 메타데이터](https://pypi.org/pypi/langchain-openai/1.6.6/json)
- 로컬 참고: `../user_proxy_agent/user_proxy_agent/runtime/model_config.py`,
  `../user_proxy_agent/pyproject.toml`. 형제 프로젝트 파일은 변경하지 않는다.

## 구현 중 검증 기록

- 새 `.env`를 원본의 모델 설정만 복사해 생성했다. 권한 0600과 Git ignore를 확인했다. 값은 출력하지 않았다.
- 설정·DeepAgents·SSE·disconnect·기존 backend 테스트 28개 통과, Ruff 통과.
- deepagents 0.7.19에는 기본 파일 도구 `delete`가 있고 Todo middleware는 기본 포함이 아니었다.
  `delete`까지 제외하고 공식 TodoListMiddleware를 명시해 모델에 `write_todos`만 전달됨을 검증했다.
- ASGI 2.4+의 기본 StreamingResponse는 공급자가 조용할 때 즉시 disconnect를 듣지 않았다.
  전송과 disconnect를 함께 감시하는 response를 구현했다. 실제 HTTP 테스트에서 1초 내 upstream 정리를 검증했다.

- 웹 API/스트림 테스트 16개와 브라우저 E2E 22개 통과, TypeScript·ESLint·빌드 통과.
- 중단 버튼의 type이 submit으로 바뀌면서 초안을 전송하는 브라우저 기본 동작을 발견했다.
  클릭 기본 동작을 취소해 수정했으며, 중단·초안 보존·늦은 토큰 무시 회귀 테스트를 추가했다.
- Impeccable 점검 결과 탐지 항목 없음. 기존 UI 디자인을 유지하고 답변 상태·중단·재시도·최신 메시지 이동을 추가했다.
