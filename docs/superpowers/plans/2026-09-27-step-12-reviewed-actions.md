# 12단계: 확인 후 일정 등록·메일 발송

상태: in_progress. 승인: 2026-09-27 사용자의 자율 구현 요청. 실제 계정 쓰기는 이번 작업에서 하지 않는다.
설계: 메인 에이전트는 실행안을 제안하고, 사용자는 내용이 보이는 카드의 확인 버튼으로 실행한다.
채팅 모델이나 통화 상대가 확인 버튼을 대신 누를 수 없다. 기존 개인용 세션/DB/Google 연결을 사용한다.

## 계약과 범위

- `propose_calendar_event(title,start,end,description,location)`는 기본 캘린더의 단일 시간 일정만 지원.
  참석자 초대·기존 일정 변경/삭제·반복 일정은 포함하지 않는다. 시간대 필수, 범위 최대 31일.
- `propose_email(to,subject,body)`는 1~5 수신자의 일반 텍스트 메일만 지원. CC/BCC·첨부·기존 스레드 변경 없음.
- owner/conversation/source user message는 서버에서 바인딩한다. 모델은 approve/execute 도구를 갖지 않는다.
- 초안은 현재 연결된 계정 이메일에 묶고 30분 만료, payload는 immutable. 수정은 취소 후 새 메시지로 제안.
  같은 source/kind의 같은 payload는 같은 초안, 달라진 payload는 충돌. conversation당 최대 100건.
- 상태 pending/executing/succeeded/failed/unknown/rejected/expired. 재시작 중 executing은 unknown.
  네트워크·5xx로 실행 결과 불명확하면 자동 재전송하지 않는다. 재클릭/다른 탭 중복도 한 번만 실행.
- Google 쓰기 권한은 ‘일정 등록·메일 발송 허용’이라는 별도 명시적 OAuth 동의로 확장한다.
  코드 구현 중에는 동의·쓰기·발신을 수행하지 않는다. 승인 때 연결 계정과 실제 scope를 다시 검사한다.
- 실행 전 확인 카드에 대상 계정, 수신자/제목/본문 또는 시간/장소/설명을 모두 표시한다.
  도구 결과와 새로고침에서 실제 공급자 확인과 미확인을 구분한다.

## 구현 단위

- [ ] `actions/{store,manager,routes,tools}.py`: 모델 제안·owner 검사·source 중복·상태 CAS·만료·복구,
  Google API 쓰기와 종료 시 background task 정리. 출처는 고정 provider URL이며 모델은 URL/토큰 지정 불가.
- [ ] `integrations/google.py`, `routes.py`, 연결 UI: 조회 동의와 쓰기 추가 동의를 구분. scope 없는 쓰기 거절.
  `main.py`/chat runtime에 서비스와 제안 도구 및 현재 실행 상태 문맥을 연결.
- [ ] `web/src/actions/ActionCards.tsx`: 기존 메시지 아래 확인 카드. 승인/취소 중복 방지·상태 복원·
  모바일/키보드·긴 본문. 정적인 과거 채팅 응답과 현재 카드 상태를 구분한다.
- [ ] tests/actions: 권한/계정 변경·동시 승인·전송 후 응답 유실·재시작·만료·payload 충돌·header injection.
  모사 Calendar/Gmail만 사용. Chrome E2E로 정확한 실행안 표시와 중복 클릭·새로고침 확인.
- [ ] 전체 검사·문서·작은 커밋. 실제 Google 연결/쓰기 검증은 미완료로 남김.
