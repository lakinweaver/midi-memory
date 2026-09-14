"""FastAPI application: the library web UI and the door capture clients come in by.

The server never touches MIDI hardware. Recordings arrive over HTTP from clients,
which is the whole point of the split -- the machine holding the library does not
have to be the machine next to the piano.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from midi_memory import __author__, __copyright__, __license__, __version__
from midi_memory.server import samples
from midi_memory.server.api import clients as clients_api
from midi_memory.server.api import ingest as ingest_api
from midi_memory.server.api import sessions as sessions_api
from midi_memory.server.api import settings as settings_api
from midi_memory.server.api import status as status_api
from midi_memory.server.api import tags as tags_api
from midi_memory.server.clients import SWEEP_SECONDS, ClientRegistry
from midi_memory.server.config import Settings, get_settings
from midi_memory.server.db import Database
from midi_memory.server.events import EventBus
from midi_memory.shared.auth import Auth, is_public

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def asset_url(path: str) -> str:
    """Static URL stamped with the file's mtime.

    Without this, updating the app leaves browsers serving the CSS and JS they
    cached weeks ago, and the new version looks broken for no reason.
    """
    relative = path.lstrip("/")
    try:
        stamp = int((BASE_DIR / "static" / relative).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{relative}?v={stamp}"


templates.env.globals["static"] = asset_url
# Every page shows the version; the settings dialog shows the rest.
templates.env.globals["app_version"] = __version__
templates.env.globals["app_author"] = __author__
templates.env.globals["app_license"] = __license__
templates.env.globals["app_copyright"] = __copyright__


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        samples.ensure_manifest()
        app.state.db = Database(settings.db_path, settings.zone)
        app.state.bus = EventBus()
        app.state.auth = Auth(
            password=settings.password,
            secret_key=settings.resolve_secret_key(),
            max_age_days=settings.session_max_age_days,
        )
        app.state.clients = ClientRegistry(app.state.db, app.state.bus)
        sweeper = asyncio.create_task(_sweep_clients(app), name="client-sweep")
        try:
            yield
        finally:
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)

    app = FastAPI(title="MIDI Memory", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None)

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
    app.include_router(clients_api.router)
    app.include_router(ingest_api.router)

    # -- pages ---------------------------------------------------------------
    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        return {"ok": True, "clients": request.app.state.clients.count()}

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
             "clients": request.app.state.clients.listing(),
             "stats": request.app.state.db.stats()},
        )

    @app.get("/sessions/{session_id}")
    async def session_page(request: Request, session_id: str):
        session = request.app.state.db.get_session(session_id)
        if session is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request, "session.html",
            {"session": session,
             "all_tags": request.app.state.db.list_tags(),
             "clients": request.app.state.clients.listing()},
        )

    return app


async def _sweep_clients(app: FastAPI) -> None:
    """Notice when a client stops reporting, so the UI does not show it as live."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            app.state.clients.sweep()
        except Exception:
            log.exception("Client status sweep failed")


def _safe_next(target: str) -> str:
    """Only ever redirect within this app, never to an attacker-supplied host."""
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


app = create_app()
