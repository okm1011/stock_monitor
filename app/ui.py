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
    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.config_path = config_path or default_config_path()
        self.title("Stock Monitor")
        self.geometry("900x760")
        self.minsize(780, 680)
        self.configure(bg=BG)

        self._monitor: Monitor | None = None
        self._thread: threading.Thread | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._status_queue: queue.Queue[tuple[dict, dict]] = queue.Queue()
        self._running = False

        self.var_poll = tk.StringVar()
        self.var_tf = tk.StringVar()
        self.var_rsi = tk.StringVar()
        self.var_macd = tk.StringVar()
        self.var_bb = tk.StringVar()
        self.var_atr_sl = tk.StringVar()
        self.var_atr_tp = tk.StringVar()
        self.var_cooldown = tk.StringVar()
        self.var_ext_hi = tk.StringVar()
        self.var_ext_lo = tk.StringVar()
        self.var_cross_os = tk.StringVar()
        self.var_cross_ob = tk.StringVar()
        self.var_sq = tk.StringVar()
        self.var_rule1 = tk.BooleanVar(value=True)
        self.var_rule2 = tk.BooleanVar(value=False)
        self.var_rule3 = tk.BooleanVar(value=False)
        self.var_rule4 = tk.BooleanVar(value=False)
        self.var_rule5 = tk.BooleanVar(value=True)
        self.var_rule6 = tk.BooleanVar(value=True)
        self.var_rule7 = tk.BooleanVar(value=True)
        self.var_vs_mult = tk.StringVar()
        self.var_vs_pct = tk.StringVar()
        self.var_vs_cd = tk.StringVar()

        self._build()
        self._load_into_form()
        self.after(200, self._poll_queues)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _card(self, parent: tk.Misc, **pack) -> tk.Frame:
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill=tk.X, **pack)
        shadow = tk.Frame(wrap, bg="#E8EAED")
        shadow.pack(fill=tk.X, padx=1, pady=(0, 2))
        card = tk.Frame(shadow, bg=CARD, padx=20, pady=16)
        card.pack(fill=tk.X)
        return card

    def _pill(self, parent: tk.Misc, text: str, fg: str, bg: str) -> tk.Label:
        return tk.Label(parent, text=text, font=FONT_SMALL, fg=fg, bg=bg, padx=10, pady=4)

    def _field(self, parent: tk.Misc, label: str, var: tk.StringVar, choices: list[str] | None = None) -> None:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text=label, font=FONT, fg=SUB, bg=CARD, width=22, anchor="w").pack(side=tk.LEFT)
        if choices:
            menu = tk.OptionMenu(row, var, *choices)
            menu.config(font=FONT, bg=INPUT_BG, fg=TEXT, highlightthickness=0, bd=0, relief=tk.FLAT, width=14)
            menu.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        else:
            entry = tk.Entry(
                row, textvariable=var, font=FONT, bg=INPUT_BG, fg=TEXT, relief=tk.FLAT,
                highlightthickness=1, highlightbackground=LINE, highlightcolor=BLUE, insertbackground=TEXT,
            )
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7)

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

    def _build(self) -> None:
        root = tk.Frame(self, bg=BG, padx=24, pady=18)
        root.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(root, bg=BG)
        header.pack(fill=tk.X, pady=(0, 12))
        left = tk.Frame(header, bg=BG)
        left.pack(side=tk.LEFT)
        tk.Label(left, text="Stock Monitor", font=FONT_TITLE, fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(left, text="1h 봉마감 · RSI/MACD/BB/다이버전스 규칙 엔진", font=FONT_SMALL, fg=SUB, bg=BG).pack(anchor="w")
        right = tk.Frame(header, bg=BG)
        right.pack(side=tk.RIGHT)
        self.tg_pill = self._pill(right, "Telegram", BLUE, "#E8F3FF")
        self.tg_pill.pack(side=tk.RIGHT, padx=(8, 0))
        self.state_pill = self._pill(right, "대기 중", SUB, LINE)
        self.state_pill.pack(side=tk.RIGHT)
        self._refresh_telegram_label()

        conf = self._card(root, pady=(0, 10))
        tk.Label(conf, text="공통 설정", font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        cols = tk.Frame(conf, bg=CARD)
        cols.pack(fill=tk.X, pady=(8, 0))
        c1 = tk.Frame(cols, bg=CARD)
        c2 = tk.Frame(cols, bg=CARD)
        c1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        c2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._field(c1, "폴링(초)", self.var_poll)
        self._field(c1, "봉 단위", self.var_tf, TIMEFRAMES)
        self._field(c1, "RSI period", self.var_rsi)
        self._field(c1, "MACD fast,slow,signal", self.var_macd)
        self._field(c2, "BB period,stddev", self.var_bb)
        self._field(c2, "ATR SL배수", self.var_atr_sl)
        self._field(c2, "ATR TP배수", self.var_atr_tp)
        self._field(c2, "알림 쿨다운(초)", self.var_cooldown)

        rules = self._card(root, pady=(0, 10))
        tk.Label(rules, text="알람 규칙 (상세 임계값은 config.yaml)", font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        toggles = tk.Frame(rules, bg=CARD)
        toggles.pack(fill=tk.X, pady=(8, 4))
        tk.Checkbutton(toggles, text="1 극단 RSI", variable=self.var_rule1, bg=CARD, fg=TEXT, activebackground=CARD).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(toggles, text="2 RSI+MACD", variable=self.var_rule2, bg=CARD, fg=TEXT, activebackground=CARD).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(toggles, text="3 다이버전스", variable=self.var_rule3, bg=CARD, fg=TEXT, activebackground=CARD).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(toggles, text="4 BB스퀴즈", variable=self.var_rule4, bg=CARD, fg=TEXT, activebackground=CARD).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(toggles, text="5 펌프초입", variable=self.var_rule5, bg=CARD, fg=TEXT, activebackground=CARD).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(toggles, text="알트OI", variable=self.var_rule6, bg=CARD, fg=TEXT, activebackground=CARD).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(toggles, text="매집봉", variable=self.var_rule7, bg=CARD, fg=TEXT, activebackground=CARD).pack(side=tk.LEFT)
        rcols = tk.Frame(rules, bg=CARD)
        rcols.pack(fill=tk.X)
        rc1 = tk.Frame(rcols, bg=CARD)
        rc2 = tk.Frame(rcols, bg=CARD)
        rc1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        rc2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._field(rc1, "극단 RSI high/low", self.var_ext_hi)
        self._field(rc1, "극단 RSI low", self.var_ext_lo)
        self._field(rc2, "크로스 oversold/ob", self.var_cross_os)
        self._field(rc2, "스퀴즈 비율", self.var_sq)
        self._field(rc1, "펌프 가격%(15분)", self.var_vs_pct)
        self._field(rc2, "펌프 거래량배수", self.var_vs_mult)
        self._field(rc2, "펌프 쿨다운(초)", self.var_vs_cd)

        actions = tk.Frame(root, bg=BG)
        actions.pack(fill=tk.X, pady=(0, 10))
        self.btn_start = self._btn(actions, "시작하기", self._start, primary=True)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = self._btn(actions, "중지", self._stop, danger=True)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_save = self._btn(actions, "설정 저장", self._save)
        self.btn_save.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_tg = self._btn(actions, "텔레그램 테스트", self._telegram_test)
        self.btn_tg.pack(side=tk.LEFT)

        status_card = self._card(root, pady=(0, 10))
        tk.Label(status_card, text="현재가 / RSI", font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        self.status_text = tk.Text(status_card, height=5, wrap=tk.WORD, font=("Consolas", 10), bg=INPUT_BG, fg=TEXT, relief=tk.FLAT, padx=12, pady=10, highlightthickness=0, state=tk.DISABLED)
        self.status_text.pack(fill=tk.X, pady=(8, 0))

        log_wrap = tk.Frame(root, bg=BG)
        log_wrap.pack(fill=tk.BOTH, expand=True)
        shadow = tk.Frame(log_wrap, bg="#E8EAED")
        shadow.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 2))
        log_card = tk.Frame(shadow, bg=CARD, padx=20, pady=16)
        log_card.pack(fill=tk.BOTH, expand=True)
        tk.Label(log_card, text="로그", font=FONT_B, fg=TEXT, bg=CARD).pack(anchor="w")
        body = tk.Frame(log_card, bg=CARD)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.log_text = tk.Text(body, wrap=tk.WORD, font=("Consolas", 9), bg=INPUT_BG, fg=TEXT, relief=tk.FLAT, padx=12, pady=10, highlightthickness=0, state=tk.DISABLED)
        scroll = tk.Scrollbar(body, command=self.log_text.yview)
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
        self.var_rsi.set(str(cfg.rsi.period))
        self.var_macd.set(f"{cfg.macd.fast},{cfg.macd.slow},{cfg.macd.signal}")
        self.var_bb.set(f"{cfg.bollinger.period},{cfg.bollinger.stddev}")
        self.var_atr_sl.set(str(cfg.atr.sl_mult))
        self.var_atr_tp.set(str(cfg.atr.tp_mult))
        self.var_cooldown.set(str(cfg.alert_cooldown_seconds))
        self.var_ext_hi.set(str(cfg.rules.extreme_rsi.high))
        self.var_ext_lo.set(str(cfg.rules.extreme_rsi.low))
        self.var_cross_os.set(f"{cfg.rules.rsi_macd_cross.oversold},{cfg.rules.rsi_macd_cross.overbought}")
        self.var_sq.set(str(cfg.rules.bb_squeeze.squeeze_ratio))
        self.var_rule1.set(cfg.rules.extreme_rsi.enabled)
        self.var_rule2.set(cfg.rules.rsi_macd_cross.enabled)
        self.var_rule3.set(cfg.rules.divergence.enabled)
        self.var_rule4.set(cfg.rules.bb_squeeze.enabled)
        self.var_rule5.set(cfg.rules.volume_spike.enabled)
        self.var_rule6.set(cfg.rules.accumulation.enabled)
        self.var_rule7.set(cfg.rules.absorption_bar.enabled)
        self.var_vs_mult.set(str(cfg.rules.volume_spike.volume_mult))
        self.var_vs_pct.set(str(cfg.rules.volume_spike.min_price_pct))
        self.var_vs_cd.set(str(cfg.rules.volume_spike.cooldown_seconds))
        self._append_log(f"설정 로드 · {self.config_path}")

    def _parse_csv_nums(self, text: str, n: int) -> list[float]:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) != n:
            raise ValueError(f"{n}개 숫자가 필요합니다: {text}")
        return [float(p) for p in parts]

    def _read_form_config(self) -> AppConfig:
        base = load_config(self.config_path)
        data = base.model_dump(mode="python")
        data["poll_interval_seconds"] = float(self.var_poll.get().strip())
        data["timeframe"] = self.var_tf.get().strip()
        data["signal_on_closed_bar"] = True
        data["rsi"] = {"period": int(float(self.var_rsi.get().strip()))}
        f, s, sig = self._parse_csv_nums(self.var_macd.get(), 3)
        data["macd"] = {"fast": int(f), "slow": int(s), "signal": int(sig)}
        bp, bs = self._parse_csv_nums(self.var_bb.get(), 2)
        data["bollinger"] = {"period": int(bp), "stddev": bs}
        data["atr"]["sl_mult"] = float(self.var_atr_sl.get().strip())
        data["atr"]["tp_mult"] = float(self.var_atr_tp.get().strip())
        data["alert_cooldown_seconds"] = int(float(self.var_cooldown.get().strip()))
        data["rules"]["extreme_rsi"]["enabled"] = bool(self.var_rule1.get())
        data["rules"]["extreme_rsi"]["high"] = float(self.var_ext_hi.get().strip())
        data["rules"]["extreme_rsi"]["low"] = float(self.var_ext_lo.get().strip())
        os_, ob_ = self._parse_csv_nums(self.var_cross_os.get(), 2)
        data["rules"]["rsi_macd_cross"]["enabled"] = bool(self.var_rule2.get())
        data["rules"]["rsi_macd_cross"]["oversold"] = os_
        data["rules"]["rsi_macd_cross"]["overbought"] = ob_
        data["rules"]["divergence"]["enabled"] = bool(self.var_rule3.get())
        data["rules"]["bb_squeeze"]["enabled"] = bool(self.var_rule4.get())
        data["rules"]["bb_squeeze"]["squeeze_ratio"] = float(self.var_sq.get().strip())
        data["rules"]["volume_spike"]["enabled"] = bool(self.var_rule5.get())
        data["rules"]["volume_spike"]["volume_mult"] = float(self.var_vs_mult.get().strip())
        data["rules"]["volume_spike"]["min_price_pct"] = float(self.var_vs_pct.get().strip())
        data["rules"]["volume_spike"]["cooldown_seconds"] = int(float(self.var_vs_cd.get().strip()))
        data["rules"]["accumulation"]["enabled"] = bool(self.var_rule6.get())
        data["rules"]["absorption_bar"]["enabled"] = bool(self.var_rule7.get())
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
        for key, price in list(prices.items())[:20]:
            rsi = rsis.get(key)
            rsi_s = f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "n/a"
            symbol = key.split(":", 1)[-1]
            if isinstance(price, float):
                price_s = f"{price:,.2f}" if abs(price) >= 1000 else (f"{price:,.4f}" if abs(price) >= 1 else f"{price:.6f}")
            else:
                price_s = str(price)
            lines.append(f"{symbol:<12}  {price_s:>14}   RSI {rsi_s}")
        if len(prices) > 20:
            lines.append(f"... +{len(prices)-20} more")
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
            self.after(500, self.destroy)
        else:
            self.destroy()


def run_ui(config_path: str | Path | None = None) -> int:
    app = MonitorApp(Path(config_path) if config_path else None)
    app.mainloop()
    return 0
