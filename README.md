# Agent Service

사용자가 채팅으로 일을 맡기면 실제 앱과 전화로 처리하고, 진행 중인 통화를 읽거나
들으며 추가 지시·종료로 개입할 수 있는 개인 비서 서비스.

현재는 **1단계: 웹·백엔드 기본 구조**를 구현했다. 실제 health API를 호출하여 연결 상태와
오류·재시도를 표시한다. 채팅·DeepAgents·전화·앱 연결은 이후 단계에서 추가한다.

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
- 백엔드 health: http://127.0.0.1:9010/api/health
- 웹 프록시 health: http://127.0.0.1:5180/api/health

두 health 주소는 `{"status":"ok","service":"agent-service"}`를 반환한다.
이는 웹↔서버 연결만 확인하며 외부 모델이나 통화 공급자의 연결 상태를 뜻하지 않는다.
화면을 열거나 ‘다시 확인’을 누를 때 요청한다. 주기적인 자동 감시는 하지 않는다.

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

웹을 연 뒤 터미널 1을 Ctrl-C로 중단하고 ‘다시 확인’을 누르면 오류가 표시된다.
백엔드를 다시 실행한 뒤 ‘다시 시도’를 누르면 복구된다. 요청은 5초 후 시간초과된다.

## 검증

```bash
./scripts/check.sh
```

백엔드 계약·실제 개발 서버 수명 테스트, Ruff, 웹 요청 처리 테스트, TypeScript,
ESLint, 프로덕션 빌드를 실행한다. 수명 테스트는 임시 로컬 포트에 서버를 실행하고 정리한다.
검증 전 웹과 백엔드 의존성을 모두 설치해야 한다. 외부 API는 호출하지 않는다.
`web/dist`는 빌드 결과물이다. 실제 배포의 `/api` 라우팅은 후속 배포 설계 대상이다.

## 문서 읽기

- [작업 지침](AGENTS.md): 단계별 계획·컨펌·구현 절차.
- [제품 방향과 구조](docs/product.md): 합의한 경험, 구성요소의 책임, 기존 작업 재사용 범위.
- [전체 로드맵](docs/roadmap.md): 12단계 순서와 단계별 완료 기준.
- [1단계 세부 계획](docs/superpowers/plans/2026-09-25-step-01-foundation.md):
  웹·백엔드 기본 구조의 구현 범위, 파일, 검증 방법.

## 기존 작업과의 관계

`../calling-agent/`의 ClawOps·Realtime 통화 구현과 검증 사례를 기반으로 한다.
`../user_proxy_agent/`의 시나리오·대화 정책·평가 사례는 테스트 자산으로 참고한다.
실서비스에는 실제 사용자가 채팅으로 참여한다.

현재 로컬 설정·API 키·녹음은 기존 디렉터리에 그대로 있다. 필요한 시점의 승인된
계획에 따라 연결하며, 이 디렉터리에 자격증명을 복사하지 않았다.
