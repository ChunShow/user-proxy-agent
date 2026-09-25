# 6단계 개정 제안: GPT-Live 전환과 통화 중 사용자 확인

## 상태와 승인 기록

- 상태: `proposed` — 구현 전 세부 계획 검토 대기.
- 요청: 2026-09-26 사용자 “live-1모델로 연결을 변경해서 진행해줘. 이것도 같이.”
  통화 모델의 DeepAgents 위임, 일정 확인, 메인 채팅 질문과 답변의 통화 반영을 지정했다.
- 위 요청은 목표와 설계 방향의 근거다. 이 문서 작성 후의 세부 계획 승인은 아직 받지 않았다.
- 일정 확인 방식: 계획 준비 중 사용자 “우선 채팅으로 일정 확인”을 선택했다.
  이번에는 외부 캘린더 OAuth/조회 없이 사용자에게 질문하고 답변을 받는다.
- 순차 직접 실행하고 검증된 작은 단위마다 로컬 Git에 기록한다.

## 목적과 완료할 사용자 흐름

메인 입력은 텍스트 채팅으로 유지한다. 전화 상대의 음성은 ClawOps와 `gpt-live-1` 사이에서
직접 전달한다. Live가 업무 판단을 요청하면 DeepAgents가 처리하고 결과를 Live에 돌려준다.
예: 상대가 “화요일 오후 3시가 가능한가요?”라고 묻는다 → DeepAgents가 사용자가 이미
제공한 일정 조건 또는 연결된 일정 조회 결과를 확인한다 → 충분하면 통화에 답을 돌려준다.
정보가 없거나 사용자 결정이 필요하면 메인 채팅에 질문을 표시하고, 사용자가 답하면
그 답을 근거로 통화에 전달한다. 확인 전에는 가능하다고 약속하지 않는다.

## 현재 코드와 연결 점검 결과

- `calls/realtime.py`: Azure URL만 허용하며 Realtime `session.update` 계약을 사용한다.
- `calls/bridge.py`: Realtime response/item 완료와 VAD 이벤트에 의존한다.
  Live 이벤트에 그대로 사용할 수 없다.
- `chat/runtime.py`: DeepAgents와 텍스트 모델은 연결되어 있으나 통화 중 위임을 받는 경로가 없다.
- `calls/store.py`, `calls/routes.py`, `web/src/chat/PhoneCallCard.tsx`:
  통화 상태·종료는 지원하며 질문/답변 상태는 아직 없다.
- 로컬 `.env`는 Azure 통화 설정이 있고 Live 전용 URL·키 및 `OPENAI_API_KEY`는 없다.
  키 값이나 비공개 호스트는 출력하지 않았다. 기존 Azure 키를 OpenAI 서버에 보내지 않는다.
- 실제 캘린더 연결은 아직 없다. 사용자는 이번 단계에서 채팅 확인 방식을 선택했다.

## 구조 선택

선택: **GPT-Live client delegation + 기존 DeepAgents 런타임 재사용**.
`responses` 위임은 OpenAI가 백엔드 호출을 관리하지만 기존 DeepAgents 도구·사용자 상태를
재사용하려는 목적에는 client 방식이 적합하다. Realtime 함수 도구 유지안은 사용자가
Live 전환을 명시했으므로 이번 목표로 선택하지 않는다.

```text
사용자 채팅 ↔ DeepAgents / 저장된 대화와 사용자 조건
                  ↕ 통화별 위임·확인 요청·결과
              CallManager ↔ GPT-Live ↔ ClawOps ↔ 통화 상대
                  ↕
            SQLite 이벤트·질문·답변 / 웹 조회
```

DeepAgents는 동일한 모델 설정과 공통 도구 구성을 재사용하되, 일반 채팅 실행과 통화 위임
실행의 메시지 상태를 분리한다. 통화 문맥을 같은 채팅 실행에 동시 변경하지 않는다.
서버가 owner/conversation/call/delegation ID를 주입하며 모델이 이 식별자를 선택하지 않는다.
전화 상대의 발화를 사용자의 새로운 권한 부여로 취급하지 않는다.

## 범위와 세부 계약

### A. Live 연결과 음성

- `calls/live.py` 및 `calls/live_bridge.py`를 추가하고 manager의 음성 구현 선택을 분리한다.
- `CALL_LIVE_BASE_URL`, `CALL_LIVE_API_KEY`, `CALL_LIVE_MODEL=gpt-live-1`,
  `CALL_LIVE_VOICE`를 서버 전용 설정으로 도입한다. 기존 `MODEL_*`는 유지한다.
- 공식 연결은 `wss://api.openai.com/v1/live/sessions`, Bearer 인증,
  `session.start` → `session.started`이며 `delegation.type=client`를 사용한다.
  다른 공급자 엔드포인트는 해당 공급자의 Live 계약과 접근 권한 확인 후에만 허용한다.
- ClawOps와 맞는 `audio/pcmu`, 8000Hz를 양방향에 사용한다. 별도 STT/TTS는 추가하지 않는다.
- Live 자체의 입출력 transcript delta를 위임 문맥에 사용한다. 원시 음성 녹음은 추가하지 않는다.
- Live는 Realtime과 달리 발화 완료/item ID 이벤트가 없다. 전사 조각 사이의 간격이나
  명령 접수 ACK를 음성 재생 완료로 간주하지 않는다. 생성 음성과 실제 ClawOps 재생량을 분리한다.
- 발신 전 Live 준비 실패는 실제 발신 전에 차단한다. Live 오류를 Realtime으로 자동 우회하지 않는다.
- 기존 Realtime 어댑터는 회귀 비교용으로 보존하되 Live 설정이 검증되면 기본 경로를 Live로 전환한다.

### B. DeepAgents 위임과 통화 도구

- `session.delegation.created` 수신 시 input/output transcript와 현재 목적·사용자 조건·
  최신 task revision을 읽어 `calls/delegation.py`의 독립 작업을 시작한다.
- 위임 이벤트 자체에는 요청 문장이 없으므로 timestamp와 저장된 전사를 함께 사용한다.
  전사가 아직 없으면 이벤트를 보관하고 기다리며, 근거 없이 작업을 만들지 않는다.
- 위임 실행은 같은 텍스트 모델 설정을 쓰는 DeepAgents다. 허용 도구는 일정 확인,
  사용자 질문, 현재 회선 DTMF·종료 요청으로 제한하며 새 발신은 허용하지 않는다.
- 결과는 `session.commentary.append`로 말할 내용, `session.thinking.append`로 상태 정보를
  구분해 반환한다. Live가 확인 전 성공이나 예약 완료를 말하지 않도록 프롬프트를 분리한다.
- 사용자 조건 변경 후 이전 revision 결과는 폐기한다. 중복 delegation ID는 재실행하지 않는다.
  취소/종료 후 완료된 백엔드 결과는 해당 통화에 보내지 않는다.
- DTMF는 최근 ARS 문맥과 중복 방지 기록을 검사한 뒤 기존 ClawOps 제어를 사용한다.
- 목표 달성 종료 요청은 저장 후 마지막 인사를 요청한다. 로컬 출력 음성 활동과 재생 큐,
  ClawOps 재생 확인을 함께 추적한다. 발화 경계를 확정할 수 없으면 제한 시간 내 종료 정리를
  수행하되 마지막 인사 전달이나 성공을 확정 표시하지 않는다. 전사만으로 완료를 판단하지 않는다.
- 직접 종료는 DeepAgents/Live 응답을 기다리지 않고 기존 서버 종료 경로로 처리한다.

### C. 사용자 확인과 메인 채팅

- SQLite에 delegation 및 confirmation 레코드를 추가한다. 기존 대화/통화 데이터는 보존한다.
- confirmation 필드: ID, owner/conversation/call/delegation, task revision, 질문,
  선택지, 답변, 상태, 생성/만료 시각. 상태는 pending/answered/applied/expired/canceled/failed.
- `GET /api/calls/{id}/activity?after=...`: 소유자에게 순서 있는 작업 이벤트와 질문을 제공한다.
- `POST /api/calls/{id}/confirmations/{question_id}/answer`:
  `{answer, expected_revision, request_id}`. 소유권·활성 통화·만료·중복을 서버에서 검증한다.
  답변 저장과 위임 재개를 분리하되 재시도는 동일 결과를 반환한다.
- 한 통화에는 한 번에 사용자 질문 하나만 열고, 답변 대기는 기본 60초 및 통화 잔여 시간 중
  짧은 쪽으로 제한한다. 무응답은 동의로 처리하지 않고 미확인 결과를 통화에 돌려준다.
- 일반 채팅 안에 “통화 중 확인이 필요해요”와 질문·선택지/자유 입력을 표시한다.
  답변할 질문을 명시한 상태에서 기존 채팅 입력을 사용할 수 있게 한다.
  별도 일반 채팅 메시지를 임의로 통화 답변이나 상대에게 읽을 문장으로 취급하지 않는다.
- 답변 저장 → 전달 중 → 반영됨/반영하지 못함을 표시한다. Live ACK는 명령 접수이며
  상대가 들었다는 뜻으로 표시하지 않는다. 새로고침 시 질문과 상태를 복원한다.
- 다른 대화를 보고 있어도 진행 통화 영역에서 질문 도착을 알리고 원래 대화로 이동한다.
- UI는 기존 간결한 메신저 스타일을 사용한다. 키보드 제출·모바일·중복 클릭·실패 후 재시도를 검증한다.
  구현 전에 프로젝트 지정 frontend-design 스킬을 적용한다.

### D. 일정 확인의 근거

- 우선 기존 사용자 대화에서 명시한 조건과 사용자 확인 요청을 연결한다.
  캘린더가 연결되지 않은 상태를 “일정 없음”으로 반환하지 않는다.
- Google/Outlook 실제 계정 연결은 이번 범위에서 제외한다. 사용자가 나중에 연결을 요청하면
  9단계에서 제공자와 실제 조회 어댑터·인증을 별도로 계획한다.
- 이번 승인안의 자동화 완료 기준은 **통화 → DeepAgents → 사용자 일정 질문 → 답변 → 통화 반영**이다.
  실제 캘린더 조회 완료는 별도 인증과 실제 조회 증거가 있어야 기록한다. 일정 생성/수정은 포함하지 않는다.

## 작은 작업 단위와 대상 파일

1. Live 설정·접속·종료 계약 테스트 → `calls/settings.py`, `live.py`, `.env.example`, 발신 없는 점검 도구.
2. Live 오디오/전사/재생 어댑터 → `live_bridge.py`, `media.py`, `manager.py`, 관련 계약 테스트.
3. DeepAgents 위임 실행·도구·revision 취소 → `calls/delegation.py`, `chat/runtime.py`, `voice_tools.py`.
4. 영속 질문·답변·소유권 API → `storage.py`, `calls/store.py`, `calls/routes.py`, 스키마/수명 테스트.
5. 채팅 질문과 응답 표시 → `web/src/api.ts`, `chat/useCalls.ts`, `useChat.ts`,
   `PhoneCallCard.tsx`, `ChatView.tsx`, 필요한 컴포넌트와 CSS, 브라우저 테스트.
6. 통합 검증·발신 없는 실제 Live 연결·문서 갱신. 각 단위는 관련 검증 후 독립 로컬 커밋한다.

## 검증과 완료 기준

- 모사 프로토콜: 시작/실패/종료, μ-law 음성 순서·큐 제한, 중복/늦은 전사·위임,
  오디오 없는 결과, 상대 끼어들기, DTMF 중복 방지, 종료 중 답변·백엔드 완료.
- 서버: 타 소유자 접근 차단, 질문 답변 멱등성, 만료/취소/수정 후 오래된 결과 폐기,
  일반 채팅과 위임의 동시 실행, 서버 재시작 후 재발신 및 질문 자동 승인 없음.
- UI: 질문 표시·키보드/선택지 답변·새로고침 복원·일반 채팅 병행·직접 종료·모바일 화면.
- 실제 모델(발신 없음): 권한 있는 Live 엔드포인트와 키가 준비되면 짧은 합성 입력 음성을
  실시간 속도로 전송하여 실제 delegation → DeepAgents → 확인 질문 → 테스트 답변 → Live
  음성 반환을 확인한다. 합성 음성 시험임을 기록하고 실제 사람 통화와 구분한다.
- 기존 전화 수명/직접 종료 회귀, backend/web 정적 검사 및 빌드와 관련 E2E를 실행한다.
- 음성 입력→위임 수신→DeepAgents 결과→사용자 답변→Live 음성→ClawOps 재생 시점을
  각각 계측한다. 사용자 응답 대기와 시스템 지연을 분리한다. 새 모델이라는 이유로 품질 개선을 단정하지 않는다.
- 실제 발신은 이번 계획 검증에 포함하지 않는다. 모델/API와 모사 검증 후 사용자가
  대상·목적을 지정한 실통화로 회선 동작과 체감 품질을 확인한다. 이전 2회 승인은 이미 사용했다.
- API 접근이 없으면 구현/모사 통과와 실제 연결 미검증을 구분하고 연결 완료라고 보고하지 않는다.

## 로드맵 영향과 운영

이 변경을 6단계 확장안으로 제안한다. 위임에 필요한 전사와 8단계 사용자 확인 일부를 앞당긴다.
완전한 통화 전사 UI·브라우저 청취·임의 추가 지시·실제 앱 OAuth는 각각 남은 후속 범위다.
통화 1개 제한, 기존 소유권, 180초 상한, 중복 발신 방지는 유지한다.
Live 키는 로컬 `.env`에 등록하고 Git/화면/일반 로그에 노출하지 않는다.
공식 OpenAI Live 접근 또는 계약이 확인된 Live 호환 엔드포인트가 필요하다.

## 공식 근거 (2026-09-26 확인)

- [Live 전환](https://developers.openai.com/api/docs/guides/live-migration)
- [WebSocket 시작·G.711·음성 이벤트](https://developers.openai.com/api/docs/guides/voice-websockets)
- [client delegation와 문맥 관리](https://developers.openai.com/api/docs/guides/live-delegation)
- [전사·세션·안내 발화](https://developers.openai.com/api/docs/guides/live-conversations)

공식 Live API의 이벤트와 기존 Azure Realtime 계약을 혼용하지 않는다.
