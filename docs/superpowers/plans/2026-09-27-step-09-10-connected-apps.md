# Google Calendar·Gmail 연결 구현 계획

> 직접 순차 실행한다. meaningful behavior는 실패 검사 → 구현 → 검증 → 커밋으로 진행한다.

**목표:** 개인용 웹에서 계정을 연결하고 메인 채팅이 실제 Calendar/Gmail 조회 API를 사용할 수 있게 한다.
**구조:** owner 기반 연결 저장 → OAuth/갱신 manager → bounded Google 조회 → 메인 도구 → 연결 화면.
**기술:** FastAPI, SQLite, httpx, cryptography/Fernet, 기존 DeepAgents·React.
**설계:** `docs/superpowers/specs/2026-09-27-connected-apps.md`.
**승인:** `2026-09-27-autonomous-product.md`의 사용자 자율 진행 요청. 상태 in_progress.

## 1. 암호화 저장과 OAuth

파일: `backend/src/agent_service/integrations/{settings,store,google,routes}.py`, `main.py`,
`backend/tests/integrations/test_google.py`, `.env.example`, `backend/pyproject.toml` 및 lock.

- [ ] `IntegrationStore`는 owner별 Google 토큰·scope·version과 10분 일회용 state/쿠키 nonce/PKCE를 저장.
  토큰은 암호화, public status는 email/허용 기능/connected·reconnect_required만 노출.
- [ ] `GoogleManager.connect/callback/disconnect/access`로 code 교환·scope 검증·refresh·철회와
  동시에 들어오는 연결 해제/계정 교체 경합을 처리. 사용자 없는 자동 로그인은 하지 않음.
- [ ] GET status, POST connect/disconnect, GET callback. 모든 mutation origin/세션 확인.
  콜백은 state+브라우저 쿠키 바인딩 후 고정 웹 경로로 복귀. 외부 error_description은 렌더/로그하지 않음.
- [ ] 가짜 공급자 HTTP로 state 만료·재사용·다른 브라우저·거절·누락 scope·refresh 실패·암호화·
  disconnect 중 refresh를 검사. `uv run --locked pytest tests/integrations -q` 및 Ruff 통과 후 커밋.

## 2. 조회와 에이전트 도구

파일: `integrations/{queries,tools}.py`, `calls/tools.py`, `chat/runtime.py`, `tests/integrations/test_tools.py`.

- [ ] `list_calendars`, `list_calendar_events`, `search_email`, `read_email`의 bounded 결과와 오류 계약.
  날짜/시간대·31일 제한·종일/반복 일정·잘림·MIME 본문·HTML 안전 변환·제한된 응답 크기를 검사.
- [ ] 서버의 CallContext manager를 통해 GoogleManager와 owner를 주입. 모델은 URL/토큰 지정 불가.
  연결 상태에 따라 도구 결과가 연결/권한 필요를 알리고 외부 기록 속 지시는 권한으로 취급하지 않음.
- [ ] 모사 실제 DeepAgents 도구 호출과 API transport 조합 검사. 사용자 A/B 결과 분리·해제 후 늦은
  결과 폐기·401/403/429/5xx·빈 결과·제한된 결과 구분을 테스트하고 커밋.

## 3. 앱 연결 화면과 실행 안내

파일: `web/src/integrations/{ConnectedApps.tsx,apps.ts,apps.css}`, `App.tsx`, `ChatView.tsx`,
`web/e2e/connected-apps.spec.ts`, `docs/google-setup.md`, `README.md`.

- [ ] 기존 단정한 메신저 안에 ‘앱 연결’ 대화상자. 설정 없음·미연결·연결·부분 권한·재연결·해제 실패
  상태와 명확한 조회 범위를 표시. 새 색 체계/장식은 만들지 않음. frontend-design 적용.
- [ ] OAuth를 명시적 클릭으로만 시작. 모사 callback 복귀·새로고침·Escape/포커스 복귀·모바일·
  네트워크 오류를 E2E 검사. credentials는 브라우저 UI나 스크린샷에 노출하지 않음.
- [ ] 공식 Google 설정과 정확한 redirect URI, .env 변수, 토큰 키 백업, 현재 로컬 전용 제한을 문서화.
- [ ] `./scripts/check.sh`, 관련 Chrome E2E, 합성 화면 검토. 기존 실제 데이터 보존·활성 통화 0 확인 후
  서버 반영. 구현 검증과 실제 Google 계정 연결 미검증을 구분해 기록·커밋.
