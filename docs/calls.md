# 통화 연결

5단계 구현 중. 현재 어댑터는 모사 테스트로 검증했다. 실회선 검증 결과는 완료 시 추가한다.

## 이식 출처

`../calling-agent/src/calling_agent`의 ClawOps control/connection/native,
Azure audio 세션, native_audio bridge, DTMF/end_call 계약을 필요한 범위만
`backend/src/agent_service/calls`에 이식했다. 관련 계약 테스트를 함께 옮겼다.
PCM 변환·Whisper·TTS·CLI·Journal은 가져오지 않았다. 형제 프로젝트 런타임 의존성은 없다.

G.711 μ-law 8kHz 음성을 ClawOps와 Realtime 사이에서 직접 전달한다. 서버 VAD의
끼어들기와 원격 재생 mark를 사용하며, 입력 음성 전사는 비활성화다.
음성 모델은 `send_dtmf`, `end_call`만 실행한다. 버튼 전송 성공은 메뉴 전환의 증거가 아니며,
종료 요청은 마지막 인사 재생 확인 후 서버가 회선 종료를 요청하는 동작이다.

`.env.example`의 `CALL_*`, `CLAWOPS_*`는 텍스트 모델 `MODEL_*`와 별도다.
기본적으로 통화는 비활성화이며, 발신 대상은 로컬 허용 목록으로 제한한다.
키·번호·음성 및 통화 결과는 Git에 넣지 않는다.
