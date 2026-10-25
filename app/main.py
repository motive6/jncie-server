import threading
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR
from .db import init_db
from .routers import audit, auth, dashboard, dns, ftp, network, ntp, radius, snmp, syslog, users
from .security import AuthRequired, PermissionDenied, read_session
from .services.snmpmon import poll_loop
from .services.sysmon import sysmon_loop
from .web import render

POST_ALLOWED_FOR_VIEWER = {"/login", "/logout", "/users/password"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = threading.Event()
    threading.Thread(target=poll_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=sysmon_loop, args=(stop,), daemon=True).start()
    try:
        yield
    finally:
        stop.set()


app = FastAPI(
    title="SVC Admin",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.middleware("http")
async def readonly_guard(request: Request, call_next):
    if request.method == "POST" and request.url.path not in POST_ALLOWED_FOR_VIEWER:
        session = read_session(request)
        if session and session.get("r", "admin") != "admin":
            return RedirectResponse("/?err=" + quote("只读账号不能修改配置"), status_code=303)
    return await call_next(request)


@app.exception_handler(AuthRequired)
async def auth_required_handler(request: Request, exc: AuthRequired):
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse("/login?next=" + quote(target), status_code=303)


@app.exception_handler(PermissionDenied)
async def permission_denied_handler(request: Request, exc: PermissionDenied):
    response = render(request, "denied.html", active="")
    response.status_code = 403
    return response


@app.get("/healthz")
async def healthz():
    return {"ok": True}


for module in (auth, dashboard, dns, ftp, radius, snmp, syslog, ntp, users, network, audit):
    app.include_router(module.router)
