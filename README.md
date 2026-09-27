# User Proxy Agent

채팅으로 요청하면 일정·메일을 확인하고 전화를 걸어 처리하는 개인용 로컬 웹 서비스.
통화 중에는 실시간 전사를 읽거나 양쪽 음성을 듣고, 추가 지시를 보내거나 직접 종료할 수 있다.

## 현재 기능

- DeepAgents 기반 스트리밍 채팅, 마크다운, 응답 중단·재시도
- 대화 저장·전환·이름 변경·삭제, 첫 응답 이후 자동 제목 생성
- ClawOps와 GPT-Live의 직접 음성 연결, ARS 다이얼, 끼어들기 처리와 자동 종료
- 통화 중 채팅 질문·답변과 추가 지시, 종료 후 결과 자동 보고
- Google Calendar·Gmail 조회, 사용자 확인 후 일정 등록·메일 발송
- 선택적 로컬 Langfuse 실행 추적

메인 채팅과 업무 판단은 설정한 텍스트 모델이, 전화 음성 대화는 `gpt-live-1`이 맡는다.
Live가 업무 판단을 DeepAgents에 위임하며, 별도 STT/TTS 서비스는 붙이지 않는다.

본인 계정과 통화로 주요 흐름을 검증했다. 한국어 대화의 반복 질문·자연스러움과 응답 지연은
개선 중이다. [최근 음성 평가](docs/evaluations/2026-09-27-korean-dialogue.md)와
[단계별 검증 범위](docs/roadmap.md)에 확인된 동작과 남은 제한을 기록했다.

## 전체 구조

사용자는 채팅으로 요청하고, 통화와 앱 실행은 같은 대화 안에서 확인·제어한다.

```mermaid
flowchart LR
    WEB["User Proxy Agent<br/>React 웹"]

    subgraph SERVER["FastAPI · 로컬 백엔드"]
        AGENT["DeepAgents<br/>채팅 · 통화 업무 판단"]
        CALL["통화 서비스<br/>음성 중계 · DTMF · 종료"]
        APPS["앱 서비스<br/>조회 · 승인 후 실행"]

        AGENT <-->|"통화 도구 · 판단 위임"| CALL
        AGENT -->|"조회 · 실행안 제안"| APPS
    end

    MODEL["텍스트 모델 API"]
    LIVE["GPT-Live<br/>음성 이해 · 생성"]
    CLAWOPS["ClawOps<br/>전화 연결 · 회선 제어"]
    PHONE["통화 상대 / ARS"]
    GOOGLE["Google Calendar<br/>Gmail"]
    LOCAL[("로컬 데이터 · 관측<br/>SQLite · Langfuse")]

    WEB <-->|"채팅 · 응답"| AGENT
    WEB <-->|"전사 · 듣기 · 개입"| CALL
    WEB -->|"실행 확인 · 승인"| APPS
    AGENT <-->|"모델 호출"| MODEL
    CALL <-->|"음성 · 업무 위임"| LIVE
    CALL <-->|"음성 · 회선 제어"| CLAWOPS
    CLAWOPS <-->|"실제 전화"| PHONE
    APPS <-->|"OAuth · Google API"| GOOGLE
    SERVER -.->|"상태 저장 · 실행 추적"| LOCAL

    classDef interface fill:#0f172a,color:#ffffff,stroke:#0f172a,stroke-width:2px;
    classDef intelligence fill:#eef2ff,color:#312e81,stroke:#818cf8;
    classDef service fill:#ecfdf5,color:#064e3b,stroke:#34d399;
    classDef approval fill:#fffbeb,color:#78350f,stroke:#fbbf24;
    classDef external fill:#f8fafc,color:#334155,stroke:#94a3b8;
    classDef local fill:#faf5ff,color:#581c87,stroke:#c084fc,stroke-dasharray:4 3;
    class WEB interface;
    class AGENT,MODEL,LIVE intelligence;
    class CALL service;
    class APPS approval;
    class CLAWOPS,PHONE,GOOGLE external;
    class LOCAL local;
    style SERVER fill:#f8fafc,stroke:#cbd5e1,color:#334155
```

DeepAgents 노드는 메인 채팅과 통화 업무의 **별도 실행**을 함께 나타낸다. 두 실행은 같은
팩토리와 텍스트 모델 설정을 사용한다. 전화 음성은 GPT-Live와 직접 주고받으며, Google 쓰기
작업은 사용자가 확인 카드를 승인한 뒤 실행한다. 점선은 SQLite 저장과 선택적 로컬 Langfuse
추적 경로이며, Langfuse에는 대화 원문 대신 실행 메타데이터만 전송한다.
모듈별 책임과 실제 파일 위치는 [코드 구조 안내](docs/code-structure.md)에 정리했다.

## 시작하기

Python **3.12**, uv, Node.js **24 LTS**(24–26 지원), npm을 사용한다.
전화 없는 음성 평가의 합성 입력 생성은 macOS의 Yuna 음성을 사용한다.

```bash
cd backend
uv sync --locked
cd ../web
npm ci
cd ..

# 최초 설치에만 복사. 기존 .env는 유지한다.
test -f .env || cp .env.example .env
chmod 600 .env
```

`.env`에 `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`을 입력한 뒤 실행한다.
키를 `VITE_` 변수나 웹 코드에 넣지 않는다.

```bash
./scripts/dev.sh
```

- 웹: http://127.0.0.1:5180
- 백엔드 상태: http://127.0.0.1:9010/api/health
- 종료: 실행한 터미널에서 Ctrl-C

상태 확인은 로컬 서버 연결만 검사한다. 모델·전화·Google 연결 상태를 보증하지 않는다.
서버는 로컬 주소에만 바인딩하며, 공개 배포와 다중 사용자 로그인은 지원 범위 밖이다.

## 전화와 앱 연결

채팅만 사용할 때는 `.env.example`의 `CALLS_ENABLED=0`을 유지한다.
전화 사용 시 ClawOps 계정·키·발신번호, 음성 모델 주소·키, 허용 수신번호를 설정한다.
번호와 목적을 포함한 명확한 발신 요청을 채팅으로 전달한다.

통화 중 **통화 듣기**는 양쪽 음성을 재생하고, **통화 종료**는 실제 회선 종료를 요청한다.
듣기 중지나 채팅 화면 이동만으로 통화가 끝나지는 않는다. 서비스는 한 번에 한 통화를 처리한다.
Google은 본인 OAuth 설정과 웹의 계정 동의가 필요하다. 일정 등록과 메일 발송은
모델이 제안한 내용을 확인 카드에서 검토한 뒤 실행한다.

- [모델·Google·Langfuse 설정](docs/configuration.md)
- [Google OAuth 설치 안내](docs/google-setup.md)
- [통화 운영·상태·진단](docs/calls.md)
- [로컬 실행·백업·복구](docs/local-recovery.md)

## 개발과 검증

```bash
./scripts/check.sh
```

백엔드·웹 테스트, 정적 검사와 빌드를 실행한다. 로컬 `.env`를 배제하고 외부 모델·전화·Google을
모사한다. 브라우저 검사와 유료 API 음성 평가는 [스크립트 안내](scripts/README.md)에서 구분한다.

## 저장소 구조

| 경로 | 내용 |
| --- | --- |
| `backend/src/agent_service/` | 채팅, 통화, Google 연결, 확인 후 실행, 저장소 |
| `backend/tests/` | 백엔드 자동 검사 |
| `web/src/`, `web/e2e/` | React 화면과 웹 검사 |
| `web/src/api/`, `web/src/calls/` | 공통 HTTP 처리와 통화 기능 |
| `scripts/` | 로컬 실행, 검사, 합성 음성 평가 |
| `docs/` | 운영 안내, 제품 방향, 계획·검증 기록 |
| `.env.example` | 인증정보가 없는 설정 템플릿 |
| `data/`, `var/`, `.env` | 사용자 데이터·평가 결과·인증정보, Git 제외 |

이 디렉터리가 독립 저장소의 루트다. 이전 실험 프로젝트나 모델 다운로드 폴더는 실행에 필요하지 않다.
잠금 파일 `backend/uv.lock`, `web/package-lock.json`을 함께 버전 관리한다.

[제품 방향](docs/product.md) · [로드맵](docs/roadmap.md) · [작업 지침](AGENTS.md) ·
[코드 구조](docs/code-structure.md) · [저장소 업로드 범위](docs/repository.md)
