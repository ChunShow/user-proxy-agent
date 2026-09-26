# 8단계: 채팅에서 진행 중 통화에 추가 지시

> **실행 방식:** 컨펌 후 직접 순차 구현한다. 각 작업은 실패 재현 → 구현 → 검증 → 로컬 커밋으로 진행한다.

**상태:** proposed
**목표:** 같은 채팅에서 사용자가 새 조건을 전달하고, 진행 중 통화에 전달됐는지 확인한다.
**구조:** 메인 DeepAgents의 도구 → 소유권을 확인한 지시 저장 → 실행 중 Live 통화에 전달 → ACK에 따른 상태 갱신.
기존 질문·답변과 위임에는 조건 버전을 연결해 이전 조건의 결과를 폐기한다.
**기술:** 기존 FastAPI·SQLite·DeepAgents·GPT-Live·React. 새 외부 서비스나 패키지는 추가하지 않는다.
**설계 근거:** `docs/product.md`의 통화 중 사용자 개입, `docs/roadmap.md`의 8단계 및 아래 계약.

## 요청과 승인 기록

사용자가 “7번은 나중에 하고 8번부터 해줘”라고 요청했다.
7단계 음성 듣기는 보류하고, 6A 질문·답변/6B 전사 화면을 바탕으로 8단계를 먼저 진행한다.
이 문서는 새 단계의 구체적인 계획이며 구현 컨펌 대기 상태다.
자동 종료 후속 조사·검증은 사용자의 앞선 요청대로 후순위에 둔다.

## 사용자 흐름과 범위

- 현재 대화의 통화가 연결돼 있을 때 “가격도 물어봐”, “오후 6시로 바꿔서 확인해줘”라고 입력한다.
- 메인 에이전트가 명확한 통화 지시일 때 `update_phone_call`을 호출한다.
  일반 질문은 일반 채팅으로 답하고, 대상·조건이 불명확하면 채팅에서 확인한다.
- 기존 채팅 메시지는 그대로 저장하고 통화 카드에는 전달할 지시와 상태를 간결하게 표시한다.
  별도 입력창·모드 전환은 추가하지 않는다. 전달 상태는 새로고침 후에도 복원한다.
- 원문을 기계적으로 읽는 방송 기능이 아니라, 통화 도우미가 대화에 반영할 사용자 조건이다.
  이미 말한 내용은 되돌릴 수 없으며 필요한 경우 변경된 조건을 설명하도록 한다.
- 이번에는 **현재 대화의 연결된 Live 통화 1건**만 지원한다.
  준비/발신 중·종료 대기/종료됨·기존 Realtime 모드에는 적용 불가를 반환한다.
- 한 통화에서 한 번에 지시 1건을 전달한다. 전달 중 새 지시는 접수하지 않고 기다린 뒤 다시 요청하도록 알린다.
  전달이 끝나면 다음 지시를 받을 수 있다. 여러 탭의 동시 접수도 DB에서 동일하게 제한한다.
- 지시 전송 중에도 기존 직접 종료 버튼은 즉시 사용할 수 있다.
  “통화 끊어줘”는 기존 종료 도구로 처리한다. 추가 지시로 종료된 통화를 되살리거나 재발신하지 않는다.
- 실제 발신·실제 모델 유료 검증은 이번 구현 승인 범위에 포함하지 않는다. 모사 통합 검사 후 별도 제시한다.

## 전달과 상태 계약

`update_phone_call(call_id: str, instruction: str)`:

- owner/conversation/source_user_message_id는 `CallContext`에서 바인딩한다.
- 1~2,000자 지시만 받는다. source 메시지가 실제 해당 대화의 사용자 메시지인지 서버에서 확인한다.
- 통화별 최대 30건을 저장하고 `UNIQUE(call_id, source_user_message_id)`로 중복 실행을 막는다.
  같은 메시지의 동일 지시는 기존 기록을 반환하고, 다른 내용의 재호출은 충돌로 거절한다.
- 반환값은 `instruction_id`, `call_id`, `status`, 오류 시 `error`와 자동 재시도 금지 정보다.
  전달할 내용은 사용자의 실제 메시지 범위 안에서 구성하도록 도구 설명·메인 프롬프트에 명시한다.

저장 상태와 UI 문구:

| 상태 | 의미 | 표시 |
| --- | --- | --- |
| pending | 접수됐고 전송 전 | 전달 대기 중 |
| sending | 해당 연결로 전송 시작 | 전달 중 |
| delivered | 필요한 명령 ACK를 받고 조건 갱신 완료 | 통화 도우미에게 전달됨 |
| not_applied | 전송 전 종료/지원 불가/취소 | 전달하지 못함 + 짧은 사유 |
| delivery_unknown | 전송 후 ACK 누락·연결 종료·재시작으로 확정 불가 | 전달 여부 확인 필요 |

`delivered`는 모델의 명령 수신 확인이며 상대방 발화·청취·업무 성공의 증거가 아니다.
실패/미확인 지시는 자동 재전송하지 않는다. 사용자는 결과를 보고 새 메시지로 다시 요청할 수 있다.

## 작업 1: 지시 저장과 소유권·중복 방지

**파일:** 새 `backend/src/agent_service/calls/instructions.py`, 기존 `calls/live_store.py`,
`calls/store.py`, `calls/manager.py`, 새 `backend/tests/calls/test_call_instructions.py`.

- [ ] `phone_calls.condition_revision`과 `call_delegations.condition_revision`을 기본값 0으로
  마이그레이션한다. 기존 통화·질문·전사를 보존하며 반복 시작 시 마이그레이션이 안전해야 한다.
- [ ] `call_instructions`에 id/call_id/source_user_message_id/text/status/condition_revision/
  error_code/created_at/updated_at을 저장한다. `InstructionStore.submit(context, call_id, text)`는
  소유권·같은 대화·사용자 메시지·상태·한 건 전달 중 제한·건수 제한을 한 트랜잭션에서 검증한다.
- [ ] 접수 시 조건 버전을 올리고, 기존 running 위임과 pending/answered 질문을 취소한다.
  오래된 질문 답변은 적용 불가를 반환한다. 새 위임은 현재 조건 버전을 캡처하고 `_valid`가 확인한다.
- [ ] 기존 call 조회/목록에 `instructions` 배열을 포함하고 변경 시 call.version을 올린다.
  내용·상태는 기존 소유자만 읽을 수 있다. 전사 activity 조회와 혼동하지 않는다.
- [ ] 중복 접수·충돌·다른 소유자/대화·이미 종료된 통화·동시 요청·기존 DB 업그레이드와
  늦은 질문 답변 거절을 실패 검사로 재현하고 수정한다.

**검증:** `cd backend && uv run --locked pytest tests/calls/test_call_instructions.py tests/calls/test_live_store.py tests/calls/test_call_store.py -q`

## 작업 2: 실행 중 통화 전달과 이전 조건 무효화

**파일:** `calls/manager.py`, `calls/delegation.py`, `calls/live_bridge.py`, `calls/instructions.py`,
`backend/tests/calls/test_live_delegation.py`, `test_live_bridge.py`, `test_manager.py`.

- [ ] manager가 실행 중 Live coordinator를 등록/해제한다. `update(owner, conversation_id,
  source_user_message_id, call_id, instruction)`는 실제 세션과 연결 상태를 검증해 저장·접수만 반환한다.
  모델 ACK 대기로 채팅 요청 수명을 묶지 않는다. registry가 없는 통화는 접수하지 않는다.
- [ ] 통화 수명에 속한 별도 작업이 pending 지시를 읽어 sending으로 전환하고 전송한다.
  receive 루프는 ACK를 계속 처리하며, 음성 루프와 직접 종료는 전송 대기에 막히지 않는다.
- [ ] 조건 변경과 위임 적용을 통화별로 직렬화한다. 새 조건을 받으면 기존 coordinator 작업을
  취소하고, 다음 위임 context에 전달 완료된 사용자 지시를 순서대로 포함한다.
  최신 지시가 미전달/미확인이면 이전 조건으로 작업을 계속하지 않고 변경 조건 확인이 필요하다고 처리한다.
  불확실한 지시를 다음 위임에서 자동 재전송하는 우회 경로도 만들지 않는다.
  오래된 도구 실행/종료 요청/결과 송신은 조건 버전 검사로 거절한다.
- [ ] Live에는 대기 상태 해제와 새 사용자 조건을 명확히 구분해 전달한다. 조건 변경에 따른
  새로운 업무 판단은 기존 client delegation으로 요청하도록 지시한다.
  이미 생성/송신한 음성을 소급 취소했다고 주장하지 않는다.
- [ ] 조건 갱신 중 모델의 종료 요청은 성공으로 접수하지 않고 갱신 후 다시 판단하도록 반환한다.
  이미 마지막 인사/자동 종료 대기 중이면 새 지시를 거절한다. 기존 자동 종료 취소 정책은 변경하지 않는다.
- [ ] 명령 ACK 뒤 delivered를 기록한다. 종료와 ACK가 경합하면 저장된 상태를 확인해 미확인/미전달로 정리한다.
  전송 도중 타임아웃·취소·서버 재시작은 sending → delivery_unknown, pending → not_applied로 처리한다.
  수신 여부를 알 수 없는 경우 재송신하지 않는다.
- [ ] ACK 지연·누락, 지시 도착과 이전 결과/질문/종료의 경합, 직접 종료, 재시작, 다음 위임의
  조건 유지 등을 모사 모델/매체와 실제 coordinator/store 조합으로 검사한다.

**검증:** `cd backend && uv run --locked pytest tests/calls/test_call_instructions.py tests/calls/test_live_delegation.py tests/calls/test_live_bridge.py tests/calls/test_manager.py -q`

## 작업 3: 메인 채팅 도구와 통화 카드

**파일:** `calls/tools.py`, `chat/runtime.py`, `backend/tests/calls/test_agent_tools.py`,
`web/src/chat/calls.ts`, `PhoneCallCard.tsx`, 필요 시 `chat.css`, 새 `web/e2e/call-instructions.spec.ts`.

- [ ] `update_phone_call`을 메인 에이전트에 연결하고 call_history에 해당 대화의 최신 지시 상태를
  제공한다. 일반 채팅은 무조건 전화에 보내지 않으며, 접수와 실제 전달을 구분해 답하도록 한다.
- [ ] 현재 통화 ID로 도구를 호출하는 모델 모사 응답을 통해 source 메시지 바인딩·중복 실행·
  권한 오류·종료된 통화 거절을 실제 채팅 API까지 연결해 검사한다.
- [ ] 기존 간결한 통화 카드에 추가 지시 내용과 상태만 표시한다. 원문은 일반 텍스트로 렌더링하고
  전사와 시각적으로 구분한다. 기존 질문 카드·전사·직접 종료가 계속 조작 가능해야 한다.
  `frontend-design` 스킬의 기존 디자인·문구 원칙을 적용하며 새 색상 체계나 화면을 만들지 않는다.
- [ ] 접수/전달/미확인 상태 변화, 새로고침, 다른 대화 전환, 느린 이전 응답, 모바일에서의 읽기와
  종료 버튼 접근성을 브라우저에서 검사한다. 합성 데이터만 캡처한다.
- [ ] 전체 검사와 관련 브라우저 검사를 통과한 뒤 단계 결과와 제한을 문서에 남기고 로컬 커밋한다.

**검증:** `./scripts/check.sh`

`cd web && PLAYWRIGHT_CHANNEL=chrome npm run test:e2e -- e2e/call-instructions.spec.ts e2e/phone-calls.spec.ts e2e/call-transcript.spec.ts`

## 완료 기준과 후속 검증

모사 통합 시험에서 같은 채팅의 추가 지시가 해당 Live 세션에 한 번 전달되고,
ACK 전에는 완료로 표시하지 않으며, 갱신 전 조건의 늦은 결과가 새 조건을 덮어쓰지 않아야 한다.
기존 질문 응답·채팅·전사·직접 종료가 유지돼야 한다.
실제 모델의 지시 해석과 실통화 반영 성공은 모사 검사 결과와 구분한다.
구현 후 본인 번호의 실통화 1회에서 새 조건을 입력하는 검증 범위를 별도로 제시한다.
