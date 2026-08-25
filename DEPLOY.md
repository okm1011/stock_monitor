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
sudo systemctl restart stock-monitor-web
sudo systemctl status stock-monitor --no-pager
sudo systemctl status stock-monitor-web --no-pager
```

둘 다 `Active: active (running)` 이면 성공.

- `stock-monitor` — 알람 모니터
- `stock-monitor-web` — 설정 페이지 (`:8080`)

웹 템플릿/폼을 바꿨는데 모니터만 재시작하면, 알람은 새 코드인데 설정 페이지는 예전 화면이 그대로입니다. **코드 pull 후에는 둘 다 재시작**하세요.

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
sudo systemctl restart stock-monitor-web
sudo systemctl status stock-monitor --no-pager
sudo systemctl status stock-monitor-web --no-pager
```

| 명령 | 의미 |
|------|------|
| `sudo systemctl restart stock-monitor` | 모니터 재시작 |
| `sudo systemctl restart stock-monitor-web` | 설정 웹 재시작 |
| `sudo systemctl status stock-monitor --no-pager` | 모니터 상태 |
| `sudo systemctl status stock-monitor-web --no-pager` | 웹 상태 |
| `journalctl -u stock-monitor -f` | 모니터 실시간 로그 |
| `journalctl -u stock-monitor-web -f` | 웹 실시간 로그 |

---

## C. 변경 종류별

| 무엇을 바꿨나 | PC | 서버 |
|---------------|----|------|
| 코드 (모니터·웹 포함) | `commit` → `push` | `git pull` → **`stock-monitor` + `stock-monitor-web` 둘 다 restart** |
| `config.yaml`만 git으로 | `commit` → `push` | `git pull` → `stock-monitor` restart (웹 화면은 그대로여도 됨) |
| `requirements.txt` | `commit` → `push` | `git pull` → **`pip install -r requirements.txt`** → 둘 다 restart |
| `.env` (텔레그램/웹비번) | push **금지** | 서버에서 `nano .env` → 해당 서비스 restart |
| 웹에서 숫자만 저장 | — | pull/재시작 **불필요** (모니터가 수 초 내 파일 재읽기) |

---

## D. 설정 웹 (모바일/PC에서 config 수정)

이미 배포된 설정 페이지에서 **값만** 바꾸면, 모니터가 **약 5초마다** `config.yaml`을 다시 읽어 반영합니다. 그때는 재시작이 필요 없습니다.

설정 페이지에 칸이 추가되는 등 **웹 코드를 바꾼 뒤**에는 `git pull` 후 `stock-monitor-web`도 재시작해야 새 화면이 나옵니다.

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
**서버:** `git pull` → (`pip install`) → `sudo systemctl restart stock-monitor stock-monitor-web`  
**웹에서 숫자만 바꿀 때:** `http://서버IP:8080` → 저장 (pull/재시작 없음)
