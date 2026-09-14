"""The capture client's web app: one page of settings and a live status readout.

Deliberately small. This machine's job is to record and upload; the page exists
so the two values that cannot have a sensible default -- the server address and
this client's secret -- can be set from a browser instead of an editor over SSH.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from midi_memory.client.api import router as api_router
from midi_memory.client.capture import CaptureService
from midi_memory.client.config import Settings, get_settings
from midi_memory.client.settings_store import SettingsStore
from midi_memory.client.spool import Spool
from midi_memory.client.uploader import Uploader
from midi_memory.shared.auth import Auth, is_public

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def asset_url(path: str) -> str:
    relative = path.lstrip("/")
    try:
        stamp = int((BASE_DIR / "static" / relative).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{relative}?v={stamp}"


templates.env.globals["static"] = asset_url


def create_app(settings: Settings | None = None, *,
               start_capture: bool = True) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        # Apply saved overrides before anything reads the settings.
        app.state.settings_store = SettingsStore(settings)
        app.state.settings_store.load()
        app.state.spool = Spool(settings)
        app.state.auth = Auth(
            password=settings.password,
            secret_key=settings.resolve_secret_key(),
            max_age_days=settings.session_max_age_days,
        )
        app.state.uploader = Uploader(settings, app.state.spool)
        app.state.service = CaptureService(
            settings, app.state.spool, on_session=app.state.uploader.nudge,
        )
        # The uploader reports what the recorder is doing, so it can only be
        # wired up once both exist.
        app.state.uploader._status_source = app.state.service.status

        if start_capture:
            await app.state.service.start()
            await app.state.uploader.start()
            # Anything left from a previous run goes up straight away.
            app.state.uploader.nudge()
        try:
            yield
        finally:
            if start_capture:
                await app.state.uploader.stop()
                await app.state.service.stop()

    app = FastAPI(title="MIDI Memory Client", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        auth: Auth = request.app.state.auth
        if auth.enabled and not is_public(request.url.path):
            if not auth.is_authenticated(request):
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"detail": "Not authenticated"}, status_code=401)
                return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    app.include_router(api_router)

    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        return {"ok": True, "recording": request.app.state.service.recorder.is_recording}

    @app.get("/login")
    async def login_page(request: Request):
        if not request.app.state.auth.enabled or request.app.state.auth.is_authenticated(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": ""})

    @app.post("/login")
    async def login_submit(request: Request, password: str = Form("")):
        auth: Auth = request.app.state.auth
        if not auth.verify_password(password):
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "That password didn't match."}, status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        auth.issue(response)
        return response

    @app.get("/")
    async def settings_page(request: Request):
        return templates.TemplateResponse(
            request, "settings.html",
            {"linked": request.app.state.settings.linked},
        )

    return app


app = create_app()
