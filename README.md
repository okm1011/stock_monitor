# Stock Monitor

코인 · 한국 주식 · 미국 주식 시세를 주기적으로 받아 **RSI 조건**이 맞으면 알림을 보내는 상시 모니터입니다.
UI/그래프는 없고, PC에서 꺼지지 않게 계속 실행하는 용도입니다.

## 설치

```powershell
cd c:\stock_monitor
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

활성화가 안 되면:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app verify
```

## 데이터 소스

| 자산 | 실시간/폴링 | 과거 봉(백필) |
|------|-------------|---------------|
| 코인 | Binance ticker | Binance klines |
| 한국/미국 주식 | Yahoo Finance (`yfinance`) | Yahoo history |

주식 API는 rate limit이 있어 **최소 5초 캐시**로 호출합니다. 코인은 설정한 폴링 간격(기본 1초)마다 갱신합니다.

## 설정 (`config.yaml`)

```yaml
poll_interval_seconds: 1   # 폴링 간격
timeframe: 1m              # 1m|3m|5m|15m|30m|1h|4h|1d
rsi:
  period: 14
  min: 30                  # 이하(과매도) 알림
  max: 70                  # 이상(과매수) 알림
alert_cooldown_seconds: 300
history:
  max_candles: 300
  db_path: data/candles.db
```

봉 데이터는 SQLite(`data/candles.db`)에 쌓이므로 재시작 후에도 RSI용 히스토리가 유지됩니다.

## 실행 (UI)

설정을 바꾸고 Start 하는 화면:

```powershell
cd c:\stock_monitor
.\run.bat
```

또는 (가상환경 Python을 직접 지정):

```powershell
cd c:\stock_monitor
.\.venv\Scripts\python.exe -m app
```

> `python -m app` 만 치면 **시스템 Python**이 잡혀 `No module named 'rich'` 가 날 수 있습니다.
> 반드시 `.venv` 쪽 Python 또는 `run.bat` 을 쓰세요.

가상환경 활성화 후 쓰려면:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m app
```

UI에서 조절 가능:
- 폴링 간격, 봉 단위(timeframe)
- RSI period / min / max
- 알림 쿨다운, 상태 로그 주기, 히스토리 봉 개수
- **설정 저장** → `config.yaml` 반영
- **Start / Stop**
- **텔레그램 테스트**

심볼(코인/주식 목록)은 아직 UI에 없고 `config.yaml`에서 수정합니다.

CLI만 쓰려면:

```powershell
python -m app run
```

## 동작 요약

1. 시작 시 과거 봉 백필 → DB 저장  
2. `poll_interval_seconds`마다 최신가 조회  
3. `timeframe` 기준으로 봉 집계/저장  
4. RSI 계산 후 min 이하 / max 이상이면 알림  
5. 같은 심볼·같은 방향은 `alert_cooldown_seconds` 동안 재알림 억제  
6. `.env`에 텔레그램 정보가 있으면 콘솔 + 텔레그램으로 전송  

## 텔레그램 알림 (.env 설정)

### 1) 봇 만들기 (토큰)

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색 후 대화  
2. `/newbot` 입력 → 봇 이름 / username 설정  
3. BotFather가 주는 **HTTP API token** 복사  
   - 예: `7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

### 2) 봇에게 먼저 말 걸기

1. BotFather가 준 봇 링크로 입장 (또는 검색)  
2. `/start` 전송 (이걸 안 하면 chat id를 못 받습니다)

### 3) Chat ID 확인

PowerShell에서 토큰을 바꿔 실행:

```powershell
cd c:\stock_monitor

# 아래 YOUR_BOT_TOKEN 을 BotFather 토큰으로 교체
$token = "YOUR_BOT_TOKEN"
Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates" | ConvertTo-Json -Depth 10
```

출력 JSON에서 대략 이런 부분을 찾습니다:

```text
"chat": { "id": 123456789, ... }
```

그 `id` 숫자가 `TELEGRAM_CHAT_ID` 입니다.  
(그룹이면 `-100...` 처럼 음수일 수 있습니다.)

`result`가 비어 있으면 봇에게 `/start`를 다시 보낸 뒤 위 명령을 재실행하세요.

### 4) `.env` 파일 만들기

프로젝트 루트에서 예시 파일을 복사합니다:

```powershell
cd c:\stock_monitor
Copy-Item .env.example .env
```

메모장으로 열기:

```powershell
notepad .env
```

아래처럼 **값만** 채웁니다 (따옴표 없이, 공백 없이):

```env
TELEGRAM_BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

저장 후 메모장을 닫습니다.

한 줄로 바로 쓰고 싶다면 (토큰/ID를 본인 값으로 교체):

```powershell
cd c:\stock_monitor
@"
TELEGRAM_BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
"@ | Set-Content -Path .env -Encoding utf8
```

> `.env`는 비밀정보입니다. Git에 올리지 마세요. (이미 `.gitignore`에 포함됨)

### 5) 연동 테스트

가상환경 사용 중이면:

```powershell
cd c:\stock_monitor
.\.venv\Scripts\Activate.ps1
python -m app telegram-test
```

활성화가 안 되면:

```powershell
cd c:\stock_monitor
.\.venv\Scripts\python.exe -m app telegram-test
```

성공 시 텔레그램으로 `stock-monitor 텔레그램 연동 테스트 OK` 메시지가 옵니다.  
콘솔에는 `Telegram 테스트 메시지 전송 성공`이 뜹니다.

### 6) 모니터 실행

```powershell
python -m app run
```

시작 로그에 `Telegram 알림: ON`이 보이면 연동된 상태입니다.  
RSI가 `min` 이하 또는 `max` 이상이면 콘솔 + 텔레그램으로 알림이 갑니다.

`.env`가 없거나 값이 비어 있으면 `Telegram 알림: OFF`이고 콘솔만 사용합니다.

## 다음 단계 (예정)

- 심볼별 RSI 임계값 / 다중 조건
