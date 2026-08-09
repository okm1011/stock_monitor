from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import project_root


class Settings(BaseSettings):
    """환경변수 / .env 설정."""

    model_config = SettingsConfigDict(
        env_file=str(project_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 설정 웹 (모바일/PC에서 config.yaml 수정)
    config_web_password: str = ""
    config_web_secret: str = ""
    config_web_host: str = "0.0.0.0"
    config_web_port: int = 8080

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token.strip() and self.telegram_chat_id.strip())

    @property
    def config_web_enabled(self) -> bool:
        return bool(self.config_web_password.strip() and self.config_web_secret.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
