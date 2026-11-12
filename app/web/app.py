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

from app.config import (
    MA_LINES,
    MA_TYPE_LABEL,
    AppConfig,
    default_config_path,
    load_config,
    save_config,
)
from app.settings import get_settings
from app.timeframes import TF_LABEL, TIMEFRAMES


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
                "timeframes": TIMEFRAMES,
                "tf_label": TF_LABEL,
                "ma_lines": MA_LINES,
                "ma_types": MA_TYPE_LABEL,
                "macro_text": "\n".join(f"{a.ticker}, {a.name}" for a in cfg.macro.assets),
                "exclude_text": ", ".join(cfg.universe.exclude_bases),
                "msg": msg,
                "err": err,
                "status": _service_status(),
            },
        )

    @app.post("/config")
    async def save(request: Request):
        if not _logged_in(request):
            return RedirectResponse("/login", status_code=303)
        try:
            form = await request.form()
            data = _apply_form(load_config().model_dump(mode="python"), form)
            new_cfg = AppConfig.model_validate(data)
            path = default_config_path()
            bak = path.with_suffix(".yaml.bak")
            if path.exists():
                bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            save_config(new_cfg, path)

            msg = "설정 저장 완료 · 모니터가 수 초 내 자동 반영"
            if form.get("restart") == "1":
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


def _apply_form(data: dict[str, Any], form) -> dict[str, Any]:
    def on(name: str) -> bool:
        return form.get(name) == "on"

    def num(name: str) -> float:
        raw = str(form.get(name, "")).strip()
        if raw == "":
            raise ValueError(f"값이 비어 있습니다: {name}")
        return float(raw)

    data["poll_seconds"] = num("poll_seconds")
    data["status_log_seconds"] = num("status_log_seconds")

    u = data["universe"]
    u["enabled"] = on("universe_enabled")
    u["top_n"] = int(num("universe_top_n"))
    u["include_tradfi"] = on("universe_include_tradfi")
    u["refresh_hours"] = num("universe_refresh_hours")
    u["exclude_bases"] = [
        s.strip().upper()
        for s in str(form.get("universe_exclude", "")).replace("\n", ",").split(",")
        if s.strip()
    ]

    m = data["macro"]
    m["enabled"] = on("macro_enabled")
    m["poll_seconds"] = num("macro_poll_seconds")
    m["assets"] = _parse_macro(str(form.get("macro_assets", "")))

    r = data["rsi_alert"]
    r["enabled"] = on("rsi_enabled")
    r["period"] = int(num("rsi_period"))
    r["rearm_gap"] = num("rsi_rearm_gap")
    for tf in TIMEFRAMES:
        t = r[f"tf_{tf}"]
        t["enabled"] = on(f"rsi_{tf}_enabled")
        t["overbought_enabled"] = on(f"rsi_{tf}_ob_on")
        t["overbought"] = num(f"rsi_{tf}_ob")
        t["oversold_enabled"] = on(f"rsi_{tf}_os_on")
        t["oversold"] = num(f"rsi_{tf}_os")

    ma = data["ma_alert"]
    ma["enabled"] = on("ma_enabled")
    ma["rearm_mult"] = num("ma_rearm_mult")
    for key, _period in MA_LINES:
        line = ma[key]
        line["enabled"] = on(f"ma_{key}_enabled")
        line["type"] = str(form.get(f"ma_{key}_type", line.get("type", "ema"))).strip()
        for tf in TIMEFRAMES:
            t = line[f"tf_{tf}"]
            t["enabled"] = on(f"ma_{key}_{tf}_enabled")
            t["proximity_pct"] = num(f"ma_{key}_{tf}_pct")
            t["from_above"] = on(f"ma_{key}_{tf}_above")
            t["from_below"] = on(f"ma_{key}_{tf}_below")
    return data


def _parse_macro(text: str) -> list[dict[str, str]]:
    """한 줄에 '티커, 이름'. 이름 생략 시 티커를 이름으로."""
    out: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ticker, _, name = line.partition(",")
        ticker = ticker.strip()
        if not ticker:
            continue
        out.append({"ticker": ticker, "name": name.strip() or ticker})
    return out


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
