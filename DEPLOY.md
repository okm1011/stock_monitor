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

> IP가 바뀌면 위 SSH 주소만 고치면 됩니다.

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

## D. 한 줄 요약

**PC:** `git add .` → `git commit -m "..."` → `git push`  
**서버:** `git pull` → (`pip install`) → `sudo systemctl restart stock-monitor`
