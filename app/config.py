"""Cấu hình chung, đọc từ file .env ở thư mục gốc dự án."""
import os
import secrets
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Console Windows tiếng Nhật (cp932) không in được tiếng Việt -> thay ký tự thay vì báo lỗi
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="backslashreplace")
    except Exception:  # noqa: BLE001
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
# Biến môi trường đã đặt sẵn (vd do bộ test, Docker, dịch vụ Windows) được ƯU TIÊN hơn file .env.
# DOTENV_DISABLED=1: bỏ qua hẳn file .env (bộ test dùng để không gửi mail thật, không đụng CSDL thật).
if os.getenv("DOTENV_DISABLED", "").lower() not in ("1", "true", "yes"):
    load_dotenv(BASE_DIR / ".env", override=False)

DATA_DIR = Path(os.getenv("DATA_DIR") or BASE_DIR / "data").resolve()
FILES_DIR = DATA_DIR / "files"
FILES_DIR.mkdir(parents=True, exist_ok=True)

# Mặc định SQLite (1 file, không cần cài gì). Dùng PostgreSQL: đặt DATABASE_URL trong .env
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"

MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_FILES_PER_UPLOAD = int(os.getenv("MAX_FILES_PER_UPLOAD", "10"))
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "10"))
# Chỉ bật khi chạy sau HTTPS, nếu không trình duyệt sẽ không gửi cookie đăng nhập
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY")
    if key:
        return key
    f = DATA_DIR / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return f.read_text(encoding="utf-8").strip()


SECRET_KEY = _secret_key()

# ---- Vòng lặp AI (worker) ----
WORKER_ENABLED = os.getenv("WORKER_ENABLED", "true").lower() != "false"
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "3"))
SELF_CHECK_REREADS = int(os.getenv("SELF_CHECK_REREADS", "1"))   # số lần AI tự đọc lại khi nghi sai
MAX_TRANSIENT_ATTEMPTS = int(os.getenv("MAX_TRANSIENT_ATTEMPTS", "5"))  # Gemini quá tải: thử lại bấy nhiêu vòng

# ---- Gửi mail ----
# Để trống SMTP_HOST: mail được lưu thành file .eml trong data/outbox (để thử, không gửi thật)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls").lower()  # starttls | ssl | none
MAIL_FROM = os.getenv("MAIL_FROM", "").strip() or SMTP_USER
OUTBOX_DIR = DATA_DIR / "outbox"
# Địa chỉ web mà người quản lý mở được từ máy của họ (dùng cho nút Xác nhận trong mail),
# vd http://192.168.1.25:8000. Để trống: lấy theo địa chỉ người gửi đang dùng.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
CONFIRM_LINK_DAYS = int(os.getenv("CONFIRM_LINK_DAYS", "30"))
