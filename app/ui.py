from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from app.config import AppConfig, default_config_path, load_config, save_config
from app.monitor import Monitor
from app.settings import get_settings
from app.timeframes import TIMEFRAME_SECONDS


TIMEFRAMES = list(TIMEFRAME_SECONDS.keys())

# Toss-inspired palette
BG = "#F2F4F6"
CARD = "#FFFFFF"
TEXT = "#191F28"
SUB = "#8B95A1"
LINE = "#E5E8EB"
BLUE = "#3182F6"
BLUE_PRESS = "#1B64DA"
GREEN = "#03B26C"
RED = "#F04452"
INPUT_BG = "#F9FAFB"
FONT = ("Segoe UI", 11)
FONT_B = ("Segoe UI", 11, "bold")
FONT_TITLE = ("Segoe UI", 22, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_BTN = ("Segoe UI", 12, "bold")


class MonitorApp(tk.Tk):
    """토스 스타일 설정 UI + Start/Stop."""

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.config_path = config_path or default_config_path()
        self.title("Stock Monitor")
        self.geometry("860x720")
        self.minsize(760, 640)
        self.configure(bg=BG)

        self._monitor: Monitor | None = None
        self._thread: threading.Thread | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._status_queue: queue.Queue[tuple[dict, dict]] = queue.Queue()
        self._running = False
        self._entries: list[tk.Entry] = []

        self.var_poll = tk.StringVar()
        self.var_tf = tk.StringVar()
        self.var_period = tk.StringVar()
        self.var_min = tk.StringVar()
        self.var_max = tk.StringVar()
        self.var_cooldown = tk.StringVar()
        self.var_status = tk.StringVar()
        self.var_max_candles = tk.StringVar()

        self._build()
        self._load_into_form()
        self.after(200, self._poll_queues)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _card(self, parent: tk.Misc, **pack) -> tk.Frame:
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill=tk.X, **pack)
        # soft edge simulation
        shadow = tk.Frame(wrap, bg="#E8EAED")
        shadow.pack(fill=tk.X, padx=1, pady=(0, 2))
        card = tk.Frame(shadow, bg=CARD, padx=20, pady=18)
        card.pack(fill=tk.X)
        return card

    def _pill(self, parent: tk.Misc, text: str, fg: str, bg: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            font=FONT_SMALL,
            fg=fg,
            bg=bg,
            padx=10,
            pady=4,
        )

    def _field_row(self, parent: tk.Misc, label: str, var: tk.StringVar, choices: list[str] | None = None) -> None:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=tk.X, pady=6)
        tk.Label(row, text=label, font=FONT, fg=SUB, bg=CARD, width=18, anchor="w").pack(side=tk.LEFT)
        if choices:
            # simple option menu styled as input
            menu = tk.OptionMenu(row, var, *choices)
            menu.config(
                font=FONT,
                bg=INPUT_BG,
                fg=TEXT,
                activebackground=INPUT_BG,
                activeforeground=TEXT,
                highlightthickness=0,
                bd=0,
                relief=tk.FLAT,
                width=14,
                anchor="w",
            )
            menu["menu"].config(font=FONT, bg=CARD, fg=TEXT, activebackground=BLUE, activeforeground="white")
            menu.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        else:
            entry = tk.Entry(
                row,
                textvariable=var,
                font=FONT,
                bg=INPUT_BG,
                fg=TEXT,
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=LINE,
                highlightcolor=BLUE,
                insertbackground=TEXT,
            )
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 0))
            self._entries.append(entry)

    def _btn(self, parent: tk.Misc, text: str, command, primary: bool = False, danger: bool = False) -> tk.Button:
        if primary:
            bg, fg, active = BLUE, "white", BLUE_PRESS
        elif danger:
            bg, fg, active = "#FFF1F1", RED, "#FFE3E3"
        else:
            bg, fg, active = INPUT_BG, TEXT, LINE
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            font=FONT_BTN,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg if not primary else "white",
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=10,
            cursor="hand2",
        )
        return btn

    def _build(self) -> None:
        root = tk.Frame(self, bg=BG, padx=24, pady=20)
        root.pack(fill=tk.BOTH, expand=True)

        # Header
        header = tk.Frame(root, bg=BG)
        header.pack(fill=tk.X, pady=(0, 16))
        left = tk.Frame(header, bg=BG)
        left.pack(side=tk.LEFT)
        tk.Label(left, text="Stock Monitor", font=FONT_TITLE, fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(left, text="RSI 조건 알림 · 실시간 모니터", font=FONT_SMALL, fg=SUB, bg=BG).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=BG)
        right.pack(side=tk.RIGHT)
        self.tg_pill = self._pill(right, "Telegram", BLUE, "#E8F3FF")
        self.tg_pill.pack(side=tk.RIGHT, padx=(8, 0))
        self.state_pill = self._pill(right, "대기 중", SUB, LINE)
        self.state_pill.pack(side=tk.RIGHT)
        self._refresh_telegram_label()

        # Settings card
        conf = self._card(root, pady=(0, 12))
        tk.Label(conf, text="모니터 설정", font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        tk.Label(
            conf,
            text="변경 후 저장하거나 Start 하면 config.yaml 에 반영됩니다.",
            font=FONT_SMALL,
            fg=SUB,
            bg=CARD,
        ).pack(anchor="w", pady=(2, 10))

        grid = tk.Frame(conf, bg=CARD)
        grid.pack(fill=tk.X)
        col1 = tk.Frame(grid, bg=CARD)
        col2 = tk.Frame(grid, bg=CARD)
        col1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 12))
        col2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._field_row(col1, "폴링 간격(초)", self.var_poll)
        self._field_row(col1, "봉 단위", self.var_tf, TIMEFRAMES)
        self._field_row(col1, "RSI period", self.var_period)
        self._field_row(col1, "알림 쿨다운(초)", self.var_cooldown)

        self._field_row(col2, "RSI min (이하)", self.var_min)
        self._field_row(col2, "RSI max (이상)", self.var_max)
        self._field_row(col2, "상태 로그 주기(초)", self.var_status)
        self._field_row(col2, "히스토리 봉 개수", self.var_max_candles)

        tk.Label(
            conf,
            text="심볼(코인/주식)은 config.yaml 에서 수정 · 코인은 많이 넣도 비교적 가볍고, 주식은 늘릴수록 부하↑",
            font=FONT_SMALL,
            fg=SUB,
            bg=CARD,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        # Actions
        actions = tk.Frame(root, bg=BG)
        actions.pack(fill=tk.X, pady=(0, 12))
        self.btn_start = self._btn(actions, "시작하기", self._start, primary=True)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = self._btn(actions, "중지", self._stop, danger=True)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_save = self._btn(actions, "설정 저장", self._save)
        self.btn_save.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_tg = self._btn(actions, "텔레그램 테스트", self._telegram_test)
        self.btn_tg.pack(side=tk.LEFT)

        # Status card
        status_card = self._card(root, pady=(0, 12))
        tk.Label(status_card, text="현재가 / RSI", font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        self.status_text = tk.Text(
            status_card,
            height=5,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg=INPUT_BG,
            fg=TEXT,
            relief=tk.FLAT,
            padx=12,
            pady=10,
            highlightthickness=0,
            state=tk.DISABLED,
        )
        self.status_text.pack(fill=tk.X, pady=(10, 0))

        # Log card
        log_wrap = tk.Frame(root, bg=BG)
        log_wrap.pack(fill=tk.BOTH, expand=True)
        shadow = tk.Frame(log_wrap, bg="#E8EAED")
        shadow.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 2))
        log_card = tk.Frame(shadow, bg=CARD, padx=20, pady=18)
        log_card.pack(fill=tk.BOTH, expand=True)
        tk.Label(log_card, text="로그", font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        log_body = tk.Frame(log_card, bg=CARD)
        log_body.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log_text = tk.Text(
            log_body,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=INPUT_BG,
            fg=TEXT,
            relief=tk.FLAT,
            padx=12,
            pady=10,
            highlightthickness=0,
            state=tk.DISABLED,
        )
        scroll = tk.Scrollbar(log_body, command=self.log_text.yview, bg=CARD, troughcolor=INPUT_BG)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _refresh_telegram_label(self) -> None:
        get_settings.cache_clear()
        settings = get_settings()
        if settings.telegram_enabled:
            self.tg_pill.configure(text="Telegram ON", fg=BLUE, bg="#E8F3FF")
        else:
            self.tg_pill.configure(text="Telegram OFF", fg=SUB, bg=LINE)

    def _load_into_form(self) -> None:
        cfg = load_config(self.config_path)
        self.var_poll.set(str(cfg.poll_interval_seconds))
        self.var_tf.set(cfg.timeframe)
        self.var_period.set(str(cfg.rsi.period))
        self.var_min.set(str(cfg.rsi.min))
        self.var_max.set(str(cfg.rsi.max))
        self.var_cooldown.set(str(cfg.alert_cooldown_seconds))
        self.var_status.set(str(cfg.status_log_seconds))
        self.var_max_candles.set(str(cfg.history.max_candles))
        self._append_log(f"설정 로드 · {self.config_path}")

    def _read_form_config(self) -> AppConfig:
        base = load_config(self.config_path)
        data = base.model_dump(mode="python")
        data["poll_interval_seconds"] = float(self.var_poll.get().strip())
        data["timeframe"] = self.var_tf.get().strip()
        data["alert_cooldown_seconds"] = int(float(self.var_cooldown.get().strip()))
        data["status_log_seconds"] = float(self.var_status.get().strip())
        data["rsi"] = {
            "period": int(float(self.var_period.get().strip())),
            "min": float(self.var_min.get().strip()),
            "max": float(self.var_max.get().strip()),
        }
        data["history"]["max_candles"] = int(float(self.var_max_candles.get().strip()))
        return AppConfig.model_validate(data)

    def _save(self) -> None:
        try:
            cfg = self._read_form_config()
            path = save_config(cfg, self.config_path)
            self._append_log(f"설정 저장 완료 · {path}")
            messagebox.showinfo("저장", "설정을 저장했습니다.")
        except Exception as exc:
            messagebox.showerror("저장 실패", str(exc))

    def _set_running_ui(self, running: bool) -> None:
        self._running = running
        self.btn_start.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.btn_stop.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.btn_save.configure(state=tk.DISABLED if running else tk.NORMAL)
        if running:
            self.state_pill.configure(text="실행 중", fg=GREEN, bg="#E8F8F0")
        else:
            self.state_pill.configure(text="대기 중", fg=SUB, bg=LINE)

    def _start(self) -> None:
        if self._running:
            return
        try:
            cfg = self._read_form_config()
            save_config(cfg, self.config_path)
        except Exception as exc:
            messagebox.showerror("설정 오류", str(exc))
            return

        get_settings.cache_clear()
        self._refresh_telegram_label()

        def on_log(msg: str) -> None:
            self._log_queue.put(msg)

        def on_status(prices: dict, rsis: dict) -> None:
            self._status_queue.put((prices, rsis))

        monitor = Monitor(
            cfg,
            on_log=on_log,
            on_status=on_status,
            register_signals=False,
        )
        self._monitor = monitor

        def worker() -> None:
            try:
                monitor.start()
            except Exception as exc:
                self._log_queue.put(f"모니터 오류: {exc}")
            finally:
                self._log_queue.put("__DONE__")

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        self._set_running_ui(True)
        self._append_log("모니터를 시작했습니다")

    def _stop(self) -> None:
        if self._monitor is not None:
            self._monitor.request_stop()
            self._append_log("중지 요청...")
        self.btn_stop.configure(state=tk.DISABLED)

    def _telegram_test(self) -> None:
        from app.alerts import TelegramNotifier

        get_settings.cache_clear()
        settings = get_settings()
        self._refresh_telegram_label()
        if not settings.telegram_enabled:
            messagebox.showwarning("Telegram", ".env 에 TOKEN / CHAT_ID 를 넣으세요.")
            return
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        try:
            notifier.send("stock-monitor 텔레그램 연동 테스트 OK")
            self._append_log("Telegram 테스트 전송 성공")
            messagebox.showinfo("Telegram", "테스트 메시지를 보냈습니다.")
        except Exception as exc:
            messagebox.showerror("Telegram 실패", str(exc))
        finally:
            notifier.close()

    def _poll_queues(self) -> None:
        while True:
            try:
                msg = self._log_queue.get_nowait()
            except queue.Empty:
                break
            if msg == "__DONE__":
                self._set_running_ui(False)
                self._monitor = None
                self._append_log("모니터가 종료되었습니다")
            else:
                self._append_log(msg)

        while True:
            try:
                prices, rsis = self._status_queue.get_nowait()
            except queue.Empty:
                break
            self._render_status(prices, rsis)

        self.after(200, self._poll_queues)

    def _render_status(self, prices: dict, rsis: dict) -> None:
        lines = []
        for key, price in prices.items():
            rsi = rsis.get(key)
            rsi_s = f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "n/a"
            symbol = key.split(":", 1)[-1]
            if isinstance(price, float):
                if abs(price) >= 1000:
                    price_s = f"{price:,.2f}"
                elif abs(price) >= 1:
                    price_s = f"{price:,.4f}"
                else:
                    price_s = f"{price:.6f}"
            else:
                price_s = str(price)
            lines.append(f"{symbol:<12}  {price_s:>14}   RSI {rsi_s}")
        text = "\n".join(lines) if lines else "데이터를 기다리는 중..."
        self.status_text.configure(state=tk.NORMAL)
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert(tk.END, text)
        self.status_text.configure(state=tk.DISABLED)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        if self._running and self._monitor is not None:
            self._monitor.request_stop()
            self._append_log("창 종료 · 모니터 중지 중...")
            self.after(500, self.destroy)
        else:
            self.destroy()


def run_ui(config_path: str | Path | None = None) -> int:
    app = MonitorApp(Path(config_path) if config_path else None)
    app.mainloop()
    return 0
