"""Dùng thư mục dữ liệu + CSDL tạm cho test, không đụng dữ liệu thật."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="dntt_test_")
# Không đọc file .env của máy dev: tránh gửi mail thật, tạo admin, ghi vào CSDL thật (BUG-02)
os.environ["DOTENV_DISABLED"] = "1"
os.environ["DATA_DIR"] = _tmp
# Chạy test trên MySQL: đặt TEST_DATABASE_URL=mysql+pymysql://user:pass@host/db_test (CSDL sẽ bị xóa sạch!)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["WORKER_ENABLED"] = "false"  # test tự gọi worker.run_once()
os.environ["PUBLIC_BASE_URL"] = "http://dntt.local"
# Đặt rỗng (không chỉ xóa) để dù .env có được nạp với override=False cũng không lấy lại giá trị thật
for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "GEMINI_API_KEY", "GEMINI_FALLBACK_MODEL",
          "ADMIN_USERNAME", "ADMIN_PASSWORD", "SO_DE_NGHI_FORMAT", "SO_DE_NGHI_RESET"):
    os.environ[k] = ""

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401

# Chốt an toàn: fixture bên dưới drop_all() -> tuyệt đối không được chạy trên CSDL thật
assert config.DATABASE_URL == os.environ["DATABASE_URL"], "Bộ test không dùng CSDL test, dừng lại."
assert str(config.DATA_DIR).startswith(os.path.realpath(_tmp)) or str(config.DATA_DIR) == str(_tmp)
assert not config.SMTP_HOST, "Bộ test không được gửi mail thật."


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
