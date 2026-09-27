# README 구조도

[architecture.html](architecture.html)이 편집 원본이다. HTML 안의 SVG 좌표·문구를 수정하고,
같은 원본에서 [SVG](architecture.svg)와 [PNG](architecture.png)를 내보낸다.
README에는 한글·줄바꿈이 환경마다 달라지지 않는 2배 해상도 PNG를 표시한다.

## 다시 내보내기

저장소 루트에서 실행한다. 기존 웹 개발 의존성(`cd web && npm ci`)과 Playwright 브라우저를
사용한다. macOS에 Chrome이 설치되어 있다면 추가 브라우저 설치 없이 아래 명령으로 실행한다.

```bash
PLAYWRIGHT_CHANNEL=chrome node docs/diagrams/export.mjs
```

Playwright 기본 Chromium을 설치한 환경에서는 `PLAYWRIGHT_CHANNEL`을 생략한다.
시스템 글꼴을 사용하므로 다른 OS에서 다시 내보내면 글자 폭이 조금 달라질 수 있다.
현재 PNG는 macOS의 Apple SD Gothic Neo로 렌더링했다. 외부 폰트·스크립트 요청은 없다.

## 표현과 생략

- 위에서 아래로 웹 → FastAPI 내부 실행 → 외부 공급자를 읽는다.
- DeepAgents 카드의 두 실행은 같은 프로세스의 단일 대화 세션이라는 뜻이 아니다.
  메인 채팅과 통화 업무 판단은 별도로 실행하며 공통 팩토리·모델 설정을 사용한다.
- 텍스트 모델 API는 DeepAgents 카드의 설명으로, 통화 상대·ARS는 ClawOps 카드의 목적지로
  합쳤다. 공급자 API가 로컬 서버에서 실행된다는 의미는 아니다.
- 원래 도식의 웹 → 통화 직접 제어, 웹 → 앱 실행 승인 연결은 웹 카드의 기능 설명과
  README 본문으로 옮겼다. 이 동작이 반드시 DeepAgents를 거친다는 의미는 아니다.
- SQLite와 Langfuse는 다른 역할이므로 분리했다. 개별 저장·추적 연결선은 생략했으며,
  Langfuse는 선택적인 실행 메타데이터 추적이고 대화 원문은 수집하지 않는다.
- 원본 Mermaid의 10개 노드를 7개 주요 카드와 2개 하단 항목으로 정리했다.
  텍스트 모델·통화 상대 2개 노드를 관련 카드에 합치고 저장·관측 노드 1개를 둘로 나눴다.

## 디자인과 확인

[diagram-design](https://github.com/cathrynlavery/diagram-design/tree/main/skills/diagram-design)의
architecture·Mermaid redraw 지침을 적용했다. README용 사용자 지정 960×960 캔버스,
직선 연결, 서로 다른 접점, 16px 이상 한글, 한 가지 강조색을 사용한다.
제품 `web/src/styles.css`의 중립색·초록색과 시스템 글꼴을 따른다.

수정 후 README 표시 너비에서도 한글 잘림·연결선 교차·카드 밖 넘침을 확인한다.
HTML에는 이미지 제목·설명과 접근성 속성을 포함하고, README 이미지에는 대체 텍스트를 둔다.
