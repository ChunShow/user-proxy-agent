# 전화 없는 한국어 음성 평가

`baseline.txt`는 2026-09-27 재질문 제보 당시의 통화 프롬프트다. `candidate.txt`는 비교용 수정안이다.
입력 문장은 `scripts/live_dialogue_cases.py`에 있으며 모두 합성 시나리오다.

프로젝트 루트에서 Python 3.12/macOS로 실행한다. 기존 로컬 `.env`의 Live/메인 모델 설정을 사용한다.

```sh
uv run --project backend python scripts/check_live_dialogue.py --prepare
uv run --project backend python scripts/check_live_dialogue.py --run --label baseline --prompt scripts/fixtures/live-dialogue/baseline.txt --repeat 2
uv run --project backend python scripts/check_live_dialogue.py --run --label candidate --prompt scripts/fixtures/live-dialogue/candidate.txt --repeat 2
```

`--prepare`는 로컬 Yuna 음성만 생성한다. `--run`은 실제 Live와 메인 모델 API 비용이 발생한다.
실행 label은 매번 새 이름이어야 하며 결과를 덮어쓰지 않는다. `--cases activity_reason`처럼
특정 사례만 선택할 수 있다. 각 세션 최대 70초, 동시 실행 최대 2개, 반복 최대 3회다.
기본 실행은 한 프롬프트에 5개 사례 × 2회 = 10세션이다.

실제 프로덕션 Live 세션/음성 중계/끼어들기/위임/종료 로직을 사용하지만, 전화망과 재생 ACK는
모사한다. 평가 전용 SQLite만 만들며 전화 발신, Gmail, Calendar 클라이언트는 생성하지 않는다.

`var/live-dialogue/<label>/<case>-<repeat>/`에 다음이 저장된다.

- `conversation.wav`: 입력과 모사 전화 재생을 실제 수신 시각으로 합친 음성
- `input.wav`, `model.wav`, `playback.wav`: 합성 입력, 모델 원출력, 모사 재생
- `instructions.txt`: 실제 사용 프롬프트
- `result.json`: API 이벤트/전사/발화 구간/백엔드 입력과 결과/종료 근거
- `evaluation.sqlite3`: 평가만의 데이터베이스

`summary.json`은 전체 결과를 묶는다. 입력 완료/입력 전사/각 턴 응답/전송 오류는 자동 검증하지만,
내용의 적절성과 음성 자연스러움은 자동 합격으로 판정하지 않는다. 세션이 일찍 끝나 다음
입력이 없으면 해당 다중 발화 사례는 미완료다. `forced_after_timeout`은 응답을 기다리다 강제로
다음 입력을 시작한 경우다. 각 사례의 `review` 기준으로 전체 발화를 검토하고, API 오류/누락과
내용 실패를 구분한다. 단일 음절인 입력 전사도 인식 품질이 충분하다는 뜻은 아니다.

최종 인사와 모사 ACK가 끝나도 실제 통신사 종료나 사용자 청취를 확인한 것이 아니다.
소수 합성 문장 결과를 일반적인 대화 품질의 성공률로 해석하지 않는다.

추가로 종료 직후 후속 질문을 검사하려면 `--cases farewell_followup`을 사용한다.
이 사례는 backend 종료 승인을 실제로 받은 뒤 0.8초 후 합성 질문을 넣는다.
기본 5개 사례에는 포함되지 않는다.

저장된 결과만으로 듣기 페이지를 만들 수 있다. 이 명령은 API를 호출하지 않는다.

```sh
uv run --project backend python scripts/report_live_dialogue.py baseline baseline-repair candidate candidate-v2 bridge-fixed
python3 -m http.server 9123 --bind 127.0.0.1 --directory var/live-dialogue/listen
```

`listen`에는 생성한 HTML과 합성 대화 WAV만 복사한다. SQLite/원본 이벤트는 공개하지 않는다.
2026-09-27 비교의 candidate/candidate-v2는 결과가 충분하지 않아 배포에 사용하지 않았다.
