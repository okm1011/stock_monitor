from __future__ import annotations

import uvicorn

from app.settings import get_settings
from app.web.app import create_app


def main() -> None:
    settings = get_settings()
    if not settings.config_web_enabled:
        raise SystemExit(
            "CONFIG_WEB_PASSWORD / CONFIG_WEB_SECRET 를 .env 에 설정하세요."
        )
    uvicorn.run(
        create_app(),
        host=settings.config_web_host,
        port=int(settings.config_web_port),
        log_level="info",
    )


if __name__ == "__main__":
    main()
