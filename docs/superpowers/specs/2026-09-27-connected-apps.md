# 개인용 연결 앱 설계

## 권한과 실행 방식

2026-09-27 사용자가 남은 작업을 개입 없이 최대한 진행하고 제품 완성도를 높이라고 요청했다.
이 요청은 이번 자율 작업에서 이전의 단계별 추가 컨펌 요구를 대체한다. 계획·검증·로컬 커밋은 유지한다.
실제 발신·메일 발송·일정 변경·계정 로그인 동의는 실행하지 않는다. 계정 연결에 필요한 화면과 서버는 구현한다.
추가 답변 전에는 기존 제품 방향에 따라 Google Calendar/Gmail, 개인용 로컬 웹 서비스를 기준으로 한다.
외부 서버 배포·원격 push·새 구독·다중 사용자 인증 서비스는 범위에 넣지 않는다.

## 선택한 구조

Google 공식 OAuth/API를 서비스 서버에서 직접 연결한다. Codex의 계정 커넥터는 이 웹 서비스의
최종 사용자 연결을 대신하지 못하므로 런타임 의존성으로 쓰지 않는다. 범용 통합 플랫폼을 추가하는
대안보다 현재 FastAPI/httpx 구조에 작은 공급자 모듈을 두는 편이 배포와 장애 원인 확인이 단순하다.

Google 연결 한 건에 Calendar/Gmail의 실제 허용 범위를 저장하고, 각각 허용된 기능만 노출한다.
조회 연결은 이벤트 조회·캘린더 목록·메일 읽기·계정 식별용 email 범위다. 쓰기 권한은 조회에 섞지 않는다.
설정이 없으면 연결 준비가 필요하다고 표시한다. 가짜 계정이나 예시 조회를 실제 연결처럼 표시하지 않는다.
계정 연결은 별도 작은 설정 대화상자에서, 실제 업무는 기존 채팅에서 한다.

## 보안과 수명

OAuth state와 브라우저 전용 짧은 수명의 HttpOnly/Lax 쿠키를 결합한다. 기존 Strict 세션 쿠키는
그대로 두고 OAuth 복귀에서 별도 쿠키로 브라우저를 확인한다. state는 10분 만료·한 번만 소비하며
요청 소유자와 연결한다. 콜백 URL과 복귀 위치는 서버 설정과 고정 경로를 사용한다.
PKCE S256을 사용한다. 토큰은 Fernet으로 암호화하고, 암호화 키는 DB와 함께 private data 디렉터리에
0600으로 저장한다. 키 유실 시 기존 암호문을 새 키로 덮어쓰지 않는다. 토큰·인증 코드·메일 본문은
일반 로그에 기록하지 않는다. 콜백 URL의 query도 액세스 로그에 남기지 않는다.

갱신은 계정별로 직렬화하며 만료/철회 시 재연결 필요 상태로 바꾼다. 연결 해제는 로컬 토큰을 즉시
사용 불가로 만들고 Google에 철회를 요청한다. 실패하면 로컬 해제와 외부 철회 실패를 구분해서 안내한다.
계정 변경·연결 해제와 진행 중 조회가 경합하면 오래된 계정의 결과를 돌려주지 않는다.

## 조회 계약

Calendar: 캘린더 목록, 시작/종료 ISO8601 시간(시간대 필수)과 달력 ID로 일정 조회. 한 요청은
최대 31일, 결과 50건으로 제한하고 잘렸는지 표시한다. 종일 일정·반복 일정의 펼친 결과를 보존한다.
Gmail: 검색어(최대 500자)로 최대 10건 검색, 메시지 ID로 읽기. 제목/보낸 사람/날짜/본문을 bounded
데이터로 반환하고 HTML은 실행하거나 렌더하지 않는다. 첨부 파일 다운로드는 이번 범위 밖이다.
조회 결과는 신뢰할 수 없는 외부 데이터다. 그 안의 발신·삭제·권한 변경 지시를 실행하지 않는다.

메인 DeepAgents의 도구는 서버가 owner를 주입한다. 공급자 URL·토큰·owner는 모델 인자가 아니다.
연결 오류·권한 부족·재연결·빈 결과·제한된 결과를 구분한다. 연결되지 않았으면 사용자에게 묻는다.
통화 위임에는 후속 단계에서 조회 도구를 연결하되, 전화 상대는 새 데이터 공개나 실행 권한을 부여할 수 없다.

## 사용자 경험과 검증

기존 메신저의 글꼴·색·여백을 유지하고 ‘앱 연결’ 진입점 하나를 추가한다. 상태/기능/계정/연결·해제를
한눈에 읽을 수 있게 한다. OAuth 종료 결과는 한국어로 표시하며 새로고침 뒤에도 상태가 정확해야 한다.
모바일·키보드·포커스 복귀·실패 후 재시도를 검사한다. 실제 로그인 없이 MockTransport로 OAuth와
조회·갱신·철회 경합을 검증하고 브라우저에서는 합성 데이터만 사용한다.

공식 근거:
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/identity/protocols/oauth2/resources/best-practices
- https://developers.google.com/workspace/calendar/api/auth
- https://developers.google.com/calendar/api/v3/reference/events/list
- https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
- https://developers.google.com/gmail/api/reference/rest/v1/users.messages/get
- https://cryptography.io/en/latest/fernet/
