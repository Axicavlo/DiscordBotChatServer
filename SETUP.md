# 서버봇 설정 가이드

새 Windows PC에서 서버봇 프로젝트를 내려받아 VS Code로 실행하는 절차입니다. Git, Python, VS Code가 설치되어 있어야 합니다. Windows에서는 Python 3.14.7로 확인했습니다.

## 1. 프로젝트 내려받기

PowerShell에서 프로젝트를 둘 상위 폴더로 이동한 뒤 실행합니다.

```powershell
git clone https://github.com/Axicavlo/DiscordBotChatServer.git 서버봇
Set-Location -LiteralPath .\서버봇
code .
```

`code` 명령을 사용할 수 없으면 VS Code의 **파일 → 폴더 열기**에서 `서버봇`을 선택합니다.

## 2. Python 환경 준비

VS Code에서 PowerShell 터미널을 열고 프로젝트 루트에서 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

VS Code에서 Microsoft **Python**과 **Python Debugger** 확장을 설치합니다. 인터프리터 선택이 필요하면 `Ctrl+Shift+P`에서 **Python: Select Interpreter**를 실행하고 프로젝트의 `.venv\Scripts\python.exe`를 선택합니다.

## 3. 로컬 토큰 설정

`bot_server.py` 옆에 `.env`를 만들고 아래 한 줄을 넣습니다. 기존 `.env`가 있으면 그 파일을 편집합니다.

```dotenv
DISCORD_BOT_TOKEN=
```

등호 뒤에는 Discord Developer Portal의 **Bot**에서 관리하는 봇 토큰을 입력합니다. `.env`는 Git에서 제외되므로 PC마다 직접 설정합니다. 파일명이 `.env.txt`로 저장되지 않았는지 확인하세요.

## 4. 실행 확인

VS Code의 **실행 및 디버그**에서 **Run Server Bot**을 선택한 뒤 **F5**를 누릅니다. 이 구성이 프로젝트의 `.env`를 읽습니다. 터미널에 `Discord connection ready`가 표시되면 연결된 것입니다.

| 증상 | 확인할 부분 |
| --- | --- |
| `Set DISCORD_BOT_TOKEN before starting the bot` | 프로젝트 루트의 `.env` 파일명과 토큰 입력 여부를 확인합니다. |
| 잘못된 Python 인터프리터 | `.venv` 생성 여부를 확인하고 `.venv\Scripts\python.exe`를 선택합니다. |

## OCI에서 systemd로 실행할 때

서비스의 토큰 파일은 `/etc/discord-bot-chat.env`입니다. 위와 같은 `DISCORD_BOT_TOKEN=값` 형식으로 서버에서 설정하고, 관리자만 읽을 수 있도록 파일 권한을 설정합니다. 서비스 설정과 Ubuntu 실행 조건은 [README](README.md)와 [systemd 서비스 파일](deploy/discord-bot.service)에서 확인할 수 있습니다.


## Windows 조회 API 연결

봇과 함께 읽기 전용 API를 실행하려면 서버의 `/etc/discord-bot-chat.env`에 `CHATBOT_API_KEY=값`을 추가하고 서비스를 재시작합니다. 값은 Discord 봇 토큰과 별도로 생성한 무작위 키를 사용합니다. Windows 앱에는 이 키를 입력합니다.

API는 서버의 `127.0.0.1:8765`에서만 대기합니다. Windows PowerShell에서 평소 쓰는 SSH 키와 OCI 공인 IP를 넣어 로컬 포트 포워딩을 실행합니다.

```powershell
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 -i "<SSH 키 경로>" ubuntu@<OCI 공인 IP>
```

SSH 창을 열어둔 채 Windows 앱에서 서버 주소를 `http://127.0.0.1:8765`로 설정합니다. 앱 접속 키를 입력하고 서버 및 채널을 선택하면 최근 메시지 최대 100개를 확인할 수 있습니다.

## Windows 앱 로그인 (PostgreSQL)

OCI의 `/etc/discord-bot-chat.env`에 아래 항목을 설정합니다. `DB_PASSWORD`는 PostgreSQL의 `discordbot_app` 계정 비밀번호입니다.

```dotenv
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=discordbot
DB_USER=discordbot_app
DB_PASSWORD=
```

DB에는 `bot_auth.app_users`와 `bot_auth.app_sessions`가 있어야 합니다. 세션 토큰의 SHA-256 해시는 32바이트 `bytea`로 저장합니다.

새 의존성을 설치하고 운영 서버에서 서비스 환경을 사용해 앱 계정을 만듭니다.

```bash
cd /opt/discord-bot-chat
.venv/bin/python -m pip install -r requirements.txt
sudo systemd-run --wait --pty --collect --uid=ubuntu \
  --property=EnvironmentFile=/etc/discord-bot-chat.env \
  /opt/discord-bot-chat/.venv/bin/python /opt/discord-bot-chat/manage_users.py create --username myname
sudo systemctl restart discord-bot.service
```

계정 관리 명령은 앱 비밀번호를 두 번 입력받습니다. 비밀번호를 초기화하려면 `create`를 `reset-password`로 바꿉니다. 초기화하면 해당 사용자의 모든 세션이 폐기됩니다.

Windows 로그인은 OCI의 공인 HTTPS 주소를 사용합니다. 계정 로그인과 채널 조회를 확인한 후 필요하면 기존 `CHATBOT_API_KEY` 환경 변수를 제거하고 서비스를 재시작합니다.


## 앱 세션과 점검 모드 관리

이 버전을 배포하기 전에 PostgreSQL의 `discordbot`에 [db/001_app_control.sql](db/001_app_control.sql)을 `discordbot_app`으로 적용합니다.

OCI에 SSH로 접속해 아래처럼 명령 목록을 확인합니다.

```bash
cd /opt/discord-bot-chat
.venv/bin/python manage_users.py --help
.venv/bin/python manage_users.py maintenance --help
```

DB 접속 환경을 읽어 관리자 명령을 실행하려면 다음을 사용합니다. 예시는 현재 점검 상태를 조회합니다.

```bash
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  --property=EnvironmentFile=/etc/discord-bot-chat.env \
  /opt/discord-bot-chat/.venv/bin/python \
  /opt/discord-bot-chat/manage_users.py maintenance status
```

| 마지막 부분 | 동작 |
| --- | --- |
| `revoke-all-sessions` | 모든 세션을 폐기합니다. 점검 모드 설정은 유지합니다. |
| `maintenance on` | 앱 접속을 차단하고 같은 전체 세션 폐기 로직을 한 트랜잭션에서 실행합니다. |
| `maintenance off` | 앱 접속을 다시 허용합니다. 새로 로그인해야 합니다. |
| `maintenance status` | 현재 점검 상태를 확인합니다. |

점검 중에는 앱 로그인·채팅 API·WebSocket이 차단됩니다. Discord 슬래시 명령은 계속 사용할 수 있습니다.

## 앱 로그인·로그아웃 이력

이 버전을 배포하기 전에 `discordbot`에 [db/002_app_session_log.sql](db/002_app_session_log.sql)을 적용합니다. `bot_log` 스키마가 이미 있어야 합니다.

`bot_log.app_session_log`에는 로그인 성공, 직접 로그아웃, 관리자의 활성 세션 폐기를 기록합니다. 같은 세션의 이벤트는 내부 숫자 `session_id`로 연결할 수 있습니다. 관리자 폐기에는 사유를, 로그인과 로그아웃에는 접속 IP를 기록합니다. 비밀번호와 인증 토큰은 이 테이블에 저장하지 않습니다.

앱 종료·세션 자동 복원은 기존 세션을 유지하고, 유휴 만료는 로그아웃 요청을 발생시키지 않습니다.

## 계정 ID

PostgreSQL은 Asia/Seoul 기준으로 계정에 YYYYMMNNN 형식의 9자리 ID를 발급합니다.
월별 순번은 001~999이며, 계정 생성 명령이 ID를 표시합니다. 기존 사용자 이름으로 로그인합니다.

마이그레이션 003을 적용한 DB는 [db/004_unify_app_user_id.sql](db/004_unify_app_user_id.sql)을 한 번 적용합니다.
봇 서비스를 중지하고 계정 관리 명령 사용을 멈춘 뒤, DB 소유 역할 또는 관리자로 스크립트 전체를 실행합니다.
이 SQL은 계정·세션·API 요청 로그·세션 로그의 ID를 함께 변경하고 user_code 컬럼을 제거합니다.
계정과 연결할 수 없는 로그가 있으면 자동으로 중단합니다.
SQL 적용을 확인한 후 새 서버 버전을 배포하고 봇 서비스를 시작합니다.


## 웹 오목 활성화

- 서비스 환경에 `GOMOKU_PUBLIC_URL=https://<웹 주소>`를 설정한다. 브라우저가 실제 사용하는 HTTPS 주소와 포트를 적으며 경로를 붙이지 않는다. 이 값이 비어 있으면 오목 명령과 웹 경로는 등록되지 않는다.
- HTTPS 프록시는 `/gomoku/` 아래 HTTP 요청과 WebSocket 업그레이드를 기존 서버봇의 `127.0.0.1:8765`로 전달한다. 브라우저의 Origin 헤더를 유지하고, 외부 이용자의 PC와 모바일에서 이 HTTPS 주소에 접속할 수 있어야 한다.
- 서버봇 PostgreSQL 환경 설정을 사용한다. `db/005_gomoku.sql` 반영 여부와 서비스 DB 역할의 `gomoku` 스키마 USAGE, `gomoku.record` SELECT·INSERT 권한을 확인한다.
- 서비스를 시작한 뒤 테스트 서버에서 `/오목 방만들기`를 실행한다. 두 계정으로 참가와 재입장, 관전자 착수 제한, 대국 종료 후 결과 저장과 모드별 전적을 확인한다.

## Discord Activity 오목

1. 기존 서버봇과 같은 Discord Developer Portal 애플리케이션에서 Activities를 활성화한다.
2. URL Mappings에 두 항목을 설정한다. 대상은 기존 오목의 공개 HTTPS 호스트이며 Portal 형식에 맞춰 스킴은 생략한다.
   - `/` → `<오목 HTTPS 호스트>/gomoku/activity`
   - `/gomoku` → `<오목 HTTPS 호스트>/gomoku`
3. 서버 환경 변수에 같은 애플리케이션의 `GOMOKU_ACTIVITY_CLIENT_ID`와 `GOMOKU_ACTIVITY_CLIENT_SECRET`을 설정한다. 두 변수를 비워 두면 기존 웹 입장 방식으로 동작한다. 비밀 값은 클라이언트나 Git에 넣지 않는다.
4. 같은 서버에서 두 계정으로 방 카드의 참가를 누르고 착수·채팅을 확인한다. 별도 계정으로 관전을 확인한다. App Launcher로 직접 실행하면 방 카드에서 입장해야 한다는 안내가 표시된다.

번들을 다시 만들 때는 임시 디렉터리에 `gomoku_assets/activity.js`를 복사한 뒤 아래를 실행하고, 출력된 `activity.bundle.js`를 `gomoku_assets`에 복사한다.

```sh
npm install --save-exact @discord/embedded-app-sdk@2.5.0 esbuild@0.28.2
npx esbuild activity.js --bundle --format=esm --minify --legal-comments=inline --outfile=activity.bundle.js
```
