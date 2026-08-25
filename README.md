# Stock Monitor

Binance 시세를 받아 **1시간 봉 마감** 기준으로 규칙이 맞으면 텔레그램 알림을 보냅니다.  
서버(AWS EC2)에서 `systemd`로 24시간 실행 중입니다.

- 코인: 바이낸스 현물 USDT, **7일 거래대금 상위 30%**(최대 60개), 매일 갱신
- 주식/ETF: 바이낸스 USDⓈ-M 선물 (`config.yaml`의 `us_stocks` + `binance_futures`)
# 알람 규칙 on/off (`config.yaml` rules.*.enabled)
# - extreme_rsi: RSI 과매수/과매도
# - rsi_macd_cross: RSI 이탈 + MACD 크로스
# - divergence: 상/하강 다이버전스
# - bb_squeeze: 볼린저 스퀴즈
# - volume_spike: 잡코인 펌프 초입 (가격% ∧ 거래량 ∧ 횡보 ∧ 메이저제외)
# - accumulation: 알트코인 신호 1 OI 변동
# - absorption_bar: 알트코인 신호 2 매집봉 (윗꼬리+거래량, 종가 거의 고정)

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

```yaml
universe:
  enabled: true
  top_percentile: 30   # 부하 크면 15
  max_symbols: 60
  refresh_hours: 24
  include_static_stocks: true

us_stocks:             # 바이낸스 선물로 받는 종목
  - ticker: SKHY
    binance_futures: SKHYUSDT
  # ...

timeframe: 1h
signal_on_closed_bar: true

rules:
  extreme_rsi:
    enabled: true
  volume_spike:          # 잡코인 펌프 초입 (1∧2∧3)
    enabled: true
    timeframe: 3m
    window_bars: 5
    min_price_pct: 15
    volume_mult: 4.0
    quiet_bars: 40
    quiet_range_pct: 10
    cooldown_seconds: 600
  accumulation:          # 알트 신호 1 OI 변동
    enabled: true
    range_pct: 40
    tight_range_pct: 28
    oi_change_pct: 12
    vol_mult: 1.35
    btc_rs_pct: 4
    min_oi_usdt: 1000000
    cooldown_seconds: 86400
  absorption_bar:        # 알트 신호 2 매집봉
    enabled: true
    timeframe: 4h
    wick_ratio: 0.55
    max_body_ratio: 0.30
    max_close_pct: 3.5
    volume_mult: 2.5
```

- RSI 등 기존 알람은 **1시간 봉** 마감 기준
- `volume_spike`는 선물 알트 **펌프 초입만** (메이저 제외, 횡보 후 +15%·거래량×4)
- `accumulation`은 **알트 신호 1**: 아직 안 터진 알트 중 OI↑ 또는 거래량 회복 + 박스/BTC강세
- `absorption_bar`는 **알트 신호 2**: 4h 매집봉 (윗꼬리 + 거래량, 종가는 거의 안 움직임)
- 같은 심볼 반복은 `cooldown_seconds`로 제한

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
