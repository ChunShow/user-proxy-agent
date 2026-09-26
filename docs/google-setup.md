# Google Calendar와 Gmail 연결

현재 앱은 개인용 로컬 웹 서비스다. Google 계정 연결 정보는 이 브라우저의 로컬 서비스 세션에 연결된다.
실제 로그인·권한 동의는 본인이 직접 진행한다. 기존 Codex 커넥터의 연결과는 별개다.

1. [Google Cloud Console](https://console.cloud.google.com/)에서 사용할 프로젝트를 선택하고
   Google Calendar API와 Gmail API를 활성화한다.
2. OAuth 동의 화면의 앱 이름과 연락처를 설정한다. 개인 시험용이면 테스트 사용자에 본인 계정을 등록한다.
3. OAuth 클라이언트를 **웹 애플리케이션** 유형으로 만든다. 승인된 리디렉션 URI를 다음과 같이 등록한다.

   `http://127.0.0.1:5180/api/integrations/google/callback`

4. 프로젝트 루트의 Git 제외 `.env`에 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`을 등록한다.
   키를 채팅이나 브라우저의 일반 입력란에 붙여 넣지 않는다. 웹 포트를 변경했다면
   `GOOGLE_REDIRECT_URI`도 동일한 127.0.0.1 웹 포트의 `/api/integrations/google/callback`으로 맞춘다.
5. 웹 상단 **앱 연결**을 열고 **Google 계정 연결**을 누른다. 계정과 권한을 확인하고 직접 동의한다.
   Calendar 또는 Gmail 권한을 선택하지 않으면 해당 기능은 사용하지 않고 필요한 권한을 안내한다.
6. 채팅에서 “오늘 일정 확인해줘”, “이번 주 회의 관련 메일을 찾아줘”처럼 요청한다.
   일정 조회는 기본 캘린더를 기준으로 한다. 다른 캘린더는 이름을 지정한다.

첫 연결은 일정·캘린더 목록 조회와 메일 읽기 권한만 요청한다. 이벤트 참석 여부나 빈 시간 조회 결과는
일정 등록·초대·메일 발송의 승인을 뜻하지 않는다. 메일/일정 본문은 응답 생성에 필요한 경우 설정된
AI 모델에 전달되므로 그 점을 고려해 연결한다. 첨부 파일은 읽지 않는다.

토큰은 서버의 `data/agent-service.sqlite3` 안에 암호화해서 보관한다. 암호화 키는
`data/integration.key`에 0600 권한으로 저장하며 두 파일 모두 Git에서 제외한다.
백업/복원은 DB와 이 키를 함께 취급한다. 키를 잃으면 기존 토큰을 읽을 수 없으므로 계정을 해제하고
다시 연결해야 한다. DB를 가진 로컬 관리자에게서 데이터를 격리하는 구조는 아니다.

연결을 해제하면 로컬 토큰 사용을 즉시 차단하고 Google 토큰 철회를 요청한다. Google 접속 실패로
철회가 확인되지 않으면 화면에 구분해 안내한다. 이 경우 [Google 계정 연결 관리](https://myaccount.google.com/connections)에서도
접근 권한을 해제할 수 있다. 브라우저 쿠키를 지우면 기존 로컬 계정의 대화와 연결에 접근할 수 없으므로
먼저 앱 연결을 해제한다. 외부 배포·다중 사용자 로그인은 아직 지원하지 않는다.

연결 흐름은 state 만료·중복·다른 브라우저, 토큰 갱신·철회·늦은 조회 결과를 모사 공급자로 검사한다.
실제 Google 연결은 자격증명 등록과 본인 동의 후 별도로 확인해야 한다.

공식 문서: [OAuth 웹 서버 흐름](https://developers.google.com/identity/protocols/oauth2/web-server),
[Calendar 권한](https://developers.google.com/workspace/calendar/api/auth),
[Gmail 메일 목록](https://developers.google.com/workspace/gmail/api/guides/list-messages).

## 일정 등록·메일 발송

앱 연결에서 **일정 등록·메일 발송 허용**을 누르고 추가 Google 권한에 직접 동의한다.
채팅에서 요청하면 실행안이 대화 아래 카드로 표시된다. 대상 계정, 시간 또는 수신자·본문을 확인하고
**확인하고 등록/발송**을 눌러야 실행한다. 모델은 제안만 할 수 있고 확인 버튼을 대신 누를 수 없다.
기본 캘린더의 단일 일정, 최대 5명에게 일반 텍스트 메일을 지원한다. 참석자 초대·반복 일정·기존 일정
수정/삭제·첨부 파일은 아직 지원하지 않는다. 실행안은 30분 뒤 만료되고 수정은 취소 후 새 요청으로 한다.

결과를 확인하지 못했거나 실행 중 서버가 재시작되면 **실행 여부 확인 필요**로 표시하며 재전송하지 않는다.
Google에서 실제 등록/발송 여부를 먼저 확인한다. 이 기능은 모사 Google API로 검증했으며 실제 쓰기는 미검증이다.
공식 문서: [Calendar 등록](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert),
[Gmail 발송](https://developers.google.com/workspace/gmail/api/guides/sending).
