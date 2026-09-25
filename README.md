# user proxy agent

사용자가 채팅으로 일을 맡기면 실제 앱과 전화로 처리하고, 진행 중인 통화를 읽거나
들으며 추가 지시·종료로 개입할 수 있는 개인 비서 서비스.

현재는 **2단계: 채팅 화면 디자인**까지 구현했다. 메시지는 메모리에만 추가되며
아직 비서가 응답하거나 실제 전화가 걸리지는 않는다. 서버 연결 상태·재시도는 실제 health API를 사용한다.

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
API 키나 기존 프로젝트의 가상환경은 필요하지 않다.

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

메인 대화에서는 텍스트를 입력하고 Enter 또는 화살표 버튼으로 화면에 메시지를 추가한다.
Shift+Enter는 줄바꿈이며 한글 조합 중 Enter로는 추가하지 않는다.
‘화면 예시’에서는 6가지 통화 상태, 내역 펼침, 듣기 전환, 추가 지시, 통화 종료를 살펴볼 수 있다.
종료 버튼은 ‘종료 확인 중’으로 바꾸고, 예시 도구의 ‘종료 확인’을 눌러야 종료 상태가 된다.
듣기는 화면 상태만 바꾸며 실제 소리는 재생하지 않는다.

메시지·초안·통화 조작은 서버나 브라우저 저장소에 저장하지 않으며 새로고침 시 초기화된다.
메인/예시 화면을 오갈 때 초안은 유지하고, 다른 통화 상태 예시를 선택하면 예시에 추가한 메시지는 초기화한다.
‘추가 지시’는 입력창의 대상을 지정한다. 메시지 추가 또는 대상 해제로 일반 대화로 돌아온다.

검토한 화면: [데스크톱 대화](docs/verification/step-02-refined-desktop-empty.png),
[데스크톱 통화](docs/verification/step-02-refined-desktop-call.png),
[모바일 대화](docs/verification/step-02-refined-mobile-empty.png),
[모바일 통화](docs/verification/step-02-refined-mobile-call.png).
표시 이름은 `user proxy agent`이며, 내부 디렉터리·패키지·health API 식별자는 `agent-service`를 유지한다.

Ctrl-C로 두 서버를 함께 종료한다. 하나가 종료되면 나머지도 정리한다.
포트가 이미 사용 중이면 이유를 출력하고 종료하며 기존 프로세스는 건드리지 않는다.
코드 수정 시 웹은 자동 갱신되고, 백엔드는 개발 스크립트를 다시 실행한다.

포트 변경은 환경변수로 지정한다. `.env`는 자동으로 읽지 않는다.

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
검증 전 웹과 백엔드 의존성을 모두 설치해야 한다. 외부 API는 호출하지 않는다.
`web/dist`는 빌드 결과물이다. 실제 배포의 `/api` 라우팅은 후속 배포 설계 대상이다.

브라우저 회귀 검증은 별도로 실행한다.

```bash
cd web
npx playwright install chromium  # 최초 한 번
npm run test:e2e
```

이미 설치된 Google Chrome을 사용하려면 브라우저 설치 대신
`PLAYWRIGHT_CHANNEL=chrome npm run test:e2e`로 실행한다. 이번 검증은 이 방식으로 진행했다.
E2E 14개는 5193 포트에 독립 웹 서버를 띄운 뒤 정리하며 포트가 점유되어 있으면 실패한다.
health 응답은 E2E에서 제어하고, 실제 서버 오류·복구는 별도 통합 검증으로 확인했다.
스크린샷과 실패 시 trace는 `web/test-results/`에 생성한다. 이 경로는 커밋에서 제외한다.

## 문서 읽기

- [작업 지침](AGENTS.md): 단계별 계획·컨펌·구현 절차.
- [제품 방향과 구조](docs/product.md): 합의한 경험, 구성요소의 책임, 기존 작업 재사용 범위.
- [전체 로드맵](docs/roadmap.md): 12단계 순서와 단계별 완료 기준.
- [1단계 세부 계획](docs/superpowers/plans/2026-09-25-step-01-foundation.md):
  웹·백엔드 기본 구조의 구현 범위, 파일, 검증 방법.
- [2단계 세부 계획](docs/superpowers/plans/2026-09-25-step-02-chat-design.md):
  Muse·Grok Bot 레퍼런스, 채팅·통화 카드 구현 및 검증 기록.
- [2단계 디자인 수정](docs/superpowers/plans/2026-09-25-step-02-design-refinement.md):
  user proxy agent 이름, 장식을 줄인 메신저 화면, Impeccable 스킬 적용 기록.

## 기존 작업과의 관계

`../calling-agent/`의 ClawOps·Realtime 통화 구현과 검증 사례를 기반으로 한다.
`../user_proxy_agent/`의 시나리오·대화 정책·평가 사례는 테스트 자산으로 참고한다.
실서비스에는 실제 사용자가 채팅으로 참여한다.

현재 로컬 설정·API 키·녹음은 기존 디렉터리에 그대로 있다. 필요한 시점의 승인된
계획에 따라 연결하며, 이 디렉터리에 자격증명을 복사하지 않았다.
