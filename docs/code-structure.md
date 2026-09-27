# 코드 구조와 의존 방향

## 백엔드

`backend/src/agent_service/`는 기능별로 나눈 하나의 FastAPI 서비스다.

| 경로 | 책임 |
| --- | --- |
| `agents/factory.py` | 명시된 프롬프트와 도구로 DeepAgents 생성, 셸·파일·하위 에이전트 도구 제외 |
| `chat/` | 사용자 채팅의 문맥·스트리밍·중단·제목, 채팅용 도구 구성 |
| `chat/prompts.py` | 메인 채팅 정책 |
| `calls/` | ClawOps 연결, 음성 중계, 위임, 통화 상태·전사·결과 보고 |
| `calls/prompts.py` | Live 발화·업무 위임·통화 결과 보고 정책 |
| `integrations/` | Google 연결·토큰·조회와 제공 도구 |
| `actions/` | 일정·메일 실행안, 사용자 확인 후 실행과 결과 저장 |
| `storage.py`, `session.py` | 대화 저장과 브라우저 세션 소유권 |
| `observability.py` | 선택적 로컬 Langfuse 메타데이터 추적 |
| `main.py` | API·관리자 연결과 서버 수명 관리 |

채팅 실행과 통화 위임은 공통 `agents.factory`에 의존한다. 팩토리는 채팅·통화·Google 모듈을
가져오지 않는다. `chat.runtime.build_agent`는 기존 채팅 기본 정책을 선택하는 얇은 진입점이며,
통화는 이를 통하지 않고 자신의 명시적 프롬프트로 공통 팩토리를 사용한다.

프롬프트는 검토하기 쉽게 실행 코드와 분리했으며 설정 파일로 동적 로딩하지 않는다.
모델 연결, timeout, 도구 실행, 음성 상태 머신은 원래 담당 모듈에 유지한다.
전송 프로토콜·DB 스키마·외부 API 계약은 이번 구조 정리에서 변경하지 않았다.

## 웹

| 경로 | 책임 |
| --- | --- |
| `web/src/api/` | 공통 JSON 요청·오류 변환·health 조회와 관련 검사 |
| `web/src/chat/` | 채팅 화면, 메시지·대화 목록·SSE·입력 상태 |
| `web/src/calls/` | 통화 카드·음성 듣기·전사·통화 API·상태 훅과 관련 검사 |
| `web/src/integrations/` | 앱 연결 화면과 Google 연결 API |
| `web/src/actions/` | 일정·메일 확인 카드 |
| `web/src/components/` | 공통 표시 요소 |
| `web/src/App.tsx` | 채팅·통화·사용자 답변의 연결 |

대화·통화·앱 연결·실행 카드는 공통 `api/request.ts`를 사용한다. 공통 API가 특정 기능을
가져오지 않도록 유지한다. `ApiError`는 서버 코드만으로 사용자 문구와 재시도 가능 여부를 정한다.
SSE 수신은 채팅 전용 `chat/stream.ts`에서, 통화 음성은 `calls/`에서 처리한다.

단위 검사는 대상 모듈 옆에 두고 브라우저 흐름 검사는 `web/e2e/`에 둔다.
화면 DOM과 CSS는 이동하지 않았다. 현재 통화 카드 스타일도 기존 `chat/chat.css`에 있으며,
시각 디자인 변경과 함께 필요할 때 따로 분리할 수 있다.

## 실행·평가

기존 `scripts/` 명령 경로는 유지한다. [실행·평가 안내](../scripts/README.md)를 따른다.
`data/`와 `var/`의 위치도 유지하므로 기존 DB·Google 연결 키·평가 음성을 옮길 필요가 없다.
과거 계획 문서의 파일 경로는 당시 구조를 기록한 것이며, 현재 경로는 이 문서와 실제 소스를 따른다.
