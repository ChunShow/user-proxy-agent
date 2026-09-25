# user proxy agent

사용자가 채팅으로 일을 맡기면 실제 앱과 전화로 처리하고, 진행 중인 통화를 읽거나
들으며 추가 지시·종료로 개입할 수 있는 개인 비서 서비스.

현재는 **4단계: 대화 저장·이어가기**까지 구현했다. 실제 모델의 스트리밍 응답과 중단·재시도,
새 대화·이전 대화 목록, 새로고침·서버 재시작 후 문맥 복원을 지원한다. 실제 전화·앱 연동은 후속 단계다.
서버 연결 상태·재시도는 health API를 사용하며, 모델 연결은 실제 메시지를 보내 확인한다.

## 설치

Python 3.12 이상, uv, Node.js 24 LTS 권장(24–26 지원), npm이 필요하다.
Python은 `backend/.python-version`, Node는 `.node-version`에 권장 버전을 기록했다.

프로젝트 루트에서 처음 한 번 실행한다.

```bash
cd backend
uv sync --locked
cd ../web
npm ci
cd ..
```

의존성은 `backend/uv.lock`과 `web/package-lock.json`으로 고정한다.
형제 프로젝트의 가상환경이나 코드에 의존하지 않는다.

## 모델 설정

프로젝트 루트의 `.env`에서 모델 설정을 읽는다. 처음 설치하는 환경에서는 `.env.example`을
복사한 뒤 `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`을 입력한다. 기존 `.env`는 덮어쓰지 않는다.
현재 로컬 `.env`에는 사용자 승인으로 이전 텍스트 모델 설정을 복사했으며 모델은 `gpt-5.6-sol`이다.
키 파일은 권한 0600, Git 제외 상태다. 키 값을 브라우저에 전달하지 않는다.

- `MODEL_BASE_URL`: OpenAI 호환 Chat Completions API의 base URL.
- `MODEL_API_KEY`: 해당 API 키. `VITE_` 접두어를 사용하지 않는다.
- `MODEL_NAME`: 공급자가 받는 모델 이름.
- `MODEL_MAX_TOKENS`: 기본 2048, 양의 정수.
- `MODEL_TRUST_ENV`: HTTP 프록시 환경변수 사용 여부(1/0). 현재 로컬은 기존 gateway에 직접 연결하는 0이다.

프로세스 환경변수가 파일보다 우선하며, `AGENT_SERVICE_ENV_FILE`로 명시한 파일을 사용할 수도 있다.
다른 프로젝트의 설정은 자동으로 읽지 않는다. `.env` 변경은 다음 요청에 반영되고,
프로세스 환경변수를 바꿀 때는 서버를 다시 시작한다. 서버 시작·health 확인에는 모델 호출이 없다.
설정이 빠져도 서버는 시작되며, 메시지를 보낼 때 설정 오류를 표시한다.

모델은 공식 `deepagents==0.7.19`와 `langchain-openai==1.6.6`으로 연결한다.
현재 모델에 제공하는 도구는 내부 계획용 `write_todos`뿐이다. 전화·메일·일정·셸·파일 도구는 없다.

## 실행

```bash
./scripts/dev.sh
```

- 웹: http://127.0.0.1:5180
- 통화 화면 예시: http://127.0.0.1:5180/?preview=call
- 백엔드 health: http://127.0.0.1:9010/api/health
- 웹 프록시 health: http://127.0.0.1:5180/api/health

두 health 주소는 `{"status":"ok","service":"agent-service"}`를 반환한다.
이는 웹↔서버 연결만 확인하며 외부 모델이나 통화 공급자의 연결 상태를 뜻하지 않는다.
화면을 열거나 우측 상단 서버 상태 버튼을 누를 때 요청한다. 주기적인 자동 감시는 하지 않는다.

메인 대화에서는 텍스트를 입력하고 Enter 또는 화살표 버튼으로 모델에 메시지를 보낸다.
첫 화면은 안내와 입력창을 함께 보여 주고, 대화가 시작되면 입력창을 하단에 유지한다.
Shift+Enter는 줄바꿈이며 한글 조합 중 Enter로는 전송하지 않는다.
응답 중에는 화살표가 중단 버튼으로 바뀐다. 다음 메시지를 작성할 수 있지만 응답 중에는 전송하지 않는다.
중단·실패한 부분 응답은 표시를 남기며, 재시도는 같은 사용자 메시지에 새 응답을 생성한다.
인증·설정·입력 오류는 설정이나 입력을 수정한 뒤 다시 보내야 한다.
위로 스크롤해 읽을 때 자동으로 끌어내리지 않으며 ‘최신 메시지로’ 버튼으로 돌아올 수 있다.
‘화면 예시’에서는 6가지 통화 상태, 내역 펼침, 듣기 전환, 추가 지시, 통화 종료를 살펴볼 수 있다.
종료 버튼은 ‘종료 확인 중’으로 바꾸고, 예시 도구의 ‘종료 확인’을 눌러야 종료 상태가 된다.
듣기는 화면 상태만 바꾸며 실제 소리는 재생하지 않는다.

메시지와 완료된 응답은 다음 모델 요청의 문맥으로 전달한다. 실패·중단된 부분 응답과 예시 대화는 제외한다.
대화와 응답 상태는 서버의 `data/agent-service.sqlite3`에 저장한다. 사이드바의 ‘새 대화’로
새로 시작하고 이전 대화를 선택해 이어간다. URL의 대화 ID로 새로고침과 뒤로/앞으로 이동도 복원한다.
이전 메시지는 50개씩 불러오며 ‘이전 메시지 더 보기’를 눌러도 읽던 위치를 유지한다.
전송하지 않은 초안과 통화 예시 상태는 탭 메모리에만 있다. 초안은 대화별·예시별로 구분하며 새로고침하면 사라진다.
대화 전환·예시 이동·새로고침·탭 닫기는 진행 중 응답을 중단한다. 복원만으로 모델 요청을 재실행하지 않는다.
정상 중단은 받은 부분까지 저장하고, 강제 종료는 마지막 DB 저장분을 ‘연결이 끊겨 응답이 중단되었습니다’로 복원한다.
다른 탭에서 응답 중인 대화는 새 전송을 막고 ‘다시 불러오기’로 상태를 확인한다.
브라우저에서 중단하면 서버의 실행과 공급자 연결도 닫는다. 공급자 내부 처리·과금 취소까지 보장하지는 않는다.
메인/예시 화면을 오갈 때 초안은 유지하고, 다른 통화 상태 예시를 선택하면 예시에 추가한 메시지는 초기화한다.
‘추가 지시’는 입력창의 대상을 지정한다. 메시지 추가 또는 대상 해제로 일반 대화로 돌아온다.

검토한 실제 저장 대화: [데스크톱](docs/verification/step-04-desktop-chat.png),
[모바일](docs/verification/step-04-mobile-chat.png), [대화 목록](docs/verification/step-04-mobile-list.png).
통화 화면은 [데스크톱 예시](docs/verification/step-02-refined-desktop-call.png),
[모바일 예시](docs/verification/step-02-refined-mobile-call.png)다.
표시 이름은 `user proxy agent`이며, 내부 디렉터리·패키지·health API 식별자는 `agent-service`를 유지한다.

## 저장 위치와 접근 범위

현재는 **로그인 없이 브라우저별로 구분하는 로컬 서비스**다. HttpOnly 쿠키로 30일간 같은
브라우저 프로필·호스트의 대화를 찾는다. 다른 브라우저/시크릿 창은 별도 공간이며, 서버가 모든 대화 접근의 소유권을 확인한다.
쿠키 삭제·만료 후 기존 대화 복구와 기기 간 동기화는 아직 지원하지 않는다. localStorage에는 마지막 선택 ID만 저장한다.
SQLite 파일·저널과 `.env`는 Git에서 제외한다. 세션 토큰은 해시만 DB에 저장하지만 대화 본문은 로컬 파일의 평문이다.
DB 디렉터리 권한은 0700, 파일은 0600이다. 백엔드 단일 프로세스를 사용한다.
`AGENT_SERVICE_DATABASE_PATH` 프로세스 환경변수로 별도 DB 경로를 지정할 수 있다. 자동 테스트는 임시 DB를 사용한다.
DB를 복사해 보관할 때는 서버를 정상 종료한 뒤 `data/` 전체를 보관한다. DB만으로 브라우저 쿠키를 복원하지는 못한다.

## 서버 종료와 포트 변경

Ctrl-C로 두 서버를 함께 종료한다. 하나가 종료되면 나머지도 정리한다.
포트가 이미 사용 중이면 이유를 출력하고 종료하며 기존 프로세스는 건드리지 않는다.
코드 수정 시 웹은 자동 갱신되고, 백엔드는 개발 스크립트를 다시 실행한다.

포트 변경은 프로세스 환경변수로 지정한다. 모델용 `.env`의 포트 값은 읽지 않는다.

```bash
BACKEND_PORT=9011 WEB_PORT=5181 ./scripts/dev.sh
```

백엔드 중단 시 웹의 오류 표시를 직접 확인하려면 두 터미널에서 따로 실행한다.

```bash
# 터미널 1: 프로젝트 루트에서
cd backend
uv run --locked uvicorn agent_service.main:app --host 127.0.0.1 --port 9010
```

```bash
# 터미널 2: 프로젝트 루트에서
cd web
npm run dev
```

웹을 연 뒤 터미널 1을 Ctrl-C로 중단하고 서버 상태 버튼을 누르면 오류가 표시된다.
백엔드를 다시 실행한 뒤 같은 버튼을 누르면 복구된다. 요청은 5초 후 시간초과된다.

## 검증

```bash
./scripts/check.sh
```

백엔드 계약·실제 개발 서버 수명 테스트, Ruff, 웹 요청 처리 테스트, TypeScript,
ESLint, 프로덕션 빌드를 실행한다. 수명 테스트는 임시 로컬 포트에 서버를 실행하고 정리한다.
검증 전 웹과 백엔드 의존성을 모두 설치해야 한다. 일반 검증은 모델을 모사하며 외부 API를 호출하지 않는다.
`web/dist`는 빌드 결과물이다. 실제 배포의 `/api` 라우팅은 후속 배포 설계 대상이다.

브라우저 회귀 검증은 별도로 실행한다.

```bash
cd web
npx playwright install chromium  # 최초 한 번
npm run test:e2e
```

이미 설치된 Google Chrome을 사용하려면 브라우저 설치 대신
`PLAYWRIGHT_CHANNEL=chrome npm run test:e2e`로 실행한다. 이번 검증은 이 방식으로 진행했다.
E2E 28개는 5193 포트에 독립 웹 서버를 띄운 뒤 정리하며 포트가 점유되어 있으면 실패한다.
health·세션·대화 저장·chat 응답은 E2E에서 제어한다. 실제 모델과 브라우저 연결은 별도 smoke test로 확인했다.
현재 자동 테스트는 backend 41개, 웹 API/스트림 17개, E2E 28개다.
스크린샷과 실패 시 trace는 `web/test-results/`에 생성한다. 이 경로는 커밋에서 제외한다.

실제 모델 검증은 실행 중인 서버에 대해 아래 명령을 명시적으로 실행한다.
합성 문장으로 한국어 응답·문맥·중단 후 새 요청을 확인하며 실제 모델 사용 비용이 발생한다.

```bash
backend/.venv/bin/python scripts/check_live_chat.py --run
```

API 사용 시 먼저 `POST /api/session`에 `{}`를 보내 쿠키를 받고, 이후 같은 쿠키를 유지한다.
`POST /api/conversations`에 클라이언트가 생성한 `conversation_id` UUID를 보내 대화를 만든다.
`POST /api/chat`은 `{request_id, conversation_id, content}` 또는
`{request_id, conversation_id, retry_message_id}`를 받고 start/delta/done/error SSE를 반환한다.
같은 전송을 네트워크 재시도할 때 request_id를 유지한다. 이미 접수됐으면 409와 기존 ID를 반환하고 모델을 재실행하지 않는다.
사용자의 명시적 ‘다시 시도’는 새 request_id와 마지막 응답의 retry_message_id를 사용한다.
문맥은 서버가 저장 기록에서 선택한다. 최근 전체 턴 기준 최대 80개 메시지·60,000자이며,
오래된 턴은 모델 입력에서 제외하지만 원문은 DB에 남긴다. 자동 요약과 DeepAgents 내부 실행 상태 복원은 하지 않는다.
공급자 오류 원문은 전달하지 않는다. 전체 실행 한도는 120초다.
전체 계약과 상태 전이는 [4단계 계획](docs/superpowers/plans/2026-09-25-step-04-conversation-storage.md)에 있다.
계정 로그인·공개 배포·대화 검색/편집/삭제는 아직 구현하지 않았다.

## 문서 읽기

- [작업 지침](AGENTS.md): 단계별 계획·컨펌·구현 절차.
- [제품 방향과 구조](docs/product.md): 합의한 경험, 구성요소의 책임, 기존 작업 재사용 범위.
- [전체 로드맵](docs/roadmap.md): 12단계 순서와 단계별 완료 기준.
- [1단계 세부 계획](docs/superpowers/plans/2026-09-25-step-01-foundation.md):
  웹·백엔드 기본 구조의 구현 범위, 파일, 검증 방법.
- [2단계 세부 계획](docs/superpowers/plans/2026-09-25-step-02-chat-design.md):
  Muse·Grok Bot 레퍼런스, 채팅·통화 카드 구현 및 검증 기록.
- [3단계 실제 채팅](docs/superpowers/plans/2026-09-25-step-03-live-chat.md):
  모델 설정, SSE 계약, 중단·재시도와 실제 모델 검증 기록.
- [4단계 대화 저장](docs/superpowers/plans/2026-09-25-step-04-conversation-storage.md):
  SQLite·브라우저 세션·복원·중복 방지와 실제 모델 검증 기록.
- [2단계 디자인 수정](docs/superpowers/plans/2026-09-25-step-02-design-refinement.md):
  user proxy agent 이름, 장식을 줄인 메신저 화면, Impeccable 스킬 적용 기록.

## 기존 작업과의 관계

`../calling-agent/`의 ClawOps·Realtime 통화 구현과 검증 사례를 기반으로 한다.
`../user_proxy_agent/`의 시나리오·대화 정책·평가 사례는 테스트 자산으로 참고한다.
실서비스에는 실제 사용자가 채팅으로 참여한다.

3단계에서 승인받은 텍스트 모델 설정만 새 `.env`에 복사했다. 원본은 변경하지 않았다.
통화 키·녹음·앱 계정은 기존 디렉터리에 유지하며, 해당 단계의 승인된 계획에 따라 연결한다.
