from __future__ import annotations

from threading import Lock

import httpx

from app.settings import Settings, get_settings


# 텔레그램 메시지 최대 4096자
TELEGRAM_MAX_CHARS = 3800


class Notifier:
    enabled = False

    def send(self, message: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class NullNotifier(Notifier):
    def send(self, message: str) -> None:
        pass


class TelegramNotifier(Notifier):
    enabled = True

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 15.0) -> None:
        self.bot_token = bot_token.strip()
        self.chat_id = str(chat_id).strip()
        self._url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self._http = httpx.Client(timeout=timeout)
        self._lock = Lock()

    def send(self, message: str) -> None:
        with self._lock:
            resp = self._http.post(
                self._url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code >= 400:
                detail = resp.text[:300]
                raise RuntimeError(f"Telegram send failed ({resp.status_code}): {detail}")

    def close(self) -> None:
        with self._lock:
            self._http.close()


def build_notifier(settings: Settings | None = None) -> Notifier:
    settings = settings or get_settings()
    if settings.telegram_enabled:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return NullNotifier()


def batch_messages(messages: list[str], max_chars: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """여러 알람을 빈 줄로 이어 붙여 텔레그램 전송 횟수를 줄임."""
    out: list[str] = []
    cur = ""
    for m in messages:
        if cur and len(cur) + 2 + len(m) > max_chars:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{m}" if cur else m
    if cur:
        out.append(cur)
    return out
