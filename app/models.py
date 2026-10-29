"""Các bảng CSDL."""
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


def fold(text) -> str:
    """Chuẩn hóa để tìm kiếm: chữ thường, bỏ dấu tiếng Việt. 'Công ty ĐIỆN LỰC' -> 'cong ty dien luc'.
    (LIKE/ILIKE của SQLite chỉ không phân biệt hoa/thường với chữ ASCII.)"""
    t = unicodedata.normalize("NFD", str(text or "").casefold())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", t.replace("đ", "d"))


# Trạng thái phiếu, theo đúng sơ đồ quy trình
QUEUED = "queued"            # 1. Nhận hóa đơn - chờ AI nhận
PROCESSING = "processing"    # 2-4. AI đang đọc / xuất Excel / tự kiểm tra
NEEDS_HUMAN = "needs_human"  # Ảnh mờ, không đọc được -> trả lại cho con người
REVIEW = "review"            # 5. Chờ người xem xét (xem và sửa trực tiếp)
SENT = "sent"                # Đã xác nhận & gửi mail, chờ quản lý xác nhận
CONFIRMED = "confirmed"      # Quản lý đã bấm Xác nhận -> Đã xác nhận đọc

STATUS_LABELS = {
    QUEUED: "Chờ AI đọc", PROCESSING: "AI đang đọc", NEEDS_HUMAN: "Cần người xử lý",
    REVIEW: "Chờ xem xét", SENT: "Chờ quản lý xác nhận", CONFIRMED: "Đã xác nhận đọc",
}
EDITABLE = (NEEDS_HUMAN, REVIEW)      # người dùng được sửa trực tiếp
REREADABLE = (NEEDS_HUMAN, REVIEW)    # được bấm "AI đọc lại" (B)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(128), default="")
    department: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str | None] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_manager: Mapped[bool] = mapped_column(Boolean, default=False)  # nhận mail xác nhận
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requests: Mapped[list["PaymentRequest"]] = relationship(
        back_populates="user", foreign_keys="PaymentRequest.user_id")

    @property
    def label(self) -> str:
        return self.full_name or self.username


class PaymentRequest(Base):
    """Một giấy đề nghị thanh toán tạo từ một hóa đơn."""
    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=QUEUED, index=True)
    error: Mapped[str | None] = mapped_column(Text)  # lý do cần người xử lý / lỗi tạm thời

    so_de_nghi: Mapped[str | None] = mapped_column(String(64), index=True)
    so_hoa_don: Mapped[str | None] = mapped_column(String(64), index=True)
    ngay_hoa_don: Mapped[str | None] = mapped_column(String(32))
    ben_ban: Mapped[str | None] = mapped_column(String(255))
    tong_tien: Mapped[float | None] = mapped_column(Float)

    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(64))
    original_path: Mapped[str] = mapped_column(String(512))
    template_path: Mapped[str | None] = mapped_column(String(512))
    output_path: Mapped[str | None] = mapped_column(String(512))

    data: Mapped[dict | None] = mapped_column(JSON)       # dữ liệu hóa đơn (AI đọc hoặc người sửa)
    extra: Mapped[dict | None] = mapped_column(JSON)      # thông tin người dùng nhập
    warnings: Mapped[list | None] = mapped_column(JSON)   # kết quả tự kiểm tra + cảnh báo mẫu

    # vòng lặp AI
    attempts: Mapped[int] = mapped_column(Integer, default=0)        # số vòng lỗi tạm thời liên tiếp
    ai_reads: Mapped[int] = mapped_column(Integer, default=0)        # tổng số lần AI đọc
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by_human: Mapped[bool] = mapped_column(Boolean, default=False)

    # gửi mail & quản lý xác nhận
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_to: Mapped[str | None] = mapped_column(String(255))
    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    confirm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # số đề nghị + số hóa đơn + bên bán + tên file, đã fold() - dùng cho ô tìm kiếm (tự cập nhật khi lưu)
    search_text: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="requests", foreign_keys=[user_id])
    sent_by: Mapped[User | None] = relationship(foreign_keys=[sent_by_id])
    events: Mapped[list["RequestEvent"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestEvent.id")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def build_search_text(self) -> str:
        return fold(" | ".join(str(v) for v in (self.so_de_nghi, self.so_hoa_don, self.ben_ban,
                                                self.original_filename) if v))


@event.listens_for(PaymentRequest, "before_insert")
@event.listens_for(PaymentRequest, "before_update")
def _refresh_search_text(_mapper, _conn, target: PaymentRequest):
    target.search_text = target.build_search_text()


class RequestEvent(Base):
    """Nhật ký từng bước của phiếu (ai làm gì, lúc nào)."""
    __tablename__ = "request_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("payment_requests.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(255))   # "AI", tên người dùng, "Quản lý <email>"
    kind: Mapped[str] = mapped_column(String(32))     # upload, ai_read, reread, needs_human, review, edit, send, confirm...
    message: Mapped[str] = mapped_column(Text, default="")

    request: Mapped[PaymentRequest] = relationship(back_populates="events")


class Counter(Base):
    """Bộ đếm (số đề nghị theo ngày/tháng/năm)."""
    __tablename__ = "counters"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)
