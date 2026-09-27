# DeepAgents Langfuse 실행 추적

상태: completed

## 승인

로컬 Langfuse 설정·인증·합성 기록 저장 검증 후 다음 작업으로 DeepAgents 실행에
Langfuse 추적 연결을 제안했고 사용자가 “진행해줘.”라고 승인했다.
기존에 제안한 메인 채팅/통화 위임/도구 실행 관계 추적과 개인정보 제외 범위로 진행한다.

## 설계와 작업 단위

1. 서버 전용 선택 설정과 Langfuse SDK 클라이언트. 기본 비활성, 현재 로컬 환경만 활성화.
2. 개인정보를 전달하지 않는 LangChain callback: 모델/도구 이름, 종류, 소요 시간,
   완료/실패/취소를 기록한다. 입력/출력/오류 원문/모델 연결 주소/키는 전송하지 않는다.
3. 메인 채팅과 별도 통화 위임에 연결. 원 요청 메시지에서 계산한 trace ID로 관련 실행을
   묶고 내부 대화 식별자에서 계산한 session ID를 사용한다. 전화번호/계정 이메일은 제외한다.
4. 종료·취소 시 열린 관측을 닫고 서버 종료 시 전송 큐를 정리한다.
   수집 서버 장애/잘못된 설정은 업무 실패로 전파하지 않는다.
5. 테스트에서 모델과 도구를 모사하여 개인정보 제외, 오류 반환, 예외, 취소와
   동시 실행 격리를 확인한다. 실제 로컬 Langfuse에서 자동 생성된 관측을 재조회한다.

## 파일과 검증

- backend/src/agent_service/observability.py (설정, 클라이언트, callback, 실행 수명)
- chat/runtime.py, calls/delegation.py, main.py (연결과 종료)
- backend/tests/test_observability.py 및 기존 chat/calls 테스트
- .env.example, backend/pyproject.toml, uv.lock, 운영 문서
- 모사 회귀 검사 + 실제 로컬 수집/재조회. 실제 발신·Google 조회·메일 발송은 없음.

## 완료 기준

모델과 도구 관측이 자동 수집되고 원문은 없으며 실패/취소가 구별된다.
기존 채팅/위임 흐름이 유지되고, 수집 장애가 업무 실행을 막지 않는다.
실행 메타데이터는 완료 여부의 근거이며 외부 작업 성공이나 상대 청취를 보증하지 않는다.

## 결과와 검증

- Langfuse SDK 4.15.6과 별도 OpenTelemetry provider, 최소 callback을 연결했다.
  공식 기본 LangChain callback에 원문을 넘기는 대신 허용한 메타데이터만 관측으로 만든다.
- `.env`의 로컬 추적을 활성화하고 활성 채팅/통화 0건을 확인한 뒤 백엔드를 재시작했다.
- `AGENT_SERVICE_ENV_FILE=/dev/null uv run pytest -q`: 최종 백엔드 256개 검사 통과.
  Ruff 검사와 변경 Python 파일 포맷 확인 통과.
- 실제 SDK exporter 경계에서도 원문 미포함을 검증했고, exporter가 실패를 반환해도
  도구 실행과 관측 종료가 정상적으로 완료됨을 확인했다.
- 합성 실제 DeepAgents 그래프의 main-chat/call-delegation root 2건, generation 4건,
  tool 2건을 같은 trace에서 재조회했다. 부모 관계·종료 시각·입력/출력 부재·private marker
  미포함까지 확인했다. 외부 모델이나 실제 전화/Google 작업은 실행하지 않았다.
- 재시작한 실제 웹에서 앱 연결 상태만 묻는 요청을 실행했다. gpt-5.6-sol 호출 2건과
  get_connected_apps 1건, main-chat root 1건이 자동 저장됐고 모두 completed였다.
  v2 observations 재조회에서 원문 입력/출력 없음, 예상 밖 도구 없음도 확인했다.
- 서로 다른 동시 실행과 동일 이름 도구는 실행 ID로 구분하고 종료/취소 시 관측을 닫는다.
  일반 오류 반환도 도구 실패로 표시하되 업무 성공 여부와는 구분한다.

## 범위와 제한

통화 위임의 실제 새 회선 검증은 하지 않았다. Live 음성 스트림 자체, 회선 제어의 전체 수명,
웹 확인 카드에서 실행하는 실제 Google 쓰기는 이번 관측에 포함하지 않는다.
입출력 원문이 없으므로 조회 대상 날짜·결과 내용은 Langfuse만으로 재구성할 수 없다.
수집 실패는 업무를 방해하지 않지만 메모리 큐의 기록은 유실될 수 있고 영구 재전송 큐는 없다.

참고: [공식 SDK 관측 API](https://langfuse.com/docs/observability/sdk/instrumentation),
[공식 Python API](https://python.reference.langfuse.com/langfuse).
