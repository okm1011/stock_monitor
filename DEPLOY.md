# 배포 / 재시작 치트시트

PC에서 코드 수정 → GitHub → EC2 반영 → 서비스 재시작

## 접속 정보

| 항목 | 값 |
|------|-----|
| 로컬 | `C:\stock_monitor` |
| GitHub | `https://github.com/okm1011/stock_monitor.git` |
| SSH | `ssh -i C:\stock_monitor_key.pem ec2-user@13.209.65.145` |
| 서버 경로 | `~/stock_monitor` (`/home/ec2-user/stock_monitor`) |
| 서비스 | `stock-monitor` |
| 설정 웹 | `http://13.209.65.145:8080` (`stock-monitor-web`) |

> IP가 바뀌면 위 SSH 주소·웹 URL의 IP만 고치세요.

---

## A. 코드 업데이트 배포 (가장 자주)

### 1) PC (PowerShell)

```powershell
cd C:\stock_monitor
git status
git add .
git commit -m "변경 요약 메시지"
git push
```

- `.env` 는 커밋하지 마세요.
- 커밋할 게 없으면 `git commit` 은 건너뛰고, 이미 push 된 상태인지 `git status` 로 확인만 하면 됩니다.

### 2) 서버 SSH 접속 (PC PowerShell)

```powershell
ssh -i C:\stock_monitor_key.pem ec2-user@13.209.65.145
```

### 3) 서버에서 pull + 재시작 (SSH 안)

```bash
cd ~/stock_monitor
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart stock-monitor
sudo systemctl status stock-monitor
```

`Active: active (running)` 이면 성공.

`status` 맨 아래 `lines ... (END)` 는 **멈춘 게 아니라 페이저**입니다. **`q`** 로 나가세요.
(페이저 없이 보려면: `sudo systemctl status stock-monitor --no-pager`)

실시간 로그:

```bash
journalctl -u stock-monitor -f
```

`Ctrl+C` 로 로그만 끊기. SSH 종료는 `exit` (모니터는 계속 실행).

> 재시작 직후엔 backfill 로그가 많이 나오고, 이후엔 상태(~15초) / 거래량 스캔(~3분) 간격이라 중간에 조용할 수 있습니다.

---

## B. 서버만 다시 킬 때 (코드 변경 없음)

SSH 접속 후:

```bash
sudo systemctl restart stock-monitor
sudo systemctl status stock-monitor --no-pager
```

| 명령 | 의미 |
|------|------|
| `sudo systemctl start stock-monitor` | 켜기 |
| `sudo systemctl stop stock-monitor` | 끄기 |
| `sudo systemctl restart stock-monitor` | 재시작 |
| `sudo systemctl status stock-monitor --no-pager` | 상태 (페이저 없음) |
| `journalctl -u stock-monitor -n 50 --no-pager` | 최근 로그 |
| `journalctl -u stock-monitor -f` | 실시간 로그 |

---

## C. 변경 종류별

| 무엇을 바꿨나 | PC | 서버 |
|---------------|----|------|
| 코드 / `config.yaml` | `commit` → `push` | `git pull` → `restart` |
| `requirements.txt` | `commit` → `push` | `git pull` → **`pip install -r requirements.txt`** → `restart` |
| `.env` (텔레그램) | push **금지** | 서버에서 `nano .env` → `restart` |

---

## D. 설정 웹 (모바일/PC에서 config 수정)

브라우저로 `config.yaml`을 바꿉니다. 모니터가 **약 5초마다** 파일을 다시 읽어 반영하므로 재시작은 필요 없습니다.

### 1) 서버 `.env`에 비밀번호 추가

```bash
cd ~/stock_monitor
nano .env
```

```env
CONFIG_WEB_PASSWORD=원하는비밀번호
CONFIG_WEB_SECRET=아무긴랜덤문자열
CONFIG_WEB_PORT=8080
```

### 2) 패키지 + sudo 권한 (재시작용, 한 번만)

```bash
source .venv/bin/activate
pip install -r requirements.txt

# 비밀번호 없이 systemctl restart 허용
sudo tee /etc/sudoers.d/stock-monitor <<'EOF'
ec2-user ALL=(ALL) NOPASSWD: /bin/systemctl restart stock-monitor, /bin/systemctl status stock-monitor, /usr/bin/systemctl restart stock-monitor, /usr/bin/systemctl status stock-monitor
EOF
sudo chmod 440 /etc/sudoers.d/stock-monitor
```

### 3) systemd 등록

```bash
sudo cp ~/stock_monitor/deploy/stock-monitor-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-monitor-web
sudo systemctl status stock-monitor-web --no-pager
```

### 4) AWS 보안 그룹

인바운드 규칙 추가: **TCP 8080** → 내 IP(권장) 또는 `0.0.0.0/0`(비번만으로 보호, 비권장)

### 5) 접속

폰/PC 브라우저: `http://13.209.65.145:8080`  
비밀번호 로그인 → 값 수정 → **저장** (수 초 내 자동 반영)

| 명령 | 의미 |
|------|------|
| `sudo systemctl restart stock-monitor-web` | 설정 웹 재시작 |
| `journalctl -u stock-monitor-web -f` | 웹 로그 |

> `.env`의 비밀번호는 Git에 올리지 마세요.  
> 저장 시 `config.yaml.bak` 백업이 생깁니다.

---

## E. 한 줄 요약

**PC:** `git add .` → `git commit -m "..."` → `git push`  
**서버:** `git pull` → (`pip install`) → `sudo systemctl restart stock-monitor`  
**설정만 바꿀 때:** 웹 `http://서버IP:8080` → 저장 (자동 반영)
