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
| 코인 (기본) | Binance 현물 USDT | Binance klines |
| 주식/ETF (설정 시) | Binance USDⓈ-M 선물 (`binance_futures`) | Binance Futures klines |
| 그 외 주식 (옵션) | Yahoo Finance (`yfinance`) | Yahoo history |

주식도 바이낸스에 주식 영구선물(예: `SKHYUSDT`, `SOXLUSDT`)이 있으면 Yahoo 없이 같은 API로 받을 수 있습니다.
`config.yaml`의 `us_stocks`에 `binance_futures`를 넣으면 됩니다.

기본 모드는 **바이낸스 현물 USDT 페어**만 감시합니다.  
바이낸스에는 일반 주식/ETF가 거의 없어, 감시 대상은 코인입니다.

### 동적 유니버스 (`universe`)

고정 심볼 목록 대신, **최근 7일 거래대금 상위 N%** 를 매일 갱신합니다.

```yaml
universe:
  enabled: true
  quote_asset: USDT
  volume_lookback_days: 7
  top_percentile: 30      # 부하 크면 15
  max_symbols: 60         # EC2 t3.micro 안전 상한
  refresh_hours: 24
  exclude_leveraged: true
  exclude_stablecoins: true
  include_static_stocks: false  # true면 kr/us 고정도 함께
```

- 4가지 알람 규칙(extreme RSI, RSI+MACD, 다이버전스, BB 스퀴즈)은 그대로 적용됩니다.
- `max_symbols`로 최종 개수를 잘라 EC2 부하를 막습니다. 30%가 60개를 넘으면 상위 60개만 감시합니다.
- `universe.enabled: false` 이면 예전처럼 `crypto` / `kr_stocks` / `us_stocks` 고정 목록을 씁니다.

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

## Git + GitHub (PC에서 먼저)

EC2에 올리기 전에 **원격 저장소**를 만듭니다. 이후 서버는 `git clone` / `git pull` 로 배포합니다.

### 0-1. PC에서 Git 확인

```powershell
git --version
```

없으면: `winget install --id Git.Git -e --source winget` 후 터미널 재시작.

### 0-2. GitHub에 빈 저장소 만들기

1. https://github.com 로그인  
2. **New repository**  
3. 이름 예: `stock_monitor`  
4. **Private** 권장 (코드·설정 공개 방지)  
5. **README / .gitignore 추가 안 함** (로컬에 이미 있음)  
6. 생성 후 주소 복사  
   - HTTPS: `https://github.com/본인아이디/stock_monitor.git`  
   - SSH: `git@github.com:본인아이디/stock_monitor.git`

### 0-3. PC 프로젝트를 GitHub에 첫 push

```powershell
cd C:\stock_monitor

git status
git add .
git commit -m "Initial commit: stock monitor"
```

`LF will be replaced by CRLF` 경고는 Windows 줄바꿈 안내일 뿐, **무시해도 됩니다.**

원격 연결 (처음 한 번):

```powershell
git branch -M main
git remote add origin https://github.com/본인아이디/stock_monitor.git
git push -u origin main
```

- 브랜치가 이미 `master`면: `git push -u origin master` 도 가능 (서버 `git pull` 시 같은 브랜치 이름 사용)  
- GitHub 로그인: 브라우저 또는 **Personal Access Token** (Settings → Developer settings → Tokens)

**절대 커밋하지 말 것:** `.env` (`.gitignore`에 포함됨). `config.yaml`은 커밋해도 됨.

---

## AWS EC2에 올려 24시간 돌리기 (Git 기준 전체 순서)

PC를 끄지 않고 서버에서 계속 실행합니다.  
서버에서는 **UI 없이** `python -m app run` + **systemd** 를 사용합니다.

**권장 흐름:** PC에서 GitHub push → EC2에서 `git clone` → `.env`만 서버에 작성 → systemd

나중에 모바일 설정 API를 붙일 때는 HTTP/HTTPS만 열고, **앱 포트(8000 등)는 인터넷에 직접 열지 마세요.**

### A. AWS에서 인스턴스 만들기

1. [AWS](https://aws.amazon.com) 가입
2. 리전: **서울 `ap-northeast-2`**
3. **EC2** → **키 페어** 생성 → `.pem` 다운로드 (예: `stock_monitor_key.pem`)
4. **인스턴스 시작**
   - AMI: **Ubuntu 24.04 LTS**
   - 유형: **t3.micro** / **t2.micro**
   - 키 페어 선택
   - 퍼블릭 IP: **활성화**
   - 보안 그룹: **SSH(22)** → 가능하면 **내 IP**
   - HTTP/HTTPS: 나중에 모바일용 (지금 없어도 됨)
   - **고급 세부정보**: 변경 없음
5. **Running** 후 **퍼블릭 IPv4** 복사

### B. Windows에서 SSH 접속

```powershell
cd C:\
icacls .\stock_monitor_key.pem /inheritance:r
icacls .\stock_monitor_key.pem /grant:r "$($env:USERNAME):(R)"

ssh -i .\stock_monitor_key.pem ubuntu@퍼블릭IP
```
퍼블릭IP = 13.209.65.145


| 증상 | 해결 |
|------|------|
| `Identity file ... not accessible` | `-i` 뒤 **실제 .pem 경로/이름** 확인 |
| `Permission denied (publickey)` | 키 + 사용자 `ubuntu` (Ubuntu AMI) |

### C. 서버 패키지 설치

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### D. GitHub에서 clone (권장)

**이미 `scp`로 `~/stock_monitor` 가 있으면** 아래 **「처음부터 다시 (Git으로 전환)」** 참고.

```bash
cd ~
git clone https://github.com/본인아이디/stock_monitor.git stock_monitor
cd stock_monitor
```

Private 저장소면 GitHub **username + PAT(토큰)** 입력.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> `.env`는 저장소에 없습니다. **서버에서만** 만듭니다.

### E. 텔레그램 `.env` (서버만)

```bash
nano .env
```

```env
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=채팅ID
```

```bash
python -m app telegram-test
python -m app run
```

`Telegram 알림: ON` 확인 후 `Ctrl+C`.

### F. systemd 상시 실행

```bash
sudo nano /etc/systemd/system/stock-monitor.service
```

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

```bash
sudo systemctl daemon-reload
sudo systemctl enable stock-monitor
sudo systemctl start stock-monitor
sudo systemctl status stock-monitor
journalctl -u stock-monitor -f
```

SSH 창을 닫아도 **서비스는 계속 실행**됩니다.  
모니터만 끄기: `sudo systemctl stop stock-monitor` / 켜기: `start`

---

### 처음부터 다시 (Git으로 전환 / 서버 폴더 정리)

```bash
sudo systemctl stop stock-monitor
```

3. **기존 폴더 백업 후 제거** (`.env` 백업 필수)

```bash
cp ~/stock_monitor/.env ~/.env.stock_monitor.backup
mv ~/stock_monitor ~/stock_monitor_old
```

4. **clone + venv + .env 복구**

```bash
cd ~
git clone https://github.com/본인아이디/stock_monitor.git stock_monitor
cd stock_monitor
cp ~/.env.stock_monitor.backup .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app telegram-test
```

5. **systemd 재시작** (F와 동일 경로면 서비스 파일 수정 불필요)

```bash
sudo systemctl start stock-monitor
sudo systemctl status stock-monitor
```

6. 문제 없으면 예전 폴더 삭제

```bash
rm -rf ~/stock_monitor_old
```

`data/candles.db` 를 유지하려면 백업 폴더에서 복사:

```bash
mkdir -p ~/stock_monitor/data
cp ~/stock_monitor_old/data/candles.db ~/stock_monitor/data/
```

---

### G. 코드만 바꿨을 때 (재배포, Git)

**EC2·키·systemd·`.env`는 그대로.** PC에서 push 한 뒤 서버에서 pull.

**PC:**

```powershell
cd C:\stock_monitor
git add .
git commit -m "변경 내용 요약"
git push
```

**서버:**

```bash
ssh -i C:\stock_monitor_key.pem ubuntu@퍼블릭IP

cd ~/stock_monitor
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart stock-monitor
sudo systemctl status stock-monitor
```

| 변경 | 추가 작업 |
|------|-----------|
| `requirements.txt` | `pip install -r requirements.txt` |
| `config.yaml`만 (서버에서 직접 수정) | `nano config.yaml` → `restart` (push 불필요) |
| `.env` | 서버에서만 `nano .env` → `restart` |

한 줄: `git push` (PC) → `git pull` → `[pip install]` → `systemctl restart`

#### scp로 배포 중이었다면

전체 `scp -r` 은 **`.env`를 지울 수 있어** 비추천. 위 **「처음부터 다시」** 로 Git 전환하는 것을 권장합니다.

---

### H. 운영 체크리스트

- [ ] GitHub에 최신 코드 push
- [ ] EC2 **Running**
- [ ] `systemctl status stock-monitor` → active
- [ ] 텔레그램 테스트 / 알람 수신
- [ ] SSH 종료 후에도 알람 유지
- [ ] AWS 예산 알림 설정 권장

### I. 나중에 모바일로 config 바꿀 때 (예정)

1. API는 `127.0.0.1`만 listen  
2. nginx + HTTPS + 토큰  
3. 저장 후 `systemctl restart stock-monitor`  
4. 보안 그룹 **443** (앱 포트 직접 공개 금지)

---

## 다음 단계 (예정)

- 모바일용 설정 API (토큰 인증)
- 심볼별 규칙 / UI에서 심볼 편집
