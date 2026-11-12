# Stock Monitor

바이낸스 선물 상위 종목과 큰 자산(지수·원자재·금리·환율)의 **4시간/일/주/월봉**을 감시해서 조건에 들어오는 순간 텔레그램 알림을 보냅니다.  
서버(AWS EC2)에서 `systemd`로 24시간 실행 중입니다.

**감시 대상**

- 바이낸스 USDⓈ-M 선물 **24시간 거래대금 상위 100개** (코인 + NVDA·SOXL 같은 주식 토큰 + 금·원유 같은 원자재 토큰), 하루 1번 갱신
- 야후 파이낸스 큰 자산 20개: 나스닥100·S&P500·러셀2000 선물, SOXX, 금·은·백금·구리, WTI·브렌트·천연가스, 달러인덱스, 미 10년물 금리, TLT, VIX, 달러/엔, 달러/원, 코스피, 니케이, 항셍 (웹에서 추가/삭제)

**알람**

| 알람 | 조건 | 설정 |
|------|------|------|
| 1. RSI 과매수/과매도 | RSI(14)가 과매수 기준을 위로 넘거나 과매도 기준 아래로 내려가는 순간 | 타임프레임별 on/off, 과매수·과매도 각각 on/off와 숫자 |
| 2. 이평 근접 | 가격이 50 / 200 / 100 이평선 **위에서 내려오거나 아래에서 올라와** 근접 % 안으로 들어오는 순간 | 선마다 종류(EMA / MA / VWMA, 기본 EMA50·EMA200·VWMA100), 선 × 타임프레임별 on/off, 근접 %, 위/아래 방향 |

- 형성 중인 봉 기준으로 바로 울립니다 (봉 마감 대기 없음). 같은 봉에서 같은 신호는 1번만.
- 재알람 조건: 알람 후 RSI는 기준에서 5포인트, 이평은 근접 %의 3배만큼 멀어졌다가 다시 들어와야 또 울립니다 (경계선 잔진동 방지).
- 데이터가 모자라면 해당 알람만 자동으로 건너뜁니다. 예: 상장 1년 미만 토큰의 월봉 RSI, 바이낸스 코인의 월봉 EMA200(200개월 필요), 거래량 없는 지수·금리·환율의 VWMA100.
- 야후는 4시간봉이 없어서 1시간봉을 세션 시작부터 4개씩 묶어 만듭니다 (미국 주식 09:30/13:30, CME 선물 18:00 ET 기준).

---

## 접속 정보

| 항목 | 값 |
|------|-----|
| GitHub | `https://github.com/okm1011/stock_monitor.git` |
| 브랜치 | `master` |
| EC2 IP | `13.209.65.145` |
| AMI | Amazon Linux 2023 |
| SSH 사용자 | `ec2-user` |
| 키 파일 | `C:\stock_monitor_key.pem` |
| 서버 경로 | `/home/ec2-user/stock_monitor` |
| systemd 서비스 | `stock-monitor` |
| 설정 웹 | `http://13.209.65.145:8080` (비밀번호, `stock-monitor-web`) |
| Python | 서버 venv는 **3.11** 권장 (기본 3.9면 타입 에러 남) |

> IP가 바뀌면 이 표와 아래 SSH 명령의 IP만 고치세요.

### SSH 접속 (PC PowerShell)

```powershell
ssh -i C:\stock_monitor_key.pem ec2-user@13.209.65.145
```

| 증상 | 해결 |
|------|------|
| `Permission denied (publickey)` | 키 확인 + 사용자 **`ec2-user`** (`ubuntu` 아님) |
| 연결 타임아웃 | 보안 그룹 SSH(22), 인스턴스 Running/상태검사 |
| 키 권한 오류 | 아래 `icacls` 한 번 실행 |

```powershell
icacls C:\stock_monitor_key.pem /inheritance:r
icacls C:\stock_monitor_key.pem /grant:r "$($env:USERNAME):(R)"
```

---

## 코드 업데이트 후 배포 (가장 자주 씀)

PC에서 수정 → GitHub push → 서버 pull → 서비스 재시작.

### 1) PC에서 커밋·푸시

```powershell
cd C:\stock_monitor
git status
git add .
git commit -m "변경 요약"
git push
```

- **커밋하면 안 됨:** `.env` (텔레그램 토큰)
- **커밋해도 됨:** `config.yaml`, 코드, `requirements.txt`, README

`git push` 후 GitHub `master`에 반영됐는지 확인.

### 2) 서버 SSH 접속

```powershell
ssh -i C:\stock_monitor_key.pem ec2-user@13.209.65.145
```

### 3) 서버에서 pull + 재시작

```bash
cd ~/stock_monitor
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart stock-monitor
sudo systemctl status stock-monitor
```

`Active: active (running)` 이면 성공.

실시간 로그:

```bash
journalctl -u stock-monitor -f
```

`Ctrl+C`로 로그만 끊고, SSH는 `exit`로 나가도 **모니터는 계속** 돕니다.

### 변경 종류별 추가 작업

| 무엇을 바꿨나 | PC | 서버 |
|---------------|----|------|
| 코드 (`app/`) | `commit` → `push` | `git pull` → `restart` |
| `requirements.txt` | `commit` → `push` | `git pull` → **`pip install -r requirements.txt`** → `restart` |
| `config.yaml` (PC에서 수정) | `commit` → `push` | `git pull` → `restart` |
| `config.yaml` (서버에서만 임시 수정) | push 불필요 | `nano config.yaml` → `restart` 또는 **설정 웹** |
| `.env` (텔레그램) | push **금지** | 서버에서만 `nano .env` → `restart` |

한 줄 요약: **`git push`(PC) → `git pull` → `[pip]` → `systemctl restart`**

**설정 웹:** 폰/PC 브라우저에서 `config.yaml` 저장 → 모니터가 수 초 내 자동 반영. 최초 세팅은 [DEPLOY.md § D](DEPLOY.md) 참고.

---

## 서버에서 다시 킬 때 / 끄고 켤 때

모니터는 보통 **systemd가 자동 실행**합니다. SSH로 들어가서 상태만 보면 됩니다.

### 상태 확인

```bash
sudo systemctl status stock-monitor
```

| 표시 | 의미 |
|------|------|
| `active (running)` | 정상 동작 중 |
| `inactive (dead)` | 꺼져 있음 → 아래 start |
| `failed` | 오류 → 로그 확인 후 restart |

### 켜기 / 끄기 / 재시작

```bash
# 켜기 (꺼져 있을 때)
sudo systemctl start stock-monitor

# 끄기 (알람 중단)
sudo systemctl stop stock-monitor

# 재시작 (설정·코드 반영, 가장 많이 씀)
sudo systemctl restart stock-monitor
```

재시작 후 반드시:

```bash
sudo systemctl status stock-monitor
journalctl -u stock-monitor -n 50 --no-pager
```

### 부팅 후 자동 실행

이미 `enable` 되어 있으면 EC2를 재부팅해도 자동으로 다시 뜹니다.

```bash
# 자동 시작 켜져 있는지
systemctl is-enabled stock-monitor

# 꺼져 있으면
sudo systemctl enable stock-monitor
```

### EC2 인스턴스 자체를 재부팅했을 때

1. AWS 콘솔에서 인스턴스가 **Running**인지 확인 (IP가 바뀌었으면 README 표 수정)
2. SSH 접속
3. 서비스 확인:

```bash
sudo systemctl status stock-monitor
```

`running`이면 끝. 안 떠 있으면:

```bash
sudo systemctl start stock-monitor
sudo systemctl status stock-monitor
journalctl -u stock-monitor -n 100 --no-pager
```

### 수동으로 한 번만 실행해 보고 싶을 때

서비스를 먼저 끄고 포그라운드로 실행합니다. (둘 다 켜면 충돌할 수 있음)

```bash
sudo systemctl stop stock-monitor
cd ~/stock_monitor
source .venv/bin/activate
python -m app verify
python -m app telegram-test
python -m app run
```

확인 후 `Ctrl+C`, 다시 상시 실행:

```bash
sudo systemctl start stock-monitor
sudo systemctl status stock-monitor
```

### 텔레그램 / .env만 고칠 때

```bash
cd ~/stock_monitor
nano .env
```

```env
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=채팅ID
```

```bash
source .venv/bin/activate
python -m app telegram-test
sudo systemctl restart stock-monitor
```

---

## 설정 요약 (`config.yaml`)

보통은 설정 웹(`http://서버IP:8080`)에서 바꾸면 됩니다. 파일 구조:

```yaml
poll_seconds: 60          # 바이낸스 갱신 주기 (30 이상)
universe:                 # 바이낸스 선물 상위 N
  top_n: 100
  include_tradfi: true    # 주식·원자재 토큰 포함
  exclude_bases: [USDC, FDUSD, ...]
macro:                    # 야후 큰 자산
  poll_seconds: 300       # 120 이상 (야후 차단 방지)
  assets:
    - {ticker: NQ=F, name: 나스닥100 선물}
    # ...
rsi_alert:                # 알람 1
  period: 14
  rearm_gap: 5            # 재알람: 기준에서 5포인트 되돌아가야
  tf_4h: {enabled: true, overbought_enabled: true, overbought: 75, oversold_enabled: true, oversold: 25}
  tf_1d: {...}            # tf_1w, tf_1mo 동일 구조
ma_alert:                 # 알람 2
  rearm_mult: 3           # 재알람: 근접%×3 이상 멀어져야
  ma50:                   # ma200, ma100 동일 구조
    type: ema             # ema=EMA, sma=MA(단순), vwma=VWMA
    tf_4h: {enabled: true, proximity_pct: 1.0, from_above: true, from_below: true}
    tf_1d: {...}
```

알람 양 참고 (최근 30일 시뮬레이션, 바이낸스 상위 100 · 4h+일봉만): 기본값으로 하루 약 100건이고, 그중 4시간봉 EMA50 근접이 약 30건으로 가장 많습니다. 너무 많으면 4시간봉 이평 근접을 끄거나 `top_n`을 줄이세요.

알람 없이 현재 RSI / 이평 이격만 표로 보기:

```bash
python -m app verify              # 바이낸스 상위 5개 + 야후 전체
python -m app verify --binance 20 --no-macro
```

PC에서 UI로 보려면:

```powershell
cd C:\stock_monitor
.\.venv\Scripts\python.exe -m app
```

---

## 문제 있을 때

### 로그부터 보기

```bash
journalctl -u stock-monitor -n 100 --no-pager
journalctl -u stock-monitor -f
```

### `str | None` / pydantic TypeError

서버 Python이 3.9인 경우. venv를 3.11로 다시 만드세요.

```bash
sudo systemctl stop stock-monitor
cd ~/stock_monitor
deactivate 2>/dev/null
rm -rf .venv
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
sudo systemctl start stock-monitor
```

### git pull 충돌

```bash
cd ~/stock_monitor
git status
# 서버에서 config만 손댔다면 백업 후
cp config.yaml ~/config.yaml.bak
git checkout -- config.yaml
git pull
# 필요하면 bak 내용 다시 반영 후 restart
```

### 서비스는 도는데 알람이 안 옴

```bash
cd ~/stock_monitor
source .venv/bin/activate
python -m app telegram-test
sudo systemctl status stock-monitor
journalctl -u stock-monitor -n 50 --no-pager
```

`.env` 토큰/채팅 ID, `Telegram 알림: ON` 로그를 확인하세요.
