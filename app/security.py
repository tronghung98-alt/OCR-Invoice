"""Mật khẩu (scrypt), đăng nhập theo session cookie, CSRF, chống dò mật khẩu."""
import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(h)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                              n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:  # noqa: BLE001
        return False


USERNAME_RE = re.compile(r"^[a-z0-9._-]{1,64}$")


def validate_username(username: str) -> str | None:
    """Tên đăng nhập (đã .strip().lower()): chỉ chữ không dấu, số, dấu chấm, gạch dưới, gạch ngang."""
    if not USERNAME_RE.fullmatch(username or ""):
        return "Tên đăng nhập chỉ gồm chữ không dấu, số, dấu chấm, gạch dưới hoặc gạch ngang (tối đa 64 ký tự)."
    return None


def validate_new_password(pw: str) -> str | None:
    if len(pw) < 8:
        return "Mật khẩu cần ít nhất 8 ký tự."
    return None


# ---- chống dò mật khẩu: sai 5 lần -> khóa 5 phút theo (IP, tên đăng nhập) ----
_fails: dict[tuple[str, str], tuple[int, float]] = {}
_fails_lock = threading.Lock()
MAX_FAILS, LOCK_SECONDS = 5, 300


def login_locked(ip: str, username: str) -> int:
    with _fails_lock:
        n, until = _fails.get((ip, username.lower()), (0, 0))
        return max(0, int(until - time.time())) if n >= MAX_FAILS else 0


def record_login(ip: str, username: str, ok: bool):
    key = (ip, username.lower())
    with _fails_lock:
        if ok:
            _fails.pop(key, None)
            return
        now = time.time()
        n, until = _fails.get(key, (0, 0))
        if n >= MAX_FAILS and until <= now:
            n = 0  # đã hết thời gian khóa -> có lại đủ MAX_FAILS lần thử
        n += 1
        _fails[key] = (n, now + LOCK_SECONDS)
        if len(_fails) > 10_000:  # không để bộ nhớ phình mãi
            for k in [k for k, (_, u) in _fails.items() if u <= now]:
                _fails.pop(k, None)


# ---- CSRF ----
def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = request.session["csrf"] = secrets.token_urlsafe(32)
    return tok


def check_csrf(request: Request, token: str | None):
    if not token or not hmac.compare_digest(token, request.session.get("csrf", "")):
        raise HTTPException(400, "Phiên làm việc đã hết hạn. Tải lại trang rồi thử lại.")


# ---- người dùng hiện tại ----
class LoginRequired(Exception):
    """Ném ra ở trang web -> chuyển tới /login."""


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = db.get(User, uid)
    if not user or not user.is_active:
        request.session.clear()
        return None
    return user


def require_user(user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        raise LoginRequired()
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Chỉ quản trị viên được dùng chức năng này.")
    return user


def require_api_user(user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        raise HTTPException(401, "Chưa đăng nhập.")
    return user
