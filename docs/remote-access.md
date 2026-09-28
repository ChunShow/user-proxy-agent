# 개인용 공유 페이지 로그인

HTTP Basic 인증 팝업을 지원하지 않는 앱 내 브라우저를 위해 일반 HTML 비밀번호 폼과
같은 사이트의 쿠키를 사용한다. 일반 쿠키 허용이 필요하며 Google OAuth의 앱 내 브라우저
지원 여부는 별개다.

## 실행 구성

Cloudflare → loopback Caddy(5188) → 인증 서비스(5189) / 제품 API(9010) / `web/dist`.
인증 서비스는 제품 서버와 별도 프로세스이며 기본 개발 실행에 포함되지 않는다.

Git 제외 비공개 JSON 파일에 다음 필드를 설정하고 `chmod 600`으로 권한을 제한한다.

- `origin`: 공유 HTTPS origin. 경로와 마지막 `/`는 제외한다.
- `password`: 공유 비밀번호(최소 5자).
- `signing_key`: 임의 32바이트 이상의 키를 hex로 표현한 값.

`backend`에서 실행한다.

```sh
REMOTE_ACCESS_CONFIG_FILE=/absolute/private/config.json .venv/bin/python -m uvicorn agent_service.remote_access:create_app --factory --host 127.0.0.1 --port 5189 --no-access-log
```

Caddy에서 `/_access/login`, `/_access/logout`은 인증 서비스로 전달하고, 그 뒤 모든
정적 파일·API·WebSocket 요청에 아래 검사를 적용한다.

```caddyfile
forward_auth 127.0.0.1:5189 {
    uri /_access/verify
    header_up -Connection
    header_up -Upgrade
}
```

`Connection`과 `Upgrade`는 **인증 하위 요청에서만** 제거한다. 인증 서버는 HTTP GET으로
쿠키를 확인한다. 이 헤더를 그대로 넘기면 인증 요청까지 WebSocket 업그레이드가 되어
403으로 거절되며, 실제·가상 통화 모두 공유 페이지에서 소리를 들을 수 없다.
제품 API로 전달하는 원래 요청에는 WebSocket 업그레이드를 유지한다.

전체 설정 템플릿은 [`config/Caddyfile`](../config/Caddyfile)이다. 프로젝트 루트에서
`UPA_PUBLIC_ORIGIN`을 공유 HTTPS origin으로, `UPA_WEB_ROOT`를 빌드한 `web/dist`의
절대 경로로 지정한 뒤 실행한다. 인증 서비스의 비공개 JSON `origin`과 일치해야 한다.

```sh
UPA_PUBLIC_ORIGIN=https://your-tunnel.trycloudflare.com UPA_WEB_ROOT="$PWD/web/dist" caddy run --config config/Caddyfile --adapter caddyfile
```

다른 Origin은 앞단에서 거부한다. 제품 API의 기존 Host/Origin 프록시 설정은 유지한다.
게이트웨이·API·인증 포트는 loopback에만 바인딩하고 HTTPS는 터널에서 종료한다.
현재 기기의 설정·PID·로그는 Git 제외 `var/remote-access/`에 있다.
터널 주소가 바뀌면 비공개 설정과 Caddy의 허용 Origin도 갱신한다.

## 동작과 제한

- 익명 페이지는 로그인 폼으로 이동하며 HTTP 인증 팝업을 요구하지 않는다.
- 로그인 후 홈으로 이동한다. 미인증 API 요청은 401을 반환한다.
- 쿠키는 Secure·HttpOnly·SameSite=Lax이며 12시간 만료된다.
- 로그인 시도는 서비스 전체에서 분당 10회로 제한된다.
- 같은 Origin에서 `POST /_access/logout`을 호출하면 접근 쿠키가 지워진다.
  모든 기존 쿠키를 무효화하려면 서명 키를 교체하고 인증 서비스를 재시작한다.
- 공유 비밀번호는 제품 계정이 아니다. 브라우저별 대화·Google 연결은 합쳐지지 않는다.
- 자격증명·개인 설정·로그는 저장소에 올리지 않는다.

검증: `cd backend && uv run pytest tests/test_shared_listening.py tests/test_remote_access.py tests/test_session.py tests/calls/test_listening.py -q`.
공유 연결 회귀 검사는 설치된 Caddy로 인증 전 거부·다른 Origin 거부·인증 후 양쪽 음성
트랙 전달을 확인한다. Caddy가 없으면 해당 검사만 건너뛴다.
