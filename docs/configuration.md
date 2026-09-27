# 모델과 앱 연결 설정

설정 변수의 기본값과 빈 템플릿은 [`.env.example`](../.env.example)을 기준으로 한다.

## 모델

프로젝트 루트의 `.env`에서 모델 설정을 읽는다. 처음 설치하는 환경에서는 `.env.example`을
복사한 뒤 `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`을 입력한다. 기존 `.env`는 덮어쓰지 않는다.
개발 검증에는 `gpt-5.6-sol`을 사용했다. 공급자가 지원하는 텍스트 모델 이름을 직접 지정한다.
`.env`는 권한 0600으로 보관하고 키 값을 브라우저에 전달하지 않는다.

- `MODEL_BASE_URL`: OpenAI 호환 Chat Completions API의 base URL.
- `MODEL_API_KEY`: 해당 API 키. `VITE_` 접두어를 사용하지 않는다.
- `MODEL_NAME`: 공급자가 받는 모델 이름.
- `MODEL_MAX_TOKENS`: 기본 2048, 양의 정수.
- `MODEL_TRUST_ENV`: HTTP 프록시 환경변수 사용 여부(1/0). 프록시를 쓰지 않을 때만 0으로 지정한다.

프로세스 환경변수가 파일보다 우선하며, `AGENT_SERVICE_ENV_FILE`로 명시한 파일을 사용할 수도 있다.
다른 프로젝트의 설정은 자동으로 읽지 않는다. `.env` 변경은 다음 요청에 반영되고,
프로세스 환경변수를 바꿀 때는 서버를 다시 시작한다. health 확인은 모델 호출이나 발신을 하지 않는다.
서버 시작도 새 발신은 하지 않는다.
시작 시 이전의 미종료 회선은 상태 조회·종료 정리를 수행하고, 대기 중인 통화 결과 보고는
모델을 호출할 수 있다. 자세한 동작은 [복구 안내](local-recovery.md)를 따른다.
설정이 빠져도 서버는 시작되며, 메시지를 보낼 때 설정 오류를 표시한다.

모델은 공식 `deepagents==0.7.19`와 `langchain-openai==1.6.6`으로 연결한다.
메인 모델에는 전화 시작·조회·종료·추가 지시, 앱 연결 조회, Calendar 목록·일정 조회,
Gmail 검색·본문 읽기와 일정/메일 실행안 제안 도구를 제공한다. 셸·파일·하위 에이전트 도구는 없다.
실행안의 승인·발송/등록은 웹 버튼과 서버에서 처리하며 모델에 승인 도구를 주지 않는다.
Live는 client delegation으로 DeepAgents에 요청한다. 통화 업무 도구는 `ask_user`, `send_dtmf`,
`end_call`, `check_calendar_availability`다. 일정 제목/메일 원문을 상대에게 자동 공개하지 않으며,
연결/권한이 없거나 결과가 불확실하면 웹 채팅으로 요청자에게 확인한다.

통화는 `CALLS_ENABLED=1`, `CLAWOPS_ACCOUNT_ID`, `CLAWOPS_API_KEY`, `CLAWOPS_FROM_NUMBER`와
`CALL_ALLOWED_NUMBERS`가 필요하다. `CALL_MAX_SECONDS`는 1~180초다.
Live와 Realtime은 `CALL_REALTIME_BASE_URL`과 `CALL_REALTIME_API_KEY` 설정을 함께 사용한다.
`CALL_AUDIO_MODE=live`, `CALL_LIVE_MODEL=gpt-live-1`, `CALL_LIVE_VOICE=marin`으로 활성화한다.
Azure Live 경로는 `/openai/v1/live/sessions`이며 Realtime의 이벤트와 다르다.
`CALL_AUDIO_MODE=realtime`은 기존 비교 경로다. 자동 폴백은 하지 않는다.

통화 카드의 선택지를 누르거나 ‘직접 답변하기’를 누른 뒤 기존 입력창으로 답한다.
답변 대상 표시는 일반 채팅과 구분되며, 해제하면 일반 대화로 돌아온다.
질문과 답변은 새로고침 후 복원되고 기본 60초 후 마감된다. ‘전달됨’은 통화 모델이
결과를 접수했다는 뜻이며 상대방이 들었다는 보장은 아니다. 일반 채팅을 자동으로 읽어주지 않는다.
[구현·검증 기록](superpowers/plans/2026-09-26-step-06-live-delegation.md)을 참고한다.

## Google Calendar · Gmail

[Google 설정 안내](google-setup.md)에 따라 OAuth 웹 클라이언트를 만들고 서버의 `.env`에
클라이언트 ID·비밀키를 저장한다. 각 설치 환경에서 웹의 앱 연결을 통해 본인 계정으로 동의한다.
조회 권한과 쓰기 권한은 구분하며, 일정 등록·메일 발송은 매번 확인 카드에서 승인한다.
기존 개발 환경의 로그인 정보는 저장소에 포함되지 않는다.

## 로컬 Langfuse 실행 기록

`.env`에 `LANGFUSE_TRACING_ENABLED=1`, `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`를 설정하고 백엔드를 다시 시작한다. 기본은 비활성이며 현재 구현은
localhost/127.0.0.1/::1의 로컬 수집 서버만 허용한다. 잘못되거나 빠진 설정은 추적을 비활성화한다.
키는 서버에만 보관하고 Git에 넣지 않는다. Langfuse 서버는 별도로 설치·실행해야 하며,
이 저장소에 특정 개발자의 컨테이너나 수집 서버 데이터는 포함하지 않는다.

Langfuse에서 `main-chat`, `call-delegation`, `call-completion-review` 실행 아래의
모델(GENERATION)·도구(TOOL) 이름, 시작/종료 시간, `completed`/`error`/`canceled`를 확인한다.
동일 원 요청의 채팅·통화 위임은 같은 trace ID, 같은 대화는 같은 session ID로 연결한다.
식별자는 내부 UUID에서 해시하며 전화번호·이메일은 넣지 않는다. 입력·출력·전사·오류 원문도
수집하지 않는다. `completed`는 해당 모델/도구 실행의 반환을 뜻하며 예약 확정이나 통화 종료
완료를 보증하지 않는다. 반환된 `error` 및 도구 예외는 오류로 표시한다.

SDK가 백그라운드로 전송하고 수집 오류를 업무 실행에 전파하지 않는다. 수집 장애 중의 기록을
로컬 디스크에 재전송용으로 보존하지 않으므로 누락될 수 있다. 종료 단계는 `call.end_tool_requested`, `call.farewell_commands_acked`,
`call.end_playback_finished`, `call.carrier_end_confirmed` 등의 관측으로 확인한다.
재생 상태는 음성 활동과 ACK 기반의 추정이다. 실제 발화·청취 성공을 뜻하지 않는다.
Live 음성 패킷과 확인 카드에서 실행하는 Google 쓰기 작업 자체는 추적 범위에 포함하지 않는다.

발신·외부 모델 호출 없는 합성 DeepAgents 그래프 수집 검증:

```bash
cd backend
uv run python ../scripts/check_langfuse_tracing.py --run
```

이 명령은 로컬 Langfuse에 합성 기록을 남기고 v2 observations API에서 저장·관계·원문 제외를
확인한다. 현재 서버의 events_only 모드에서는 기존 trace 조회 API 대신 v2 observations를 쓴다.
[구현·검증 기록](superpowers/plans/2026-09-27-langfuse-tracing.md)을 참조한다.
