from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from app.config import default_config_path, load_config
from app.monitor import Monitor
from app.settings import get_settings


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
FONT_B = ("Segoe UI", 11, "bold")
FONT_TITLE = ("Segoe UI", 22, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_BTN = ("Segoe UI", 12, "bold")


class MonitorApp(tk.Tk):
    """PC용 Start/Stop. 알람 설정은 설정 웹 또는 config.yaml에서."""

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.config_path = config_path or default_config_path()
        self.title("Stock Monitor")
        self.geometry("980x760")
        self.minsize(780, 600)
        self.configure(bg=BG)

        self._monitor: Monitor | None = None
        self._thread: threading.Thread | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._status_queue: queue.Queue[list[str]] = queue.Queue()
        self._running = False

        self._build()
        self._append_log(f"설정 파일 · {self.config_path}")
        self.after(200, self._poll_queues)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _pill(self, parent: tk.Misc, text: str, fg: str, bg: str) -> tk.Label:
        return tk.Label(parent, text=text, font=FONT_SMALL, fg=fg, bg=bg, padx=10, pady=4)

    def _btn(self, parent, text, command, primary=False, danger=False) -> tk.Button:
        if primary:
            bg, fg, active = BLUE, "white", BLUE_PRESS
        elif danger:
            bg, fg, active = "#FFF1F1", RED, "#FFE3E3"
        else:
            bg, fg, active = INPUT_BG, TEXT, LINE
        return tk.Button(
            parent, text=text, command=command, font=FONT_BTN, bg=bg, fg=fg,
            activebackground=active, relief=tk.FLAT, bd=0, padx=16, pady=9, cursor="hand2",
        )

    def _card(self, parent: tk.Misc, title: str, expand: bool) -> tk.Frame:
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill=tk.BOTH, expand=expand, pady=(0, 10))
        shadow = tk.Frame(wrap, bg="#E8EAED")
        shadow.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 2))
        card = tk.Frame(shadow, bg=CARD, padx=20, pady=16)
        card.pack(fill=tk.BOTH, expand=True)
        tk.Label(card, text=title, font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        return card

    def _text(self, parent: tk.Misc, height: int | None, font_size: int) -> tk.Text:
        body = tk.Frame(parent, bg=CARD)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        kw = {"height": height} if height else {}
        text = tk.Text(
            body, wrap=tk.NONE, font=("Consolas", font_size), bg=INPUT_BG, fg=TEXT,
            relief=tk.FLAT, padx=12, pady=10, highlightthickness=0, state=tk.DISABLED, **kw,
        )
        scroll = tk.Scrollbar(body, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return text

    def _build(self) -> None:
        root = tk.Frame(self, bg=BG, padx=24, pady=18)
        root.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(root, bg=BG)
        header.pack(fill=tk.X, pady=(0, 12))
        left = tk.Frame(header, bg=BG)
        left.pack(side=tk.LEFT)
        tk.Label(left, text="Stock Monitor", font=FONT_TITLE, fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(
            left,
            text="바이낸스 선물 상위 + 큰 자산 · RSI 과매수/과매도 · EMA50/200·VWMA100 근접",
            font=FONT_SMALL, fg=SUB, bg=BG,
        ).pack(anchor="w")
        right = tk.Frame(header, bg=BG)
        right.pack(side=tk.RIGHT)
        self.tg_pill = self._pill(right, "Telegram", BLUE, "#E8F3FF")
        self.tg_pill.pack(side=tk.RIGHT, padx=(8, 0))
        self.state_pill = self._pill(right, "대기 중", SUB, LINE)
        self.state_pill.pack(side=tk.RIGHT)
        self._refresh_telegram_label()

        actions = tk.Frame(root, bg=BG)
        actions.pack(fill=tk.X, pady=(0, 10))
        self.btn_start = self._btn(actions, "시작하기", self._start, primary=True)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = self._btn(actions, "중지", self._stop, danger=True)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop.configure(state=tk.DISABLED)
        self._btn(actions, "설정 파일 열기", self._open_config).pack(side=tk.LEFT, padx=(0, 8))
        self._btn(actions, "텔레그램 테스트", self._telegram_test).pack(side=tk.LEFT)

        status_card = self._card(root, "현재가 / RSI (4h 1d 1w 1mo)", expand=False)
        self.status_text = self._text(status_card, height=10, font_size=10)
        log_card = self._card(root, "로그", expand=True)
        self.log_text = self._text(log_card, height=None, font_size=9)

    def _refresh_telegram_label(self) -> None:
        get_settings.cache_clear()
        settings = get_settings()
        if settings.telegram_enabled:
            self.tg_pill.configure(text="Telegram ON", fg=BLUE, bg="#E8F3FF")
        else:
            self.tg_pill.configure(text="Telegram OFF", fg=SUB, bg=LINE)

    def _set_running_ui(self, running: bool) -> None:
        self._running = running
        self.btn_start.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.btn_stop.configure(state=tk.NORMAL if running else tk.DISABLED)
        if running:
            self.state_pill.configure(text="실행 중", fg=GREEN, bg="#E8F8F0")
        else:
            self.state_pill.configure(text="대기 중", fg=SUB, bg=LINE)

    def _start(self) -> None:
        if self._running:
            return
        try:
            cfg = load_config(self.config_path)
        except Exception as exc:
            messagebox.showerror("설정 오류", str(exc))
            return

        self._refresh_telegram_label()
        monitor = Monitor(
            cfg,
            on_log=self._log_queue.put,
            on_status=self._status_queue.put,
            register_signals=False,
            config_path=self.config_path,
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
        self._append_log("모니터를 시작했습니다 (첫 백필에 1~2분 걸립니다)")

    def _stop(self) -> None:
        if self._monitor is not None:
            self._monitor.request_stop()
        self.btn_stop.configure(state=tk.DISABLED)

    def _open_config(self) -> None:
        try:
            os.startfile(str(self.config_path))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("열기 실패", str(exc))

    def _telegram_test(self) -> None:
        from app.alerts import TelegramNotifier

        self._refresh_telegram_label()
        settings = get_settings()
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
        latest: list[str] | None = None
        while True:
            try:
                latest = self._status_queue.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self._set_text(self.status_text, "\n".join(latest) or "데이터를 기다리는 중...")
        self.after(200, self._poll_queues)

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.configure(state=tk.DISABLED)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        if self._running and self._monitor is not None:
            self._monitor.request_stop()
            self.after(500, self.destroy)
        else:
            self.destroy()


def run_ui(config_path: str | Path | None = None) -> int:
    app = MonitorApp(Path(config_path) if config_path else None)
    app.mainloop()
    return 0
