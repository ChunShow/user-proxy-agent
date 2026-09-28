# 실행과 평가 스크립트

아래 명령은 저장소 루트에서 실행한다. Python 스크립트는 설치된 backend 환경을 사용한다.
일반 검사와 실제 API 검사는 별도이며, `check.sh`에 실제 API 검사를 연결하지 않는다.

## 로컬 개발·자동 검사

```bash
./scripts/dev.sh
./scripts/check.sh
```

`dev.sh`는 웹 5180·백엔드 9010을 함께 실행하고 Ctrl-C로 정리한다.
포트 변경은 `.env`가 아닌 프로세스 환경변수로 지정한다.

```bash
BACKEND_PORT=9011 WEB_PORT=5181 ./scripts/dev.sh
```

`check.sh`는 Ruff, pytest, 웹 단위 검사, TypeScript, ESLint, 빌드를 실행한다.
서버 수명 테스트에는 임시 로컬 포트를 사용한다. API 공급자는 모사하고 로컬 `.env`는 읽지 않는다.

브라우저 검사는 별도 실행한다. 독립 웹 서버에 5193 포트를 사용하고 API는 모사한다.
스크린샷과 실패 trace는 Git 제외 `web/test-results/`에 저장하며 과거 문서 이미지는 갱신하지 않는다.

```bash
cd web
npx playwright install chromium  # 최초 한 번
npm run test:e2e
# 설치된 Google Chrome을 이용할 때
PLAYWRIGHT_CHANNEL=chrome npm run test:e2e
```

## 외부 연결이 있는 검사

| 스크립트 | 동작 | 실제 전화·Google 쓰기 |
| --- | --- | --- |
| `check_live_chat.py --run` | 실행 중인 서버에서 실제 텍스트 모델 응답·문맥·중단 검사 | 없음 |
| `check_call_followup.py --run` | 격리 DB·합성 기록으로 실제 모델의 일정/메일 실행안 판단 | 없음 |
| `check_langfuse_tracing.py --run` | 합성 그래프 실행 기록을 로컬 Langfuse에 저장·재조회 | 없음 |
| `check_live_calls.py --preflight` | ClawOps 계정·소켓과 음성 모델 연결 검사 | 발신 없음; 외부 API 접근 있음 |
| `check_live_calls.py --run --to <번호> --request-id <UUID>` | 채팅 API로 발신 승인 카드 생성 | 이 명령은 승인하지 않음 |

실제 모델과 전화 검사는 과금될 수 있다. 실제 발신은 웹 카드의 대상·목적을 확인하고 별도 승인해야 한다.
같은 발신번호를 다른 전화 실험과 동시에 사용하지 않는다. 자세한 상태 해석은
[통화 안내](../docs/calls.md)를 따른다.

```bash
# 실제 모델을 호출하므로 의도적으로 실행할 때만 사용
uv run --project backend python scripts/check_live_chat.py --run
uv run --project backend python scripts/check_call_followup.py --run --case ambiguous_time
```

## 전화 없는 한국어 음성 평가

Python 3.12/macOS에서 `--prepare`로 Yuna 합성 입력을 만든다.
`--run`은 실제 Live API를 호출하며 ClawOps에 전화하지 않는다. 원본 음성·전사·이벤트와
격리 평가 DB는 Git 제외 `var/`에 저장된다. 합성 시험을 실제 회선의 청감·종료 성공으로 해석하지 않는다.

- `check_live_korean.py`: 음성·한국어 말투·끼어들기 비교. 위임 답과 회선 ACK는 모사한다.
- `check_live_dialogue.py`: 여러 차례 음성 대화. 실제 Live와 DeepAgents를 사용하고 회선 ACK를 모사한다.
- `replay_live_audio.py`: 저장된 패킷을 가상 시계로 재생 비교. API/전화/DB 접근 없음.
- `report_live_dialogue.py`: 저장 결과에서 청취 페이지 생성. API 호출 없음.

```bash
uv run --project backend python scripts/check_live_korean.py --prepare
uv run --project backend python scripts/check_live_korean.py --run --name korean-current --prompt current
```

다중 발화 평가의 반복 수·사례·비용 범위와 청취 서버 실행법은
[평가 CLI 안내](fixtures/live-dialogue/README.md)를 따른다. 실행 이름은 매번 새로 지정한다.
픽스처의 후보 프롬프트는 비교 기록이며, 현재 제품에 채택됐다는 뜻이 아니다.

- [한국어 음성 1차 비교](../docs/research/2026-09-27-live-korean-experiment.md)
- [다중 발화 34세션 결과](../docs/evaluations/2026-09-27-korean-dialogue.md)
- [재생 중단·재개 검증](../docs/superpowers/plans/2026-09-27-live-barge-in-control.md)

`live_korean_metrics.py`, `live_dialogue_cases.py`는 평가 보조 모듈이며 독립 실행 명령이 아니다.

## 가상 ARS 개발 서버

`./scripts/dev.sh`는 기존 웹(5180)·백엔드(9010)와 내부 ARS(9020)를 함께 시작한다.
웹 좌측 하단 **디버깅**으로 가상 통화 대화를 만든다. `dev_simulator.py`는 같은 실행기의
호환 진입점이며 별도 웹을 열지 않는다. ARS 제외 실행은 `VIRTUAL_ARS_ENABLED=0`을 지정한다.
`prepare_virtual_ars.py`는 macOS Yuna로 안내 음성을 만들고 `virtual_ars_process.py`는
내부 키와 자식 프로세스 설정을 준비한다. [자세한 안내](../docs/virtual-ars.md).
