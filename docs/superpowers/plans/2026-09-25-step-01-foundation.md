# 1단계: 프로젝트 기본 구조 Implementation Plan

> **For agentic workers:** Execute inline by default. Use
> `superpowers:subagent-driven-development` only when substantial independent
> tasks make delegation cheaper or safer. Steps use checkbox (`- [x]`) syntax.
> 이 프로젝트에서는 병렬 작업에 별도 합의가 필요하며, 1단계는 직접 순차 실행한다.

**Goal:** 웹과 백엔드를 로컬에서 실행하고, 웹에서 실제 health 응답과 연결 실패를 확인한다.

**Architecture:** `backend/`와 `web/`를 분리한 단일 프로젝트로 시작한다.
웹은 상대 경로 `/api/health`를 호출하고 Vite 개발 프록시를 통해 FastAPI에 연결한다.
개발 실행 스크립트가 두 프로세스의 시작과 종료를 관리한다.

**Tech Stack:** 제안 — Python 3.12 이상, uv, FastAPI, pytest·HTTPX·Ruff;
React, TypeScript, Vite, npm, ESLint. Node는 선택한 Vite 버전을 지원하는 설치된 LTS를 확인한다.
구현 시 호환되는 버전을 선정하고 Python·Node 요구 버전과 lockfile을 저장한다.

**Spec:** [제품 방향과 구조](../../product.md)

## 상태와 승인 기록

- 상태: `completed`
- 계획 버전: 1
- 작성일: 2026-09-25
- 승인된 현재 작업: 계획 버전 1의 웹·백엔드 기본 구조 구현과 검증.
- 이 계획의 구현 승인: 2026-09-25, 1단계 계획 제시 직후 사용자 발언 “좋아 진행해줘.”
- 승인 기록: 계획 버전 1을 승인받고 `approved`에서 `in_progress`로 전환했다.

### 1단계 화면 방향

연결 확인이라는 한 가지 목적에 맞춰 작은 상태 패널을 중앙에 배치한다.
색상은 바탕 `#F6F8FC`, 표면 `#FFFFFF`, 본문 `#172435`, 보조 `#526176`,
성공 `#166B61`, 실패 `#B33D46`을 사용한다. 제목은 시스템 sans-serif의 단정한
굵기, 본문은 한국어 시스템 글꼴, 작은 영문 서비스 표시는 monospace로 구분한다.
두 점이 이어진 연결 표시를 상태의 시각적 단서로 쓰고, 장식·가짜 대화·기능 예고는 추가하지 않는다.
모바일에서는 패널 너비와 여백을 줄인다. 정식 채팅 서비스의 디자인 방향은 2단계에서 합의한다.

## Global Constraints

- 메인 에이전트와의 입력·응답은 텍스트 채팅이다.
- 웹부터 반응형으로 구현하고, 이후 앱이 같은 서버 API를 사용할 수 있도록 한다.
- 업무 상태와 통화 상태는 별도로 관리한다. 통화가 정상 종료되어도 업무가 미완료일 수 있다.
- 이미 위임된 범위는 진행하고 새 결정이 필요하면 사용자에게 질문한다.
- 이 단계는 API 키 없이 실행 가능해야 한다. 모델 호출·전화·앱 연결은 실행하지 않는다.
- 기본 개발 주소는 백엔드 `127.0.0.1:9010`, 웹 `127.0.0.1:5180`으로 제안한다.
  포트가 사용 중이면 이유를 출력하고 종료하며 기존 프로세스를 종료하지 않는다.
- 현재 단계에서는 디자인 확인용 시나리오 데이터나 가짜 에이전트 응답을 추가하지 않는다.

---

## 범위와 산출물

이번 단계는 개발 기반과 실제 웹↔서버 연결을 만든다. 채팅 화면 디자인은 2단계,
DeepAgents·모델 연결은 3단계, DB 저장은 4단계, 기존 통화 코드 이식은 5단계에서 진행한다.
컨테이너·배포·OAuth·네이티브 앱 설정은 이번 단계에 포함하지 않는다.

```text
agent-service/
├── README.md                          # 실제 설치·실행·검증 방법으로 갱신
├── AGENTS.md
├── .gitignore                         # 키·사용자 데이터·생성물 제외
├── .env.example                       # BACKEND_PORT=9010, WEB_PORT=5180
├── scripts/dev.sh                     # 포트 확인, 두 개발 프로세스 실행·정리
├── scripts/check.sh                   # 백엔드 검사 + 웹 정적 검사·빌드
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── src/agent_service/__init__.py
│   ├── src/agent_service/main.py       # create_app() 및 GET /api/health
│   └── tests/test_health.py
└── web/
    ├── package.json
    ├── package-lock.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── eslint.config.js
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx                    # 연결 상태·재시도만 표시하는 기본 화면
        ├── api.ts                     # 상대 경로 fetch, 시간초과·응답 검증
        └── styles.css
```

툴체인이 요구하는 보조 설정 파일은 같은 역할 범위에서 추가하고 완료 기록에 남긴다.
독립 Git 저장소를 사용할 경우 이 디렉터리에서만 초기화한다. 원격 생성·push는 이번 범위에 없다.
승인된 작업 범위에 맞춘 로컬 커밋은 검증 후 수행할 수 있으며 키·기존 녹음은 포함하지 않는다.

## Task 1: 백엔드 health 계약

**Files:** `backend/pyproject.toml`, `backend/uv.lock`,
`backend/src/agent_service/__init__.py`, `backend/src/agent_service/main.py`,
`backend/tests/test_health.py`.

**Interfaces:**
- `create_app() -> FastAPI`와 ASGI 변수 `app`을 제공한다.
- `GET /api/health` → HTTP 200, JSON `{"status":"ok","service":"agent-service"}`.
- health는 프로세스와 라우트의 응답 여부를 뜻한다. 모델·통화 공급자 연결 확인을 뜻하지 않는다.
- 모듈 import와 앱 시작은 외부 API 요청을 발생시키지 않는다.

- [x] 필요한 최소 의존성을 선언하고 lockfile을 생성한다.
- [x] `tests/test_health.py`에 응답 코드·JSON 계약 테스트를 먼저 작성하여 미구현 실패를 확인한다.
- [x] FastAPI 앱과 라우트를 구현하고 테스트를 통과시킨다.

**Verification:** `cd backend && uv run pytest -q` 및 `uv run ruff check .`.
동일 경로에서 `uv run uvicorn agent_service.main:app --host 127.0.0.1 --port 9010` 실행 후
`curl --fail http://127.0.0.1:9010/api/health`로 계약과 일치하는 실제 응답을 확인한다.

## Task 2: 웹에서 실제 연결·오류 확인

**Files:** 위 구조의 `web/` 파일 전체.

**Interfaces:**
- `api.ts`는 `fetchHealth(signal?: AbortSignal): Promise<HealthResponse>`를 제공한다.
- `HealthResponse`는 `{status: "ok"; service: "agent-service"}`이다.
- 상대 경로 `/api/health`를 사용한다. Vite는 `/api`를 `http://127.0.0.1:<BACKEND_PORT>`로
  프록시한다. 개발 프록시 주소는 브라우저 번들에 자격증명을 포함하지 않는다.
- 상태는 `loading | connected | error`로 표시한다. HTTP 오류·네트워크 실패·잘못된 JSON·
  5초 시간초과는 연결 실패로 표시하고, 재시도로 새 요청을 보낸다. 자동 무한 재시도는 없다.
- 컴포넌트 해제 시 진행 중인 요청을 취소한다.

- [x] React·TypeScript·Vite 최소 구성을 만들고 `dev`, `typecheck`, `lint`, `build` 명령을 정의한다.
- [x] `frontend-design` 지침을 읽고, 1단계의 단순 연결 확인 범위 안에서 상태와 재시도 버튼을 구현한다.
- [x] 개발 프록시를 설정하고 웹에서 실제 health 응답을 확인한다.
- [x] 서버 중지 시 연결 오류, 재시작 후 재시도 성공을 브라우저에서 확인한다.

**Verification:** `cd web && npm run typecheck && npm run lint && npm run build`.
브라우저에서 `http://127.0.0.1:5180`을 열고 연결·오류·복구를 실제 확인한다.
좁은 화면에서 가로 넘침이 없는지, 키보드로 재시도 버튼을 사용할 수 있는지 확인한다.
정교한 채팅 UI의 시각 승인은 2단계에서 진행한다.

## Task 3: 한 번에 실행하고 정리하기

**Files:** `.gitignore`, `.env.example`, `scripts/dev.sh`, `scripts/check.sh`, `README.md`.

**Interfaces:**
- `./scripts/dev.sh`는 초기 의존성 설치 후 두 서버를 실행한다. 반복 실행에서 자동 설치를 하지 않는다.
- 개발 스크립트는 프로젝트 루트를 기준으로 동작하고, 이 스크립트가 실행한 자식 프로세스만 정리한다.
- 포트 충돌·도구/의존성 누락·한 프로세스의 조기 종료를 출력한다. 일부만 남겨 놓지 않는다.
- `BACKEND_PORT`와 `WEB_PORT` 환경변수로 기본 포트를 바꿀 수 있다.
- `./scripts/check.sh`는 백엔드 테스트·Ruff와 웹 typecheck·lint·build를 수행하고 실패 시 0이 아닌 코드로 종료한다.

- [x] 개발·검증 스크립트와 예시 설정을 작성한다. `.env`, `.venv`, `node_modules`, 빌드·로그·사용자 데이터 경로를 제외한다.
- [x] README에 실제 설치 명령, 실행·검증 명령, 로컬 주소, 현재 가능한 범위를 적는다.
- [x] 개발 서버를 띄워 웹 경유 health 응답을 확인하고 Ctrl-C 후 두 포트가 해제되는지 확인한다.
- [x] 재실행과 포트 충돌 실패를 확인한다. 기존 실행 중인 프로젝트에는 영향을 주지 않는다.

**Verification:** `bash -n scripts/dev.sh scripts/check.sh`, `./scripts/check.sh`.
개발 서버 실행 중 `curl --fail http://127.0.0.1:5180/api/health`로 프록시 연결을 확인한다.
종료 뒤 두 주소가 더 이상 응답하지 않는지 확인한다. 검증을 위해 시작한 프로세스는 정리한다.

## 최종 완료 기준

- [x] API 키 없이 웹·백엔드를 설치·실행할 수 있다.
- [x] 백엔드 직접 호출과 웹 프록시 호출이 같은 health 계약을 반환한다.
- [x] 실제 화면에서 로딩·연결·연결 실패·재시도가 동작한다.
- [x] 검증 스크립트가 통과하고 개발 프로세스 시작·종료·포트 충돌을 확인했다.
- [x] 기존 형제 프로젝트를 변경하지 않았고 실제 모델·전화·앱 동작이 발생하지 않았다.
- [x] README와 아래 실행 기록을 실제 결과로 갱신하고 로드맵의 1단계를 완료로 표시했다.

## 실행 기록

완료일: 2026-09-25. 승인 범위인 1단계만 구현했다.

### 구현

- FastAPI `create_app()` 및 실제 `/api/health` 계약을 구현했다.
- React 화면은 loading/connected/error 상태, 수동 재시도, 요청 취소, 5초 시간초과,
  응답 JSON의 status/service 검증을 제공한다.
- `scripts/dev.sh`가 Python 표준 라이브러리 보조 파일 `scripts/dev.py`를 실행한다.
  두 서버를 별도 프로세스 그룹으로 시작하고 자신이 만든 그룹만 종료한다.
  사전 포트 검사, 환경변수 검증, Node·의존성 누락 안내를 포함한다.
- 보조 파일: `.node-version`, `backend/.python-version`, `web/src/api.test.ts`,
  `backend/tests/test_dev.py`. 설치 lockfile 두 개를 생성했다.
- ESLint 9 설치 시 지원 종료 경고가 있어 호환 peer 범위를 확인하고 ESLint 10으로 갱신했다.
- Python 3.12.13, FastAPI 0.141.1, Uvicorn 0.54.0, React 19.3.0, Vite 8.3.1을 사용했다.
  Node 24 LTS로 전체 검증하고 기존 Node 26.4.0으로 기본 개발 스크립트 실행도 확인했다.
  시스템의 기본 Node 설정은 바꾸지 않았다.

### 자동 검증

- health 테스트를 먼저 작성하여 미구현 모듈 오류를 확인한 뒤 구현했다.
- 웹 API 테스트도 미구현 모듈 오류를 먼저 확인했다. 정상 응답, HTTP 실패, 잘못된 JSON,
  다른 서비스 응답, 비정상 status, 네트워크 실패, 취소, 5초 시간초과의 8개 테스트가 통과했다.
- 개발 스크립트 테스트는 미구현 상태에서 서버가 뜨지 않는 실패를 확인한 뒤 구현했다.
  실제 서버 시작, SIGINT 정리, 같은 포트 재실행, 한 자식 종료 시 전체 정리,
  점유된 포트에 대한 실패 및 기존 리스너 보존을 검증했다.
- `./scripts/check.sh`: pytest **3 passed**, 웹 **8 passed**, Ruff·TypeScript·ESLint·Vite 빌드 통과.
  shell 구문 검사도 포함한다. 최초 Ruff 검사에서 import 정렬·긴 줄을 수정한 후 통과했다.
- 직접 및 프록시 `curl --fail` 호출 모두 HTTP 200과 동일한 정확한 JSON을 반환했다.
- 실제 터미널의 `./scripts/dev.sh` → 프록시 health 확인 → Ctrl-C 종료 후,
  소켓 연결 검사로 9010과 5180 두 포트가 모두 닫혔음을 확인했다.

### 브라우저 검증

- 데스크톱 Chrome에서 실제 서버 연결과 키보드 Tab/Enter 재시도를 확인했다.
- 로딩 상태는 실제 health 요청을 잠시 지연시켜 표시와 버튼 비활성화를 확인했다.
  연결·오류·복구는 응답 대체 없이 실제 백엔드 중단 및 재시작으로 확인했다.
- 처음 미리보기 도구에서 데스크톱을 확인한 뒤 도구 연결이 끊겼다.
  임시 Playwright 환경과 로컬 Chrome으로 나머지 검증을 완료했다.
  Playwright는 서비스 의존성에 추가하지 않았다.
- 1280×800 데스크톱, 390×844 모바일 성공·오류 스크린샷을 검토했다.
  390px 및 320px에서 가로 넘침이 없고, 브라우저 JavaScript 오류도 없었다.
- 검증에 사용한 개발 서버와 임시 Chrome 프로세스를 모두 종료했다.

### 완료 후 기록 방식 추가

2026-09-25, 사용자 요청 “작업 단위마다 기록이 필요하면 로컬 깃을 만든다음에 거기에
커밋하면서 작업해줘.”에 따라 이 디렉터리에 독립 로컬 Git 저장소를 만들고,
검증을 마친 1단계 전체를 첫 커밋의 기준점으로 기록한다. 이후 작업 단위별 커밋 규칙은
`AGENTS.md`의 ‘로컬 Git 기록’을 따른다.

### 남은 범위

- 이 화면은 웹↔서버의 기본 연결만 확인한다. 채팅과 실제 에이전트 기능은 아직 없다.
- 채팅 UI 디자인은 2단계의 별도 계획·컨펌 대상이다. 2단계 구현은 시작하지 않았다.
- 기존 `calling-agent`와 `user_proxy_agent`를 변경하거나 자격증명을 복사하지 않았다.
  모델 호출, 실제 발신, Calendar/Gmail 동작도 수행하지 않았다.
- 배포 설정과 프로덕션 `/api` 라우팅은 이번 범위에 포함하지 않았다.
