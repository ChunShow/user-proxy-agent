const errors: Record<string, [string, boolean]> = {
  invalid_title: ['대화 이름을 1~80자로 입력해 주세요.', false],
  conversation_active: ['답변 작성이나 통화, 작업 실행이 끝난 뒤 삭제해 주세요.', false],
  not_found: ['대화를 찾을 수 없습니다.', false],
  session_expired: ['세션이 만료되었습니다. 화면을 새로 불러와 주세요.', false],
  storage_unavailable: ['대화를 저장하지 못했습니다. 다시 불러와 확인해 주세요.', true],
  conversation_busy: ['다른 창에서 답변을 작성하고 있습니다.', false],
  request_exists: ['이미 접수된 요청입니다. 저장된 대화를 불러옵니다.', false],
  request_conflict: ['전송 내용이 기존 요청과 다릅니다. 다시 불러와 주세요.', false],
  invalid_retry: ['이 응답은 다시 시도할 수 없습니다. 대화를 다시 불러와 주세요.', false],
  load_failed: ['대화를 불러오지 못했습니다. 다시 불러와 주세요.', false],
  not_configured: ['서버의 모델 설정을 확인해 주세요.', false],
  invalid_request: ['메시지 길이를 확인해 주세요. 대화가 길면 새 대화를 시작해 주세요.', false],
  provider_auth: ['모델 인증에 실패했습니다. 서버 설정을 확인해 주세요.', false],
  rate_limited: ['사용량 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.', true],
  provider_unavailable: ['응답을 받지 못했습니다. 다시 시도해 주세요.', true],
  agent_step_limit: ['처리 단계가 많아 완료하지 못했습니다. 요청 범위를 줄여 다시 질문해 주세요.', false],
  timeout: ['응답 시간이 초과되었습니다. 다시 시도해 주세요.', true],
  invalid_stream: ['응답이 정상적으로 완료되지 않았습니다. 다시 시도해 주세요.', true],
}
export class ApiError extends Error {
  retryable: boolean
  code: string
  constructor(code: string) {
    const [message, retryable] = errors[code] ?? errors.provider_unavailable
    super(message)
    this.retryable = retryable
    this.code = code
  }
}
