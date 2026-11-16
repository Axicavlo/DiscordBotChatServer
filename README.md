# 서버봇

Discord 봇의 독립 실행 프로젝트입니다. 숫자야구와 주작기 토너먼트 명령을 처리합니다.

## Ubuntu에서 실행

Python 3.12와 venv를 사용합니다. 대진표의 한글 렌더링에는 Ubuntu의 `fonts-noto-cjk` 패키지를 설치합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
read -rsp 'Discord bot token: ' DISCORD_BOT_TOKEN
printf '\n'
DISCORD_BOT_TOKEN="$DISCORD_BOT_TOKEN" .venv/bin/python -m bot_server
unset DISCORD_BOT_TOKEN
```

직접 실행할 때 토너먼트 데이터는 기본적으로 `~/.discord-bot-server`에 저장되며 `STATE_DIRECTORY`로 경로를 지정할 수 있습니다.

## systemd

`deploy/discord-bot.service`는 프로젝트와 가상환경이 `/opt/discord-bot-chat`에 있다고 가정합니다. 관리자 전용 `/etc/discord-bot-chat.env`에 `DISCORD_BOT_TOKEN`을 설정하면 서비스에서 읽습니다. 서비스 실행 시 토너먼트 데이터 디렉터리는 systemd가 관리합니다.

Windows와 Android 앱은 기존 프로젝트에서 관리합니다. 클라이언트 연동용 HTTPS API와 WebSocket은 후속 구현 범위입니다.
