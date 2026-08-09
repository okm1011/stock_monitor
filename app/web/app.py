from __future__ import annotations

import hmac
import secrets
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import AppConfig, default_config_path, load_config, save_config
from app.settings import get_settings
from app.timeframes import TIMEFRAME_SECONDS


TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SERVICE_NAME = "stock-monitor"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Stock Monitor Config", docs_url=None, redoc_url=None)
    secret = settings.config_web_secret.strip() or secrets.token_hex(32)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="sm_cfg_session",
        max_age=60 * 60 * 12,
        same_site="lax",
        https_only=False,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        if not _logged_in(request):
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/config", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if _logged_in(request):
            return RedirectResponse("/config", status_code=303)
        if not settings.config_web_enabled:
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {
                    "error": ".env 에 CONFIG_WEB_PASSWORD / CONFIG_WEB_SECRET 를 설정하세요.",
                    "disabled": True,
                },
            )
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": None, "disabled": False})

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        if not settings.config_web_enabled:
            return RedirectResponse("/login", status_code=303)
        expected = settings.config_web_password
        if not hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8")):
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {"error": "비밀번호가 틀렸습니다.", "disabled": False},
                status_code=401,
            )
        request.session["auth"] = True
        return RedirectResponse("/config", status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request, msg: str | None = None, err: str | None = None):
        if not _logged_in(request):
            return RedirectResponse("/login", status_code=303)
        cfg = load_config()
        return TEMPLATES.TemplateResponse(
            request,
            "config.html",
            {
                "cfg": cfg,
                "timeframes": list(TIMEFRAME_SECONDS.keys()),
                "msg": msg,
                "err": err,
                "status": _service_status(),
            },
        )

    @app.post("/config")
    async def save(
        request: Request,
        poll_interval_seconds: float = Form(...),
        timeframe: str = Form(...),
        alert_cooldown_seconds: int = Form(...),
        status_log_seconds: float = Form(...),
        rsi_period: int = Form(...),
        extreme_rsi_enabled: str | None = Form(None),
        extreme_rsi_high: float = Form(...),
        extreme_rsi_low: float = Form(...),
        extreme_rsi_live: str | None = Form(None),
        rsi_macd_enabled: str | None = Form(None),
        divergence_enabled: str | None = Form(None),
        bb_squeeze_enabled: str | None = Form(None),
        volume_spike_enabled: str | None = Form(None),
        vs_min_price_pct: float = Form(...),
        vs_volume_mult: float = Form(...),
        vs_quiet_range_pct: float = Form(...),
        vs_poll_seconds: float = Form(...),
        vs_cooldown_seconds: int = Form(...),
        vs_window_bars: int = Form(...),
        vs_volume_lookback: int = Form(...),
        vs_quiet_bars: int = Form(...),
        universe_top_percentile: float = Form(...),
        universe_max_symbols: int = Form(...),
        include_static_stocks: str | None = Form(None),
        restart: str | None = Form(None),
    ):
        if not _logged_in(request):
            return RedirectResponse("/login", status_code=303)
        try:
            cfg = load_config()
            data = cfg.model_dump(mode="python")
            data["poll_interval_seconds"] = float(poll_interval_seconds)
            data["timeframe"] = timeframe.strip()
            data["alert_cooldown_seconds"] = int(alert_cooldown_seconds)
            data["status_log_seconds"] = float(status_log_seconds)
            data["rsi"]["period"] = int(rsi_period)
            data["rules"]["extreme_rsi"]["enabled"] = extreme_rsi_enabled == "on"
            data["rules"]["extreme_rsi"]["high"] = float(extreme_rsi_high)
            data["rules"]["extreme_rsi"]["low"] = float(extreme_rsi_low)
            data["rules"]["extreme_rsi"]["live"] = extreme_rsi_live == "on"
            data["rules"]["rsi_macd_cross"]["enabled"] = rsi_macd_enabled == "on"
            data["rules"]["divergence"]["enabled"] = divergence_enabled == "on"
            data["rules"]["bb_squeeze"]["enabled"] = bb_squeeze_enabled == "on"
            data["rules"]["volume_spike"]["enabled"] = volume_spike_enabled == "on"
            data["rules"]["volume_spike"]["min_price_pct"] = float(vs_min_price_pct)
            data["rules"]["volume_spike"]["volume_mult"] = float(vs_volume_mult)
            data["rules"]["volume_spike"]["quiet_range_pct"] = float(vs_quiet_range_pct)
            data["rules"]["volume_spike"]["poll_seconds"] = float(vs_poll_seconds)
            data["rules"]["volume_spike"]["cooldown_seconds"] = int(vs_cooldown_seconds)
            data["rules"]["volume_spike"]["window_bars"] = int(vs_window_bars)
            data["rules"]["volume_spike"]["volume_lookback"] = int(vs_volume_lookback)
            data["rules"]["volume_spike"]["quiet_bars"] = int(vs_quiet_bars)
            data["universe"]["top_percentile"] = float(universe_top_percentile)
            data["universe"]["max_symbols"] = int(universe_max_symbols)
            data["universe"]["include_static_stocks"] = include_static_stocks == "on"

            new_cfg = AppConfig.model_validate(data)
            path = default_config_path()
            bak = path.with_suffix(".yaml.bak")
            if path.exists():
                bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            save_config(new_cfg, path)

            msg = "설정 저장 완료 · 모니터가 수 초 내 자동 반영"
            if restart == "1":
                ok, detail = _restart_monitor()
                msg = f"설정 저장 완료 · 모니터 재시작 {'OK' if ok else '실패: ' + detail}"
            return RedirectResponse(f"/config?msg={_q(msg)}", status_code=303)
        except Exception as exc:
            return RedirectResponse(f"/config?err={_q(str(exc))}", status_code=303)

    @app.post("/restart")
    def restart(request: Request):
        if not _logged_in(request):
            return RedirectResponse("/login", status_code=303)
        ok, detail = _restart_monitor()
        if ok:
            return RedirectResponse(f"/config?msg={_q('모니터 재시작 OK')}", status_code=303)
        return RedirectResponse(f"/config?err={_q('재시작 실패: ' + detail)}", status_code=303)

    return app


def _logged_in(request: Request) -> bool:
    return bool(request.session.get("auth"))


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


def _restart_monitor() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "unknown").strip()
            return False, err[:300]
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _service_status() -> str:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (r.stdout or r.stderr or "unknown").strip()
    except Exception:
        return "n/a"


app = create_app()
