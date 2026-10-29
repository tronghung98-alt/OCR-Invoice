"""
Invoice to Excel - web nội bộ lập giấy đề nghị thanh toán từ hóa đơn.

Chạy từ thư mục gốc dự án:
    uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config  # noqa: F401  (nạp .env trước tiên)
from . import models  # noqa: F401
from .api import router as api_router
from .config import BASE_DIR, COOKIE_SECURE, SECRET_KEY, SESSION_HOURS
from .migrate import upgrade
from .security import LoginRequired
from .services import on_startup
from .worker import worker
from .web import render, router as web_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app):
    upgrade()      # tạo bảng mới / thêm cột mới, giữ nguyên dữ liệu cũ
    on_startup()
    if config.WORKER_ENABLED:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="Giấy đề nghị thanh toán", version="4.0.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware, secret_key=SECRET_KEY, session_cookie="dntt_session",
    max_age=SESSION_HOURS * 3600, same_site="lax", https_only=COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
app.include_router(web_router)
app.include_router(api_router)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, _exc):
    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(nxt)}", 303)


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api") or request.url.path.endswith("/trang-thai"):
        from fastapi.exception_handlers import http_exception_handler
        return await http_exception_handler(request, exc)
    try:
        return render(request, "error.html", None, status_code=exc.status_code,
                      code=exc.status_code, message=exc.detail)
    except Exception:  # noqa: BLE001
        return PlainTextResponse(str(exc.detail), exc.status_code)


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
