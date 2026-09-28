# 기존 user_proxy_agent 작업공간 교체

상태: complete
승인: 사용자가 `/Users/junsu/Project/proxy-agent-poc/user_proxy_agent`를 명시한 확인에
“네, 해당 폴더를 교체해 주세요”라고 답했다.

## 범위

- 원본은 현재 `agent-service`, 대상은 형제 `user_proxy_agent`다.
- 기존 대상 전체를 타임스탬프 백업으로 이동해 미커밋·무시 파일과 회사 Git 이력을 보존한다.
  PR #32 작업공간이 백업된 기존 저장소를 계속 가리키도록 Git 연결을 복구한다.
- 새 대상은 현재 서비스의 독립 Git 복제본으로 만들며 원격은 현재 GitHub 저장소로 맞춘다.
  회사 원격 저장소에는 push하거나 이력을 덮어쓰지 않는다.
- `.env`, 암호화 키, SQLite 일관된 스냅샷, 로컬 설정·평가 산출물을 비공개로 복사한다.
  프로세스 PID·소켓과 경로에 종속된 가상환경은 재사용하지 않는다. 새 위치에서 의존성을 설치한다.
- 실행 중인 공유 서버는 원본 위치에 유지한다. 복제 데이터로 두 번째 서비스는 실행하지 않는다.

## 검증·완료 기준

1. 백업 전후 기존 Git HEAD·미커밋 상태·파일 내용 보존 및 연결 worktree 정상 확인.
2. 원본/대상 코드 일치, SQLite integrity·암호화 키·로컬 설정·제외 규칙 확인.
3. 새 위치의 Python import·관련 자동 검사, 웹 테스트·빌드 확인.
4. 현재 서비스 GitHub에 기록을 반영하고 양쪽 작업공간 HEAD를 맞춘다.
5. 백업 위치와 복구 방법, 운영 서버와 복제 스냅샷의 차이를 기록한다.

## 결과

- 기존 폴더 전체를 형제 `backups/user_proxy_agent-before-sync-20260928-173333`으로 이동했다.
  이동 전후 파일 해시 및 Git HEAD·미커밋 상태 일치를 확인했다.
- 연결된 `worktrees/pr-32-conflicts`는 기존 HEAD·깨끗한 상태를 유지한다. Git worktree
  연결뿐 아니라 하위 `deep-agentic`의 gitdir·core.worktree·객체 alternates 경로도 복구했다.
- 새 대상은 GitHub `ChunShow/user-proxy-agent`를 origin으로 사용한다. 기존 회사 원격은
  백업에 보존했으며 변경하거나 push하지 않았다.
- `.env`·암호화 키 동일, SQLite 무결성 및 원본과 논리 내용 일치, Google 연결 토큰 복호화 확인.
  개인 데이터·설정은 모두 Git 제외 상태다. 활성 프로세스 PID는 복사하지 않았다.
- 새 경로에서 uv/npm 의존성을 새로 설치했다. `./scripts/check.sh`에서 backend 366개,
  web 59개, Ruff·타입·lint·빌드 통과. Python 모듈 ROOT가 새 작업공간을 가리킴을 확인했다.
- 기존 9010 백엔드 health 정상. 실행 중인 공유 서버와 원본 작업공간은 유지했다.

## 이후 작업과 복구

현재 개발 기준 폴더는 `user_proxy_agent`다. 실행 중인 공유 페이지는 아직
`agent-service`에서 제공한다. 새 폴더에서 수정·빌드해도 운영 페이지에 자동 반영되지는 않는다.
데이터는 복사 시점 스냅샷이며 양쪽 DB를 계속 동기화하는 구성은 아니다.
운영을 새 폴더로 옮길 때는 기존 서비스를 정상 종료하고 최신 데이터와 키를 다시 옮긴 뒤,
새 위치에서 `./scripts/dev.sh`를 실행하고 공유 Caddy의 정적 파일 경로를 맞춘다.
두 복제 DB를 동시에 운영하지 않는다.

기존 실험 작업으로 복구하려면 새 대상도 별도로 보관한 뒤 위 백업 폴더를 원래 이름으로
돌린다. 이어 Git worktree repair를 실행하고, PR 작업공간 하위 모듈의 `.git` 포인터,
core.worktree, 객체 alternates도 복구된 저장소 위치로 갱신한다. 가상환경의 경로는 원래
위치로 돌아가지만 의존성이 손상됐으면 해당 실험 저장소의 잠금 파일로 재설치한다.
이 절차는 복구 안내이며 이번 작업에서 실제 복구를 실행한 것은 아니다.

비공개 상세 기록은 양쪽 `var/workspace-sync/result.json`에 저장했다.
