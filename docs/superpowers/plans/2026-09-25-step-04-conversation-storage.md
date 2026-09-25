# 4단계: 대화 저장·이어가기 구현 계획

> **For agentic workers:** Execute inline by default. 직접 순차 구현한다.
> 병렬 에이전트는 별도 합의가 필요하다. 체크 항목은 검증 가능한 작업 결과다.

**Goal:** 새로고침·서버 재시작 뒤에도 대화를 다시 열고, 여러 대화를 구분해 문맥을 이어간다.

**Architecture:** FastAPI가 SQLite에 대화·메시지·응답 실행 상태를 저장한다.
브라우저는 새 입력과 대화 ID만 보내고 서버가 저장된 메시지로 DeepAgents 문맥을 구성한다.
로컬 브라우저 세션으로 소유권을 구분하며, 기존 SSE 스트리밍·중단·재시도를 유지한다.

**Tech Stack:** 기존 Python 3.12·FastAPI·DeepAgents·React·TypeScript, 표준 라이브러리 sqlite3.

**Spec:** `docs/product.md`, `docs/roadmap.md`, `AGENTS.md`

## 상태와 승인 기록

- 상태: `completed` — 구현·자동 검증·실제 모델 검증 완료 (2026-09-26).
- 계획 작성 요청: 3단계 완료 보고 뒤 “진행해줘.”
- 구현 승인: 4단계 세부 계획과 커밋 `4368fef` 제시 직후 사용자 “진행해줘.”
- 승인 상태를 `approved`로 확인하고 `in_progress`로 구현한 뒤 완료 기준을 충족해 `completed`로 전환했다.
- 승인 대상에는 아래 브라우저별 소유권 방식과 합성 문장으로 한정한 실제 모델 검증을 포함한다.

## Global Constraints

- 제품 표시 이름은 `user proxy agent`다. 기존 담백한 메신저 디자인을 유지한다.
- 직접 순차 구현하고, 검증한 작은 작업 단위마다 독립 로컬 Git에 커밋한다.
- 기존 `.env`와 텍스트 모델 설정을 유지한다. 형제 프로젝트는 변경하지 않는다.
- 대화·세션·SQLite 파일과 부속 파일은 Git·일반 로그에 포함하지 않는다.
- 전화·Calendar·Gmail 연결은 후속 단계다. 이번 단계는 텍스트 대화만 다룬다.
- 개발 서비스는 loopback 바인딩, 단일 백엔드 프로세스로 운영한다. 공개 배포는 포함하지 않는다.
- 앱 시작·목록 조회·대화 복원·서버 재시작은 모델 호출을 발생시키지 않는다.

## 사용자 경험과 범위

1. 사이드바의 **새 대화**로 빈 화면을 연다. 첫 전송 전에는 빈 대화를 DB에 만들지 않는다.
2. 첫 전송 때 대화를 생성한다. 제목은 첫 입력의 공백을 정리한 앞 40자로 정하고 별도 모델 호출을 하지 않는다.
3. **이전 대화** 목록은 최근 활동 순서로 보이며, 선택하면 저장된 메시지와 응답 상태를 불러온다.
4. 선택한 대화는 `?conversation=<UUID>`에 반영한다. 새로고침·뒤로/앞으로 이동도 같은 대화를 연다.
   주소에 ID가 없으면 마지막 선택 ID를 참고하고, 새 대화를 명시적으로 선택하면 해당 기록을 지운다.
   localStorage에는 선택 ID만 둔다. 메시지·세션 토큰을 넣지 않는다.
5. 대화 전환·화면 예시 이동은 진행 중인 생성을 중단한다. 새로고침·탭 닫기는 연결 해제로 중단한다.
   받은 부분 응답과 중단 상태를 저장하고, 다시 열어도 자동 재생성하지 않는다.
6. 사용자는 마지막 실패·중단 응답만 다시 시도할 수 있다. 사용자 메시지는 중복 추가하지 않는다.
7. 대화별 입력 초안은 현재 탭 메모리에 보존한다. 전송하지 않은 초안의 새로고침 후 복원은 포함하지 않는다.
8. 로딩·조회 실패·대화 없음·다른 세션 소유 대화에 대해 빈 화면과 구별되는 상태를 제공한다.
   조회 실패 때 자동으로 새 대화를 만들어 입력을 다른 곳에 보내지 않는다.

### 소유권: 이번 단계의 명시적 제안

- 첫 접속에 서버가 임의의 256비트 토큰을 발급한다. HttpOnly·SameSite=Strict·Path=/,
  30일 Max-Age의 호스트 전용 쿠키를 사용한다. 현재 loopback HTTP에서는 Secure를 설정하지 않는다.
- DB에는 토큰 해시·owner ID·만료 시각만 저장한다. 재시작 뒤에도 유효한 세션을 확인할 수 있다.
- 모든 대화·메시지·실행 조회와 변경은 세션 owner로 제한한다. 클라이언트가 owner를 지정하지 못한다.
  다른 owner의 ID와 존재하지 않는 ID는 동일하게 404로 처리한다.
- 같은 브라우저 프로필·호스트는 같은 사용자 공간이다. 별도 브라우저/시크릿 창은 분리된다.
  이는 로그인 계정이 아니다. 쿠키 삭제·만료 후 기존 대화에 다시 접근하는 기능은 이번 범위 밖이다.
- 다른 기기 동기화·계정 로그인·계정 복구는 별도 설계가 필요하다. 로컬 OS 사용자의 DB 접근을 막는 암호화도 범위 밖이다.
- 브라우저의 변경 요청은 JSON만 허용하고 Origin을 허용된 로컬 웹 주소와 비교한다.
  포트 변경은 기존 WEB_PORT/BACKEND_PORT 설정에 맞춘다. Vite 프록시 경로를 유지하고 CORS를 넓히지 않는다.
  Origin 없는 로컬 CLI 요청도 유효한 세션이 필요하다. 세션 발급 요청의 교차 출처도 검사한다.

## 저장과 실행 규칙

- DB 기본 위치: 프로젝트의 `data/agent-service.sqlite3`. 디렉터리 0700, DB와 부속 파일은
  사용자 전용 권한으로 생성한다. 기존 `data/` ignore를 검증한다. 테스트는 임시 경로를 주입한다.
- `schema_version`, `sessions`, `conversations`, `messages`, `runs` 테이블을 초기화한다.
  대화에는 owner·title·생성/활동 시각, 메시지에는 순번·role·본문·상태,
  실행에는 request ID·대화/응답 ID·입력 지문·상태·안전한 오류 코드·부분 응답을 둔다.
- 메시지/실행 상태는 `streaming`, `completed`, `stopped`, `failed`, `interrupted`를 사용한다.
  화면 전송 전 상태 `submitting`은 클라이언트에만 둔다. 사용자 메시지는 `completed`다.
- 짧은 트랜잭션으로 입력·응답 자리·실행을 함께 생성한 후 모델을 호출한다.
  네트워크 대기 중 DB 트랜잭션을 유지하지 않는다. DB 작업은 이벤트 루프 밖에서 실행한다.
- 대화별 활성 실행은 DB에서 하나만 허용한다. 여러 탭 동시 전송은 409 `conversation_busy`다.
  새 입력마다 `request_id`를 만들고, 같은 전송의 네트워크 재시도에서는 같은 ID를 유지한다.
  owner별 request ID를 유일하게 하고 입력 지문을 비교한다. 같은 요청은 409 `request_exists`와
  기존 대화/응답 ID를 반환하며 모델을 재호출하지 않는다. 다른 내용의 ID 재사용은 409 `request_conflict`다.
- 명시적 응답 재시도는 새 request ID로 마지막 assistant 메시지를 재사용한다.
  이전 시도 내용/상태는 runs에 남기고, 대화에는 현재 시도의 응답 하나만 표시한다.
- 부분 응답은 델타 수신 시 최대 초당 한 번 묶어 저장하고, 종료·실패·중단 시 최종 저장한다.
  취소 정리에서 공급자 스트림 종료와 DB 반영을 끝내고 활성 실행을 해제한다.
  정상 연결 해제는 받은 내용까지 저장한다. 프로세스 강제 종료는 마지막 DB 반영분까지만 복구된다.
- 단일 프로세스 시작 시 남은 `streaming`을 `interrupted`로 바꾼다. 모델 호출을 재개하지 않는다.
  종료 상태 저장에 실패하면 `done`을 보내지 않는다. 안전한 저장 오류를 반환하고 복원 조회로 확인한다.
- 모델에는 서버 DB의 user 및 완료된 assistant 텍스트만 전달한다. 실패/중단 응답과 예시는 제외한다.
  최신 사용자 입력을 포함해 최근 전체 턴부터 최대 80개 메시지·60,000자 안에서 구성한다.
  오래된 턴부터 제외하며 자동 요약은 하지 않는다. 저장된 원문은 유지하고 문맥 제한을 README에 명시한다.
- DeepAgents 체크포인터는 이번에 추가하지 않는다. 복원 대상은 사용자에게 보이는 대화이며,
  내부 write_todos 상태·도구 실행 중간 지점의 영속 복구는 포함하지 않는다.

## HTTP 인터페이스

모든 데이터 응답은 `Cache-Control: no-store`를 사용한다. ID는 UUID, 시각은 UTC ISO 8601이다.

| 인터페이스 | 요청 / 결과 |
| --- | --- |
| `POST /api/session` | JSON `{}` → 204와 세션 쿠키. 기존 유효 쿠키는 유지. 앱에서 먼저 완료한 뒤 다른 API 호출 |
| `GET /api/conversations?cursor=...` | 최신 활동순 50개 `{items:[{id,title,updated_at}], next_cursor}`. 같은 시각은 ID로 정렬 |
| `POST /api/conversations` | `{conversation_id: UUID}` → 201 `{id,title,updated_at}`. 같은 owner/ID 재요청은 기존 결과 반환. 제목은 첫 메시지 승인 때 설정 |
| `GET /api/conversations/{id}?before=...` | `{conversation, messages, next_cursor}`. 최근 메시지 50개를 오래된 순으로 반환. 이전 메시지는 위로 더 보기로 로드 |
| `POST /api/chat` | `{request_id, conversation_id, content}` 또는 `{request_id, conversation_id, retry_message_id}`. 둘 중 하나만 허용. 브라우저가 전체 이력을 보내는 기존 계약 대체 |
| SSE | 기존 start/delta/done/error 유지. 모든 이벤트에 `conversation_id`, `request_id`, `message_id` 포함. start에 저장된 `user_message_id` 포함. done은 최종 DB 저장 후 전송 |

저장 메시지는 `{id, role, text, status, error_code, retryable}` 형태다. 원시 공급자 오류는 저장/전달하지 않는다.
세션 없음/만료는 401, 잘못된 입력은 422/413, 저장소 불가·모델 설정 오류는 안전한 503이다.
세션 만료 후 진행 중이던 전송을 새 owner로 자동 재시도하지 않는다. 사용자에게 새로 불러오기를 안내한다.
409 응답은 ID를 기준으로 해당 대화를 다시 조회한다. 진행 중인 다른 탭 응답에는 자동 재접속하지 않으며,
상태가 남아 있으면 ‘다른 창에서 답변을 작성하고 있습니다’와 다시 불러오기 동작을 제공한다.
별도의 중단 API는 추가하지 않고 현재 요청의 연결 취소를 유지한다.

## 디자인 방향

사용자가 합의한 간결한 메신저 화면을 확장한다. frontend-design 및 Impeccable 스킬을 적용한다.
기존 canvas `#f7f7f6`, surface `#ffffff`, ink `#262626`, muted `#6a6a67`,
line `#e5e5e3`, soft `#f0f0ee`와 한국어 시스템 글꼴을 유지한다.
제목/본문/보조 정보의 기존 굵기·크기 차이를 사용하고 새 장식·폰트·대시보드는 추가하지 않는다.

- 데스크톱: 사이드바 위 ‘새 대화’, 그 아래 간결한 제목 목록, 아래 ‘화면 예시’.
- 모바일: 기존 메뉴 대화상자 안에 같은 목록. 선택 후 닫기·포커스 복귀 유지.
- 선택 행·로딩·실패를 구별하고, 긴 제목은 한 줄 말줄임과 접근 가능한 전체 이름을 제공한다.
- 대화 전환 시 스크롤을 새 대화의 마지막 메시지로 이동한다. 이전 메시지 추가 시 현재 읽는 위치를 유지한다.
- 현재 입력 초안과 늦게 도착한 스트림 이벤트가 다른 대화로 넘어가지 않게 한다.
- 하단의 ‘대화는 새로고침하면 사라집니다’ 문구를 실제 저장/실패 상태에 맞게 바꾼다.

## 작은 작업 단위

### 1. SQLite 저장소와 브라우저 세션

**Files:** 새 `backend/src/agent_service/storage.py`, `backend/src/agent_service/session.py`,
`backend/tests/test_session.py`;
수정 `backend/src/agent_service/main.py`, 필요 시 `settings.py`의 비밀값과 독립된 경로 설정.

**Interfaces:** `create_app(*, database_path: Path | None = None)`가 저장소를 주입한다.
`ConversationStore(path)`는 초기화, 세션 발급/조회, owner로 제한된 대화·메시지 조회를 담당한다.
후속 라우트는 `request.app.state.store`와 `require_owner(request) -> str`를 사용한다.

- [x] 세션 격리·만료·DB 재오픈 복원·스키마 재초기화 안전성·파일 권한 테스트를 먼저 실패시킨 뒤 구현한다.
  `cd backend && uv run --locked pytest -q tests/test_session.py` 통과 후 커밋한다.

### 2. 서버 소유 대화 이력과 스트림 저장

**Files:** 새 `backend/src/agent_service/conversations.py`, `backend/src/agent_service/chat/history.py`,
`backend/tests/test_conversations.py`, `backend/tests/test_chat_persistence.py`;
수정 `chat/routes.py`, `chat/schemas.py`, `storage.py`, `main.py`, 기존 chat 테스트.

**Interfaces:** 위 HTTP 계약을 구현한다. `history.py`가 저장 메시지에서 제한된 모델 입력을 구성한다.
기존 `stream_reply(messages, settings)` 모델 연결은 유지한다.
저장소의 실행 시작/부분 저장/최종 확정은 owner·conversation·request ID를 함께 받아 상태 전이를 제한한다.

- [x] 목록/메시지 페이지 경계, 타 owner 접근 차단, 전송/재시도 중복 방지, 동시 실행 충돌,
  완료/실패/연결 취소 저장, 강제 종료 후 복구, 저장 실패 때 완료 미표시, 문맥 선택을 테스트하고 구현한다.
  실제 HTTP 연결 해제 테스트로 공급자 취소와 최종 저장을 함께 입증한다.
  `cd backend && uv run --locked pytest -q` 및 ruff 통과 후 커밋한다.

### 3. 새 대화·목록·복원 화면

**Files:** 새 `web/src/chat/conversations.ts`, `web/src/chat/ConversationList.tsx`,
`web/e2e/conversations.spec.ts`;
수정 `App.tsx`, `chat/useChat.ts`, `chat/stream.ts`, `chat/types.ts`, `chat/ChatView.tsx`,
`chat/Composer.tsx`, `chat/MessageList.tsx`, `chat/chat.css`, 기존 stream 및 E2E 테스트.

**Interfaces:** 대화 API 모듈이 세션 준비/목록/생성/조회 및 안전한 오류 변환을 담당한다.
`useChat`은 선택 대화 ID·조회 상태·서버 메시지·전송/중단/재시도를 제공한다.
`ConversationList`는 목록·선택 ID·새 대화/선택/더 보기 콜백을 받는다.
전환마다 세대 식별자로 이전 조회/스트림 결과를 무시한다. URL popstate도 같은 전환 경로를 사용한다.

- [x] 새 대화 두 개의 문맥 분리, 새로고침 복원, 뒤로 가기, 늦은 이벤트 격리,
  세션 만료/조회 실패, 전송 중 전환, 입력 초안 보존, 모바일 메뉴·키보드·스크롤을 E2E로 검증하고 구현한다.
  `cd web && npm run test && npm run typecheck && npm run lint && npm run build`와
  `env -u NO_COLOR PLAYWRIGHT_CHANNEL=chrome npm run test:e2e` 통과 후 커밋한다.

### 4. 실제 모델 검증과 운영 기록

**Files:** 수정 `scripts/check_live_chat.py`, `README.md`, `docs/product.md`, `docs/roadmap.md`,
이 계획; 합성 대화만 포함하는 `docs/verification/step-04-*.png`.

**Interfaces:** 기존 `--run` 명시 실행 방식에 세션 쿠키·새 API 계약을 적용한다.

- [x] `./scripts/check.sh`와 전체 E2E 통과 후 실제 로컬 서버에서 합성 기억 문장을 전송한다.
  새로고침/다른 대화 전환/백엔드 재시작 뒤 같은 대화에서 기억 여부를 묻는다.
  별도 브라우저 세션에서 기존 대화 접근 거부를 확인하고, 중단 응답 복원과 명시적 재시도를 확인한다.
  모델 호출 수를 계수하는 자동 테스트로 복원 자체의 무호출을 증명한다.
  데스크톱·모바일 화면을 확인하고 자동 시험과 실제 모델 검증을 구별해 기록한 뒤 커밋한다.

## 완료 기준과 제한

- 저장된 대화 두 개를 전환하고 새로고침·서버 재시작 후에도 각각 이어서 대화할 수 있다.
- 다른 브라우저 세션은 기존 대화 내용을 목록/직접 ID/채팅 요청 어느 경로로도 읽거나 수정하지 못한다.
- 같은 전송의 반복 요청은 사용자 메시지와 모델 실행을 늘리지 않는다.
- 중단/실패/서버 종료를 완료로 오인하지 않고, 복원만으로 모델을 다시 호출하지 않는다.
- 기존 간결한 디자인·반응형·화면 예시 분리를 유지하며 저장 데이터와 비밀값이 Git에 포함되지 않는다.
- 로그인·기기 간 동기화·검색·제목 편집·대화 삭제·자동 요약·백그라운드 응답 재접속은 포함하지 않는다.
  계정이 없는 이번 단계의 브라우저별 구분과 대화 문맥 제한을 사용자 문서에 명시한다.

## 검증 기록

- 계획 작성 시 현재 API·메모리 기반 useChat·사이드바·모델 설정·Git ignore를 확인했다.
- 위 계획 제시 당시에는 문서만 변경했다. 승인 후 구현했으며 기존 `.env`는 변경하지 않았다.
- 이번 서비스의 개발 서버만 재시작했다. 형제 프로젝트와 외부 전화·앱 상태는 변경하지 않았다.

### 작업 1 검증

- 세션 API 미구현으로 3개 실패를 확인한 후 구현했다.
- 세션 격리·만료·재시작 유지·원문 토큰 미저장·파일 권한·Origin 검사를 확인했다.

### 작업 2 검증

- 대화 API 미구현으로 신규 5개 시나리오 실패를 확인한 뒤 구현했다.
- backend 전체 40개 테스트와 ruff 통과. 실제 HTTP 연결 해제 후 1초 안에 공급자 정리 및 부분 응답 저장을 검증했다.
- 별도 owner 차단, 재시작 복원, 동시 전송 단일 실행, 동일 요청 중복 방지, 저장 실패 시 done 미발행, 페이지 경계와 문맥 제한을 확인했다.

### 작업 3 검증 (2026-09-26)

- 신규 대화 복원/전환/모바일 시나리오 3개 실패를 확인한 후 UI를 구현했다.
- 기존 E2E를 새 API 계약에 맞춰 옮겼다. 예시와 실제 대화의 초안도 각각 보존한다.
- 최초 세션 실패 후 복구 불가를 회귀 테스트로 재현해 수정했다. 복구 테스트는 전송 활성화를 기다린 뒤 입력한다.
- 늦은 조회 결과 격리, 페이지 경계 중복 제거와 읽는 위치 보존을 추가 검증했다.
- 웹 단위 테스트 17개·타입·lint·빌드 통과. 전체 E2E 27개 통과 후 추가 페이지 검증을 포함한 저장 기능 6개 통과.
- 저장소 테스트는 별도 test_storage.py 대신 test_session.py와 test_chat_persistence.py에 책임별로 배치했다.
- 개발 서버 수명 테스트가 사용자 DB를 열지 않도록 AGENT_SERVICE_DATABASE_PATH 주입과 회귀 테스트를 추가했다. backend 41개 통과.
- 실제 브라우저 합성 대화에서 전환/새로고침 복원, 세션 격리, 중단 복원, 재시도를 확인했다.
- Impeccable detector 지적 없음. 데스크톱·모바일 실화면 확인, 가로 넘침 없음.

### 작업 4 최종 검증 (2026-09-26)

- 자동 테스트 총 86개: backend 41, 웹 API/SSE 17, Playwright 28. Ruff·TypeScript·ESLint·프로덕션 빌드 통과.
- `./scripts/check.sh`의 backend/웹 단위/타입 검사는 통과했다. 마지막 lint가 임시 브라우저 검증 스크립트를 검사해 실패했으나,
  임시 파일을 Git 제외된 루트 var/로 옮긴 뒤 `npm run lint`, `npm run build`, 전체 E2E 28개를 통과했다.
- 브라우저 실제 모델 호출 6회: 한국어 대화, 별도 대화, 전환/새로고침 후 기억, 중단, 명시적 재시도,
  서버 재시작 뒤 기억을 확인했다. 복원 과정의 모델 요청 0회, 별도 브라우저 접근 404, JS 오류 0개.
- `backend/.venv/bin/python scripts/check_live_chat.py --run` 실제 호출 4회 통과:
  한국어 응답 첫 토큰 1.99초, 저장 문맥 확인 2.00초, 3개 조각 후 중단/저장 2.15초,
  중단 뒤 새 요청 첫 토큰 1.14초. 모두 합성 문장만 사용했다.
- UI 확인: 1440×900 데스크톱, 390×844 모바일과 메뉴. 모바일 실제 입력창 하단 791.4px,
  뷰포트 844px, 헤더 1개, 가로 넘침 없음. 즉시 뷰포트 변경 직후의 캡처 잔상은 새 모바일 컨텍스트로 재확인했다.
- 실제 화면: `docs/verification/step-04-desktop-chat.png`, `step-04-mobile-chat.png`, `step-04-mobile-list.png`.
- 브라우저 스크립트·세션 쿠키는 Git 제외된 var/에 두고, 합성 문장 화면만 검증 자료로 기록한다.
- 완료한 로컬 커밋: `6d8a00b` 저장소/세션, `9636990` 대화/응답 저장, `2c38f19` 화면/복원.
  이 최종 기록과 실행 스크립트·화면 자료는 별도 검증 커밋에 포함한다.
- 후속 작업은 5단계(채팅에서 실제 전화)의 세부 계획과 컨펌부터 시작한다.
