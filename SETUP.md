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

## User codes

Apply [db/003_app_user_codes.sql](db/003_app_user_codes.sql) to the discordbot database
as the owner of bot_auth.app_users or as a database administrator before deploying
the new account creation CLI. Apply the migration once.

PostgreSQL assigns each account a nine-digit user code (YYYYMMNNN). The month is
based on Asia/Seoul, and the suffix runs from 001 to 999. Existing accounts are
numbered by creation time and internal ID within each month. Internal account IDs
and usernames stay the same. The create command prints the assigned code.
