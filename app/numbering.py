"""
Sinh số đề nghị tự tăng, ví dụ: 01-2809/ĐN-2026
  01   = số thứ tự phiếu (mặc định đếm lại từ 01 mỗi ngày)
  2809 = ngày + tháng lúc tạo phiếu
  2026 = năm lúc tạo phiếu

Cấu hình trong .env (không bắt buộc):
  SO_DE_NGHI_FORMAT={stt:02d}-{dd}{mm}/ĐN-{yyyy}
  SO_DE_NGHI_RESET=day        # day | month | year | never
Bộ đếm lưu trong bảng `counters` của CSDL.
"""
import os
import threading
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError

from .config import TIMEZONE
from .db import SessionLocal
from .models import Counter

DEFAULT_FORMAT = "{stt:02d}-{dd}{mm}/ĐN-{yyyy}"
_lock = threading.Lock()  # SQLite không có SELECT ... FOR UPDATE


def _period(today: date, reset: str) -> str:
    return {"day": today.isoformat(), "month": f"{today:%Y-%m}",
            "year": str(today.year)}.get(reset, "all")


def _increment(key: str) -> int:
    for _ in range(3):
        with SessionLocal() as s:
            try:
                with s.begin():
                    row = s.get(Counter, key, with_for_update=True)
                    if row is None:
                        row = Counter(key=key, value=1)
                        s.add(row)
                    else:
                        row.value += 1
                    return row.value
            except IntegrityError:  # 2 tiến trình cùng tạo dòng mới -> thử lại
                continue
    raise RuntimeError("Không cấp được số đề nghị, thử lại sau.")


def next_so_de_nghi(today: date | None = None) -> str:
    today = today or datetime.now(TIMEZONE).date()
    reset = (os.getenv("SO_DE_NGHI_RESET") or "day").lower()
    fmt = os.getenv("SO_DE_NGHI_FORMAT") or DEFAULT_FORMAT
    with _lock:
        stt = _increment(f"so_de_nghi:{_period(today, reset)}")
    return fmt.format(stt=stt, dd=f"{today.day:02d}", mm=f"{today.month:02d}",
                      yyyy=today.year, yy=f"{today.year % 100:02d}")
