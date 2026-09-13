"""FastAPI application: serves the web UI and owns the capture service's lifetime."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import sessions as sessions_api
from app.api import settings as settings_api
from app.api import status as status_api
from app.api import tags as tags_api
from app.auth import Auth, is_public
from app.config import Settings, get_settings
from app.db import Database
from app.events import EventBus
from app.settings_store import SettingsStore
from app.service import CaptureService

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def asset_url(path: str) -> str:
    """Static URL stamped with the file's mtime.

    Without this, updating the app on the Pi leaves browsers serving the CSS and
    JS they cached weeks ago, and the new version looks broken for no reason.
    """
    relative = path.lstrip("/")
    try:
        stamp = int((BASE_DIR / "static" / relative).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{relative}?v={stamp}"


templates.env.globals["static"] = asset_url


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        # Apply saved runtime overrides before anything reads the settings.
        app.state.settings_store = SettingsStore(settings)
        app.state.settings_store.load()
        app.state.db = Database(settings.db_path)
        app.state.bus = EventBus()
        app.state.auth = Auth(
            password=settings.password,
            secret_key=settings.resolve_secret_key(),
            max_age_days=settings.session_max_age_days,
        )
        app.state.service = CaptureService(settings, app.state.db, app.state.bus)
        await app.state.service.start()
        try:
            yield
        finally:
            await app.state.service.stop()

    app = FastAPI(title="MIDI Memory", lifespan=lifespan, docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        auth: Auth = request.app.state.auth
        if auth.enabled and not is_public(request.url.path):
            if not auth.is_authenticated(request):
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"detail": "Not authenticated"}, status_code=401)
                target = request.url.path
                if request.url.query:
                    target += "?" + request.url.query
                return RedirectResponse(f"/login?next={target}", status_code=303)
        return await call_next(request)

    app.include_router(sessions_api.router)
    app.include_router(settings_api.router)
    app.include_router(tags_api.router)
    app.include_router(status_api.router)

    # -- pages ---------------------------------------------------------------
    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        return {"ok": True, "recording": request.app.state.service.recorder.is_recording}

    @app.get("/login")
    async def login_page(request: Request, next: str = "/"):
        if not request.app.state.auth.enabled or request.app.state.auth.is_authenticated(request):
            return RedirectResponse(next or "/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"next": next, "error": ""})

    @app.post("/login")
    async def login_submit(request: Request, password: str = Form(""),
                           next: str = Form("/")):
        auth: Auth = request.app.state.auth
        if not auth.verify_password(password):
            return templates.TemplateResponse(
                request, "login.html",
                {"next": next, "error": "That password didn't match."},
                status_code=401,
            )
        response = RedirectResponse(_safe_next(next), status_code=303)
        auth.issue(response)
        return response

    @app.get("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("midi_memory_session")
        return response

    @app.get("/")
    async def library(request: Request):
        return templates.TemplateResponse(
            request, "library.html",
            {"tags": request.app.state.db.list_tags(),
             "stats": request.app.state.db.stats()},
        )

    @app.get("/sessions/{session_id}")
    async def session_page(request: Request, session_id: str):
        session = request.app.state.db.get_session(session_id)
        if session is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request, "session.html",
            {"session": session, "all_tags": request.app.state.db.list_tags()},
        )

    return app


def _safe_next(target: str) -> str:
    """Only ever redirect within this app, never to an attacker-supplied host."""
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


app = create_app()
