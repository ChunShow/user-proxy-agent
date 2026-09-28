# 가상 ARS로 통화 디버깅

ClawOps 계정이나 실제 전화 없이 기존 채팅·승인 카드·Live-1·DeepAgents를 시험한다.
가상 ARS는 **별도 프로세스의 로컬 HTTP/WebSocket API**다. 외부 전화/TTS API 가입은
필요 없다. Live-1과 메인 모델 호출은 기존 `.env`를 사용하므로 모델 사용 비용은 발생한다.

## 실행

기본 개발 환경과 의존성은 [README](../README.md)의 시작하기를 따른다.
Python 3.12와 macOS의 `say`/`afconvert`, 설치된 한국어 `Yuna` 음성을 사용한다.
저장소 루트에서:

```bash
backend/.venv/bin/python scripts/dev_simulator.py
```

- 디버그 웹: http://127.0.0.1:5181
- 디버그 백엔드: http://127.0.0.1:9011
- 독립 ARS API: http://127.0.0.1:9020/health
- 종료: 실행 터미널에서 Ctrl-C. 이 명령이 시작한 세 프로세스만 종료한다.

세 포트 중 하나라도 사용 중이면 시작하지 않는다. 안내 음성은 첫 실행 때 로컬에서 생성하고
`var/simulator/audio/`에 캐시한다. API 키는 `var/simulator/api-token`에 권한 0600으로
자동 생성한다. `.env`에 별도 ARS 키를 등록할 필요 없다.

`.env`에는 기존 `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`과
`CALL_REALTIME_BASE_URL`, `CALL_REALTIME_API_KEY`, `CALL_REALTIME_MODEL` 설정이 필요하다.
디버그 실행기는 통화 모드를 `live`, 모델을 `gpt-live-1`로 지정한다.

## 사용

상단에 **가상 통화 · 실제 전화 발신 없음**이 표시된 5181 웹에서 다음처럼 요청한다.

> 01000000001 가상 병원 ARS에 전화해서 진료 시간과 점심시간, 주말 진료 여부를 확인해줘.
> 자동 안내를 먼저 듣고 필요한 메뉴를 선택해. 정보를 알면 통화를 종료해줘.

카드의 **승인하고 전화 걸기**를 눌러야 가상 회선이 연결된다. 기존과 같이 전사,
통화 듣기, 추가 지시, 직접 종료를 사용할 수 있다. 시험 번호는 **이 디버그 서비스에서만**
동작한다. 기존 5180/공유 웹에서는 시험하지 않는다.

현재 시나리오는 하나다. `4(기타 안내) → 2(진료 시간)`을 누르면 평일 09:00–18:00,
점심 12:00–13:00, 주말·공휴일 휴진을 안내한다. **모두 가상 시험 데이터이며 실제 병원 정보가 아니다.**
다른 번호 선택에는 오류 안내를 재생한다. 입력이 없으면 20초 대기 후 최대 두 번 재안내하고
종료한다. 회선 전체 제한은 180초다. 음성만으로 메뉴를 선택하지 않으며 DTMF 이벤트가 필요하다.

## 분리 경계

- 디버그 앱은 `SimulatorGateway`만 사용한다. 장애 시 ClawOps로 전환하지 않는다.
- 등록된 가상 번호 외에는 ARS API에서 거부한다. 운영 발신번호·ClawOps 키가 필요 없다.
- 통화 승인과 소유권 검사는 운영과 같은 코드를 거친다.
- DB는 `var/simulator/debug.sqlite3`, 쿠키는 `proxy_simulator_session`으로 운영과 분리한다.
- Google 연결과 쓰기 도구는 디버그 앱에서 비활성화한다.
- 모두 loopback 바인딩이다. 기존 운영 서버·Cloudflare 주소는 이 디버그 웹으로 바뀌지 않는다.

## API

`GET /health`를 제외한 REST와 WebSocket에는
`Authorization: Bearer <var/simulator/api-token의 값>` 헤더가 필요하다.
키를 브라우저 코드나 커밋, 공유 URL에 넣지 않는다.

| 메서드 | 경로 | 내용 |
| --- | --- | --- |
| GET | `/health` | 준비 상태, `real_calls=false` |
| GET | `/scenarios` | 시나리오와 가상 번호 |
| POST | `/calls` | `{ "request_id": "UUID", "destination": "01000000001" }` |
| GET | `/calls/{id}` | 메뉴·입력·재생·종료 증거 |
| POST | `/calls/{id}/hangup` | 가상 회선 종료 |
| WS | `/calls/{id}/media` | 양방향 G.711 μ-law, 8kHz mono |

동일 `request_id`는 같은 회선을 반환한다. 통화 ID는 `SIM` 접두사다.
회선당 미디어 연결은 하나만 허용하며, 재접속/재발신은 자동으로 하지 않는다.

서버는 `start`, 20ms/160바이트 `media`, `mark`, `stop` 이벤트를 보낸다.
클라이언트는 다음 형식으로 오디오·버튼·재생 제어를 전달한다.

```json
{"event":"media","media":{"payload":"BASE64_MULAW"}}
{"event":"dtmf","dtmf":{"digit":"4"}}
{"event":"mark","mark":{"name":"playback-1"}}
{"event":"clear"}
```

`mark`는 앞선 오디오의 실제 시간 길이만큼 대기한 후 반환한다. `clear`는 대기 오디오와
미확인 mark를 폐기한다. 재생량은 무음을 포함한 μ-law 바이트 수이며 사람이 들었다는 증거는 아니다.

조회 결과의 `digits`는 실제 수신한 DTMF, `stage`는 메뉴 상태,
`hours_delivered`는 진료 안내가 끝까지 전송됐는지 나타낸다. `events`에는 상대 시간과
선택·안내 재생·종료 이유가 남는다. 이 회선 기록은 메모리에 최대 100개,
각 300개 이벤트로 제한한다. 재시작하면 사라진다. 앱의 전사·활동 기록은 디버그 DB에 남는다.

## 검증과 범위

자동 검사는 인증, 번호 거부, 중복 생성, 메뉴 선택, 잘못된 입력, 무입력 종료,
단일 WS 연결, 실제 시간 mark ACK, clear, 직접 종료와 ClawOps 미사용을 검사한다.
실제 Live-1 검증은 [단계 기록](superpowers/plans/2026-09-28-virtual-ars.md)을 참고한다.

이는 통신망 품질이나 실제 병원 응대의 검증이 아니다. 현재 가상 상대는 고정 음성 ARS이며
자유 대화 상담원, 여러 기관으로의 재발신, 잡음·회선 지연·끊김 시나리오는 후속 확장이다.
