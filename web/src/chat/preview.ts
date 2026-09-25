import type { CallViewModel, ChatMessage } from './types'

export const previewOptions = [
  { id: 'dialing', label: '연결 중' },
  { id: 'connected', label: '통화 중' },
  { id: 'needs_input', label: '추가 확인 필요' },
  { id: 'succeeded', label: '목표 달성' },
  { id: 'incomplete', label: '종료했지만 미완료' },
  { id: 'failed', label: '연결 실패' },
] as const
export type PreviewId = typeof previewOptions[number]['id']

export const previewMessages: ChatMessage[] = [
  { id: 'preview-user', role: 'user', text: '병원에 전화해서 진료 시간과 접수 시간을 알아봐 줘.' },
  { id: 'preview-assistant', role: 'assistant', text: '진료 시간과 접수 마감 시간을 함께 확인할게요. 통화하는 동안에도 여기서 말씀해 주세요.' },
]

export function createPreview(id: PreviewId): CallViewModel {
  const base: CallViewModel = {
    id: 'preview-call', subject: '예시 병원', purpose: '진료 시간과 접수 시간 확인',
    taskStatus: 'working', callStatus: 'connected', listening: false,
    summary: '안내를 듣고 있습니다. 아직 확인이 끝나지 않았어요.',
    transcript: [
      { id: 'a1', speaker: 'agent', text: '안녕하세요. 진료 시간과 접수 마감 시간을 확인하고 싶습니다.', final: true },
      { id: 'c1', speaker: 'counterpart', text: '평일 안내부터 말씀드리면…', final: false },
    ],
  }
  switch (id) {
    case 'dialing': return { ...base, callStatus: 'dialing', summary: '상대가 전화를 받기를 기다리고 있습니다.', transcript: [] }
    case 'needs_input': return { ...base, taskStatus: 'needs_input', summary: '진료과에 따라 시간이 다릅니다. 어느 진료과를 확인할까요?', transcript: [base.transcript[0], { id: 'c2', speaker: 'counterpart', text: '어느 진료과의 안내를 원하시나요?', final: true }] }
    case 'succeeded': return { ...base, taskStatus: 'succeeded', callStatus: 'ended', summary: '진료 시간과 접수 시간 항목을 모두 확인한 화면 예시입니다.', transcript: [{ id: 'a2', speaker: 'agent', text: '필요한 안내를 모두 확인했습니다. 감사합니다.', final: true }] }
    case 'incomplete': return { ...base, taskStatus: 'incomplete', callStatus: 'ended', summary: '통화는 끝났지만 접수 마감 시간을 확인하지 못했습니다.', transcript: [base.transcript[0]] }
    case 'failed': return { ...base, taskStatus: 'incomplete', callStatus: 'failed', summary: '연결되지 않아 진료 시간을 확인하지 못했습니다.', transcript: [] }
    default: return base
  }
}
