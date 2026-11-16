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
