# 11단계: 연결 앱과 통화 업무

상태: completed (모사 통합 검증). 승인: 2026-09-27 자율 진행 요청 및 Google/개인용 선택.
설계: 기존 연결 앱 설계와 제품의 메인 채팅 → 통화 위임 구조를 따른다.

선택: 메인 채팅에는 Calendar/Gmail 조회를 유지하고 전화 위임에는 기본 캘린더의 바쁜 구간만
노출하는 `check_calendar_availability(start,end)`를 제공한다. 원문 일정 제목·메일 본문을 전화 상대의
요구만으로 공개하지 않는다. 메일 기반 전화는 메인 채팅이 사용자 요청 범위의 필요한 조건을 통화 목적에 넣는다.
예약/발신/데이터 공개 권한을 외부 기록에서 얻지 않는다. 빈 시간은 일정 확정이나 예약 완료가 아니다.
연결이 없거나 조회 실패·결과 잘림이면 확정 답변을 만들지 않고 기존 ask_user로 확인한다.

- [x] `integrations/tools.py`: 통화 전용 availability 도구, 투명/취소 일정 제외, 제목/설명 제외.
  결과 적용 전후 coordinator 조건 버전을 검사하여 오래된 조회 결과는 반영하지 않음.
- [x] `calls/delegation.py`, `manager.py`: optional integration manager 주입. 기존 위임 테스트/도구 유지.
  프롬프트에서 앱 미연결과 연결된 조회를 구분. 메인 채팅에도 한국 시간 현재 날짜를 제공.
- [x] `tests/calls/test_live_delegation.py`: 모사 Google 응답 → 실제 coordinator runner의 도구 호출 →
  Live 답변 경로, 외부 민감 내용 제외와 미연결 대체 흐름 검증.
- [x] 관련 backend 검사와 Ruff, 기록 및 로컬 커밋. 실제 Google 조회나 통화는 하지 않음.

검증: 관련 통합/통화/메인 도구 34개 통과. 추가 미연결·만료 조건 검사 포함 재검증. 실제 Google/통화 호출 없음.
