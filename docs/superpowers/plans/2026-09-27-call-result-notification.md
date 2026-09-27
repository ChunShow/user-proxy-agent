# 통화 종료 후 채팅 결과 자동 보고 Implementation Plan

> **For agentic workers:** Execute inline by default. Use
> `superpowers:subagent-driven-development` only when substantial independent
> tasks make delegation cheaper or safer. Steps use checkbox (`- [x]`) syntax.
> 이 저장소에서는 별도 합의 없이 병렬 에이전트를 사용하지 않는다.

**Goal:** 사용자가 다시 묻지 않아도 통화 종료 결과가 원래 채팅에 한 번 표시된다.

**Architecture:** 통화 종료 상태를 저장할 때 별도 결과 보고 작업을 등록한다. 백그라운드에서
현재 메인 텍스트 모델로 저장된 통화 근거를 요약하고, 같은 대화에 assistant 메시지로 저장한다.
전화 종료 처리와 요약 생성을 분리하고, 결과 보고에는 외부 실행 도구를 제공하지 않는다.

**Tech Stack:** 기존 FastAPI·SQLite·LangChain 텍스트 모델·React·Langfuse.

**Spec:** `docs/product.md`의 통화 결과·후속 요청이 같은 대화에 이어지는 경험과 업무/회선 상태 구분.

상태: completed — 계획 제시 후 사용자가 “진행해줘.”라고 명시적으로 승인했다.
승인 범위 안에서 직접 순차 구현·모사/합성 검증·로컬 커밋을 진행한다.

## 범위와 제약

- 새로 종료되는 통화마다 원래 대화에 한 번만 결과를 남긴다. 과거 종료 통화는 소급 게시하지 않는다.
- 확인된 답변·미확인 사항·회선 상태를 짧게 설명한다. 전사/모델 보고/실제 청취를 구분한다.
- 재발신·일정 등록·메일 발송이나 실행안 생성은 자동 보고가 수행하지 않는다.
  사용자가 후속 요청을 하면 기존 메인 채팅과 확인 카드 흐름으로 진행한다.
- 원문을 Langfuse에 보내지 않는다. 현재 소유권·Origin 검사와 저장된 근거의 외부 데이터 취급을 유지한다.
- 기존 메시지·통화 기록을 덮어쓰지 않는다. 모델 오류 시에도 회선 종료를 지연시키지 않는다.
- 디자인은 기존 assistant 메시지와 통화 카드 스타일을 사용한다. 결과 확인에 필요 없는 새 장식은 추가하지 않는다.
- 실제 발신·Google 쓰기는 이번 구현 검증 범위에 포함하지 않는다.

## 1. 결과 작업과 메시지의 중복 방지

**Files:** `backend/src/agent_service/calls/store.py`, `backend/src/agent_service/calls/reports.py` (신규),
`backend/src/agent_service/storage.py`, `backend/tests/calls/test_call_reports.py` (신규).

**Interfaces:** `report_store.enqueue(db, call)`는 terminal 상태의 통화에 대해 한 행만 등록한다.
`claim()`은 queued 한 건을 running으로 원자적으로 전환한다.
`complete(call_id, text)`는 소유 대화의 assistant 메시지 삽입과 completed 전환을 한 트랜잭션으로 처리한다.
고유 call_id 및 message_id 관계로 재시도·중복 이벤트에서 메시지가 늘지 않는다.

- [x] 통화가 ended/failed/canceled로 처음 전환될 때 결과 작업을 같은 트랜잭션에서 등록한다.
  unknown/ending은 종료 완료 보고 대상이 아니다. 기존 terminal 행 마이그레이션은 작업을 만들지 않는다.
- [x] 결과 메시지의 순번은 기존 저장 로직과 같은 트랜잭션 규칙으로 배정하고, 일반 채팅 저장과 경합해도
  충돌하지 않게 한다. 대화 소유권과 목록 갱신 시각을 유지한다.
- [x] 모사 검사: 중복 종료·동시 complete → 메시지 1건, owner 격리, 과거 종료 이력 제외,
  회선 미확인 시 보고 없음. `uv run pytest -q tests/calls/test_call_reports.py`.

## 2. 종료와 독립적인 요약 생성·복구

**Files:** `backend/src/agent_service/calls/reports.py`, `backend/src/agent_service/main.py`,
`backend/src/agent_service/observability.py`, `backend/tests/calls/test_call_reports.py`.

**Interfaces:** `CallReportManager.start()`/`shutdown()`이 단일 로컬 worker를 관리한다.
생성 입력은 통화 목적·종료 근거·요약 시점·최대 80개/12000자의 자동 전사와 잘림 표시다.
생성 결과는 길이가 제한된 일반 텍스트이며 도구 호출을 허용하지 않는다.

- [x] 현재 메인 모델 설정으로 도구 없는 결과 보고를 생성한다. 전체 생성은 30초 상한과
  자동 재시도 없음으로 제한한다. 통화 정리 코루틴은 모델 결과를 기다리지 않는다.
- [x] 모델 오류/상한/빈 응답이면 확인된 회선 상태와 '요약을 만들지 못했습니다. 통화 내용을 확인해 주세요.'를
  저장한다. 실패·취소 통화에는 해당 상태를 표현하며 성공으로 바꾸지 않는다.
- [x] 재시작 시 queued만 처리한다. 중단된 running은 고정 안내로 완료해 모델 호출을 반복하지 않는다.
  메시지 저장 중 충돌·재시작에도 이전 단계의 원자성과 중복 방지를 사용한다.
- [x] Langfuse `call-result`는 원 발신 요청의 trace와 연결하고 상태·시간·모델명만 추적한다.
- [x] 검증: 모델 지연 중 회선 종료 가능, 모델 실패/빈 응답/취소의 고정 안내, 재시작 복구,
  중단된 생성 재호출 없음, 모델에 전화·Google 실행 도구가 없음.

## 3. 새로고침 없이 같은 채팅에 표시

**Files:** `backend/src/agent_service/calls/store.py`, `web/src/chat/calls.ts`,
`web/src/chat/useCalls.ts`, `web/src/chat/useChat.ts`, `web/src/App.tsx`,
`web/e2e/phone-calls.spec.ts`.

**Interfaces:** 통화 조회 결과에 `result_message_id: string | null`을 추가한다.
기존 통화 폴링이 새 ID를 발견하면 해당 대화의 메시지를 다시 읽어 ID 기준으로 병합한다.
현재 대화 ID·조회 세대가 일치하는 경우에만 적용한다.

- [x] 입력 중인 초안·스트리밍 중인 로컬 메시지·과거 페이지를 보존한다. 스트리밍 중에는
  결과 메시지 재조회를 완료 직후로 미루고, 응답 완료 시 한 번 병합한다.
- [x] 다른 대화 전환·숨긴 탭 복귀·새로고침 후 같은 저장 메시지가 복원된다. 과거 내용을
  읽고 있으면 스크롤을 강제로 이동하지 않고 기존 최신 메시지 이동 동작을 따른다.
- [x] 검증: 모사 통화 종료 후 자동 표시, 중복 폴링 시 1건, 채팅과 동시 도착,
  대화 전환 중 늦은 응답 무시, 새로고침 복원, 모바일 입력창 유지.

## 4. 통합 확인과 기록

- [x] 관련 backend 검사, 웹 단위·타입·정적 검사·빌드. 브라우저 동작은 사용 가능한
  브라우저 도구로 확인하고 자동 E2E 실행 여부를 별도 기록한다.
- [x] 실제 텍스트 모델 + 합성 통화 기록으로 보고 생성만 검증한다. 실제 전화·Google 실행 없음.
- [x] 활성 통화·채팅이 없을 때 서버 적용, 기존 기록 복원 확인, 제품·로드맵·README 갱신 및 로컬 커밋.

## 완료 기준

새 통화가 terminal 상태가 되면 사용자의 추가 메시지 없이 결과가 한 번 표시된다.
모델 실패·재시작에도 회선 정리는 계속되고 완료 메시지가 중복되지 않는다.
일정·메일 후속 실행 권한은 기존 사용자 확인 흐름을 유지한다.


## 구현·검증 기록 (2026-09-27)

- terminal 전환과 같은 SQLite 트랜잭션에서 작업을 등록하고, 단일 로컬 worker가 도구 없는
  현재 메인 모델을 호출한다. 메시지 삽입과 완료 처리도 한 트랜잭션이며 통화 version을 갱신한다.
- `report_store.py`를 별도로 분리했다. 메시지 응답에 seq와 kind를 추가하고 자동 보고를 제외한
  마지막 채팅 응답만 재시도한다. 재시도 문맥은 해당 요청 시점까지 유지한다.
- 실제 Sol 응답이 문자열 대신 reasoning/text 블록 배열일 때 요약 실패로 처리되는 문제를
  합성 검사에서 발견했다. 실패 테스트 후 사용자용 text 블록만 추출하도록 수정했다.
- `./scripts/check.sh`: backend **286 passed**, 웹 단위 **32 passed**, Ruff·TypeScript·ESLint·빌드 통과.
  일반 검사는 `/dev/null` 환경 파일을 사용하고 Ruff 설정은 프로젝트 파일로 고정했다.
  변경 전 전체 검사는 로컬 Langfuse SDK 종료 대기에서 멈춰 중단했다. 원인을 스택으로 확인한 뒤
  실제 자격증명을 읽지 않는 일반 검증 명령으로 다시 실행해 통과했다.
- 실제 gpt-5.6-sol + 합성 전사 + 임시 DB에서 도구 없는 결과 생성과 저장을 확인했다.
  “내일 오후 6시에 테스트 가능” 답변과 회선 종료를 설명했고 오류 fallback이 아니었다.
  Langfuse 연결 관계/도구 없음/원문 비전송은 모사 transport 및 기존 추적 회귀 검사로 확인했다.
- 별도 9012/5194 테스트 서버·DB·세션 쿠키에서 CUA 브라우저 검증:
  종료 후 자동 표시 1건, 작성 중 초안 유지, 대화 전환 시 결과 격리와 초안 복원,
  일반 응답 스트리밍 중 결과 갱신 대기, 응답 완료 뒤 병합, 새로고침 복원.
  390×844 화면에서 자동 결과·입력창·재시도를 확인했다. 기존 응답을 재시도해도
  결과 메시지가 중복되거나 사라지지 않았다. 임시 viewport와 서버는 정리했다.
- Playwright E2E 사례는 추가했지만 자동 E2E 실행은 하지 않았다. 브라우저 검증은 위 CUA 실행이다.
  숨긴 탭 복귀/과거 스크롤 유지는 기존 폴링·스크롤 로직을 그대로 사용하며 별도 재현은 하지 않았다.
- 활성 통화 0건·채팅 생성 0건에서 SQLite 백업 후 9010 서버에 적용했다.
  실제 웹 5180 새로고침으로 기존 대화·통화 기록과 서버 연결 복원을 확인했다.
  과거 terminal 통화에 자동 결과 작업이 소급 생성되지 않았음을 DB에서 확인했다.

### 제한

새 실회선 통화에서 자동 보고가 도착하는 검증은 아직 하지 않았다. 이번 실행은 추가 발신이나
Google API 조회·등록·발송을 수행하지 않았다. 모델 요약의 표현은 비결정적이며 실제 청취를
증명하지 않는다. 단일 프로세스 로컬 worker를 전제로 하며 여러 backend worker 실행은 지원하지 않는다.
생성 오류·30초 초과·생성 중 서버 중단은 고정 안내로 한 번 완료하고 자동 재생성하지 않는다.
