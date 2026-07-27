# Stock Monitor

코인 · 한국 주식 · 미국 주식 시세를 주기적으로 받아 **봉 마감 기준 규칙(RSI/MACD/BB/다이버전스 등)** 이 맞으면 텔레그램으로 알림을 보내는 상시 모니터입니다.

- PC: UI로 설정 후 실행 가능
- 서버(AWS EC2): CLI(`python -m app run`) + systemd로 24시간 실행

## 설치 (Windows PC)

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

> `python -m app` 만 치면 시스템 Python이 잡혀 `No module named 'rich'` 가 날 수 있습니다.  
> 반드시 `.\.venv\Scripts\python.exe -m app` 를 쓰세요.

## 데이터 소스

| 자산 | 실시간/폴링 | 과거 봉(백필) |
|------|-------------|---------------|
| 코인 | Binance | Binance klines |
| 한국/미국 주식 | Yahoo Finance (`yfinance`) | Yahoo history |

주식은 rate limit이 있어 캐시 간격(최소 약 5초)으로 호출합니다.

## 설정 (`config.yaml`)

주요 항목:

```yaml
poll_interval_seconds: 5
timeframe: 1h
signal_on_closed_bar: true   # 봉 마감 기준 알람

rsi:
  period: 7
macd:
  fast: 12
  slow: 26
  signal: 9
bollinger:
  period: 20
  stddev: 2.0
atr:
  period: 14
  sl_mult: 1.5
  tp_mult: 3.0

rules:
  extreme_rsi: { enabled: true, high: 80, low: 23 }
  rsi_macd_cross: { enabled: true, oversold: 30, overbought: 70 }
  divergence: { enabled: true, ... }
  bb_squeeze: { enabled: true, squeeze_ratio: 0.05 }

alert_cooldown_seconds: 300
```

심볼 목록도 `config.yaml`의 `crypto` / `kr_stocks` / `us_stocks` 에서 관리합니다.

## 실행 (PC UI)

```powershell
cd c:\stock_monitor
.\.venv\Scripts\python.exe -m app
```

CLI만:

```powershell
.\.venv\Scripts\python.exe -m app run
```

## 텔레그램 (.env)

1. [@BotFather](https://t.me/BotFather)에서 `/newbot` → **토큰** 복사  
2. 만든 봇에게 `/start`  
3. 본인 계정 **Chat ID** 확인 (`@userinfobot` 등)  
4. 프로젝트 루트에 `.env` 생성:

```powershell
cd c:\stock_monitor
Copy-Item .env.example .env
notepad .env
```

```env
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=본인채팅ID
```

> Chat ID는 **봇 ID가 아니라 알림 받을 내 계정(또는 채팅방) ID**입니다.  
> `.env`는 Git에 올리지 마세요.

테스트:

```powershell
.\.venv\Scripts\python.exe -m app telegram-test
```

---

## AWS EC2에 올려 24시간 돌리기 (초보용 전체 순서)

PC를 끄지 않고 서버에서 계속 실행하는 방법입니다.  
서버에서는 **UI를 쓰지 않고** `python -m app run` + systemd 를 사용합니다.

나중에 모바일에서 `config`를 바꾸는 API/소켓을 붙일 예정이면,  
보안 그룹에 HTTP/HTTPS를 열어둘 수 있지만 **앱 포트(8000 등)는 인터넷에 직접 열지 마세요.**

### A. AWS에서 인스턴스 만들기

1. [AWS](https://aws.amazon.com) 가입 (카드 등록 필요, 무료 한도 초과 시 과금)
2. 우측 상단 리전: **아시아 태평양(서울) `ap-northeast-2`**
3. **EC2** → **키 페어** → 생성  
   - 이름 예: `stock_monitor_key`  
   - 형식: `.pem`  
   - **다운로드한 `.pem` 파일을 안전한 곳에 보관** (분실 시 접속 어려움)
4. **인스턴스 시작**
   - AMI: **Ubuntu Server 24.04 LTS** (추천)
   - 유형: **t3.micro** 또는 **t2.micro** (무료 티어)
   - 키 페어: 방금 만든 키 선택
   - 네트워크:
     - 퍼블릭 IP: **활성화**
     - 보안 그룹:
       - **SSH(22)**: 가능하면 **내 IP** (전 세계 `0.0.0.0/0` 비추천)
       - **HTTP(80) / HTTPS(443)**: 나중에 모바일 설정용. 지금 없어도 모니터 실행에는 불필요
   - 스토리지: 기본(8GB 전후)면 충분
   - **고급 세부정보**: 지금은 건드리지 않음
5. 인스턴스 상태가 **실행 중(Running)** 이 되면  
   **퍼블릭 IPv4 주소**를 복사 (예: `43.201.95.85`)  
   → 이 숫자가 SSH 명령의 `ubuntu@여기` 에 들어갑니다.

### B. Windows에서 SSH 접속

1. `.pem` 파일이 있는 폴더로 이동 (예: `C:\`)
2. 키 파일 권한 정리 (파일명에 `_` / `-` 가 섞이지 않게 **실제 이름** 확인):

```powershell
cd C:\
icacls .\stock_monitor_key.pem /inheritance:r
icacls .\stock_monitor_key.pem /grant:r "$($env:USERNAME):(R)"
```

3. SSH 접속 (IP·키 파일명은 본인 것으로):

```powershell
ssh -i .\stock_monitor_key.pem ubuntu@43.201.95.85
```

- Ubuntu AMI → 사용자 `ubuntu`
- Amazon Linux → 사용자 `ec2-user`
- 처음 접속 시 fingerprint 물어보면 `yes`

#### SSH 자주 나는 오류

| 증상 | 원인 | 해결 |
|------|------|------|
| `Identity file ... not accessible` | `-i` 뒤 키 파일명/경로 틀림 | 실제 `.pem` 이름 확인 (`stock_monitor_key.pem` 등) |
| `Permission denied (publickey)` | 키 없이 접속했거나 사용자명 틀림 | 올바른 `-i` + `ubuntu`/`ec2-user` |
| `Connection timed out` | 보안 그룹 SSH 미허용 / IP 차단 | 보안 그룹 22번, 소스=내 IP |

접속 성공 시 프롬프트 예: `ubuntu@ip-...:~$`

### C. 서버 패키지 설치

SSH 들어간 뒤:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

(나중에 모바일 웹 설정용으로 `nginx` 미리 설치해도 됨)

```bash
sudo apt install -y nginx
```

### D. 프로그램 올리기

#### 방법 1) PC에서 폴더 복사 (`scp`)

**새 PowerShell 창**에서 (SSH 창이 아님):

```powershell
scp -i C:\stock_monitor_key.pem -r C:\stock_monitor ubuntu@43.201.95.85:~/
```

서버에서:

```bash
cd ~/stock_monitor
rm -rf .venv
```

> Windows에서 만든 `.venv`는 서버에서 쓰지 말고 지운 뒤 새로 만듭니다.

#### 방법 2) GitHub clone

```bash
cd ~
git clone 본인저장소주소 stock_monitor
cd stock_monitor
```

> `.env`는 저장소에 넣지 마세요. 서버에서 따로 만듭니다.

### E. 가상환경 + 의존성 + 텔레그램

```bash
cd ~/stock_monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

nano .env
```

```env
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=채팅ID
```

저장: `Ctrl+O` → Enter → `Ctrl+X`

테스트:

```bash
python -m app telegram-test
python -m app run
```

로그에 `Telegram 알림: ON`이 보이면 OK. 확인 후 `Ctrl+C`로 종료.

### F. 꺼지지 않게 systemd 등록 (필수)

SSH를 끊거나 재부팅해도 다시 실행되게 합니다.

```bash
sudo nano /etc/systemd/system/stock-monitor.service
```

아래 내용 붙여넣기 (경로는 Ubuntu 기본 home 기준):

```ini
[Unit]
Description=Stock Monitor
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock_monitor
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/stock_monitor/.venv/bin/python -m app run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

적용:

```bash
sudo systemctl daemon-reload
sudo systemctl enable stock-monitor
sudo systemctl start stock-monitor
sudo systemctl status stock-monitor
```

`active (running)` 이면 성공.

로그:

```bash
journalctl -u stock-monitor -f
```

재시작 / 중지:

```bash
sudo systemctl restart stock-monitor
sudo systemctl stop stock-monitor
```

### G. 코드만 바꿨을 때 (재배포)

**처음 EC2 세팅(A~F)은 다시 할 필요 없습니다.**  
인스턴스, 키 페어, 보안 그룹, `apt install`, systemd 서비스 파일 등록은 그대로 둡니다.

| 변경 내용 | 서버에서 할 일 |
|-----------|----------------|
| Python 코드만 | D(코드 올리기) → `restart` |
| `requirements.txt` 변경 | D → `pip install -r requirements.txt` → `restart` |
| `config.yaml`만 | 서버에서 `nano config.yaml` 또는 PC에서 `scp`로 덮어쓰기 → `restart` |
| `.env` (텔레그램) | 서버에서 `nano .env` → `restart` |
| systemd 경로/명령 변경 | F 서비스 파일 수정 → `daemon-reload` → `restart` |

#### 1) SSH 접속 (B와 동일)

```powershell
ssh -i C:\stock_monitor_key.pem ubuntu@43.201.95.85
```

#### 2) 새 코드 올리기 (D)

**GitHub 사용 시 (서버):**

```bash
cd ~/stock_monitor
git pull
```

**PC에서 통째로 복사 시 (PC PowerShell, 새 창):**

```powershell
scp -i C:\stock_monitor_key.pem -r C:\stock_monitor ubuntu@43.201.95.85:~/
```

서버에 `.venv`가 Windows에서 복사된 적 있으면 지우고 서버에서만 유지:

```bash
cd ~/stock_monitor
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Git으로 관리하면 `scp` 대신 `git pull`만 쓰는 편이 깔끔합니다.  
> `.env`는 저장소에 없으므로 `scp`로 프로젝트 전체를 덮어쓰면 **서버 `.env`가 지워질 수 있습니다.**  
> 전체 `scp` 전에 서버 `.env` 백업하거나, 코드만 `git pull` 하세요.

#### 3) 의존성 (바뀐 경우만)

```bash
cd ~/stock_monitor
source .venv/bin/activate
pip install -r requirements.txt
```

#### 4) 모니터 재시작

```bash
sudo systemctl restart stock-monitor
sudo systemctl status stock-monitor
```

로그 확인:

```bash
journalctl -u stock-monitor -f
```

#### 한 줄 요약

```text
SSH → (git pull 또는 scp) → [pip install] → sudo systemctl restart stock-monitor
```

### H. 운영 체크리스트

- [ ] EC2 인스턴스 **Running**
- [ ] `systemctl status stock-monitor` → active
- [ ] `telegram-test` 또는 실제 알람이 텔레그램으로 옴
- [ ] SSH를 끊어도 알람이 계속 옴
- [ ] AWS Billing/예산 알림 설정 권장 (무료 한도 초과 방지)

### I. 나중에 모바일로 config 바꿀 때 (예정 구조)

지금은 모니터만 돌리면 됩니다. 나중에 붙일 때:

1. 설정 API는 **`127.0.0.1`만** listen (예: 8000)
2. **nginx**가 443 → 내부 API 프록시
3. **HTTPS + 로그인/토큰** 필수
4. 저장 시 `config.yaml` 수정 후 `systemctl restart stock-monitor`
5. 보안 그룹에 **443** 개방 (앱 포트 직접 공개 금지)

도메인 없으면 Cloudflare Tunnel 등도 대안입니다.

---

## 다음 단계 (예정)

- 모바일용 설정 API (토큰 인증)
- 심볼별 규칙 / UI에서 심볼 편집
