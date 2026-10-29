"""
Nghiệp vụ theo sơ đồ quy trình:

  Người dùng chọn nhiều hóa đơn -> phiếu ở trạng thái QUEUED
  Vòng lặp AI (worker.py gọi process_request):
     1. Nhận hóa đơn      claim_next(): lấy phiếu QUEUED cũ nhất
     2. Đọc ảnh hóa đơn   Gemini đọc từng file
     4. Tự kiểm tra       checks.check_invoice(); nghi sai tiền/thuế/ngày -> AI đọc lại kèm gợi ý
        (ảnh mờ, không đọc được -> NEEDS_HUMAN, trả lại cho con người)
     3. Xuất file Excel   render_excel()
     5. Báo cáo kết quả   -> REVIEW "Chờ người xem xét"
  Người dùng: xem và sửa trực tiếp (save_edit) | B: AI đọc lại (request_reread -> QUEUED)
  Người dùng: xác nhận & gửi mail (send_to_manager -> SENT) | A: quay lại sửa
  Quản lý: bấm Xác nhận trong mail (confirm_by_token -> CONFIRMED "Đã xác nhận đọc")
"""
import hashlib
import json
import logging
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from . import config, logos, mailer
from .checks import check_invoice, has, score
from .config import DATA_DIR, FILES_DIR
from .context import to_number
from .db import SessionLocal
from .extractor import ExtractionError, extract_invoice_data
from .models import (CONFIRMED, EDITABLE, NEEDS_HUMAN, PROCESSING, QUEUED, REREADABLE, REVIEW, SENT,
                     Counter, PaymentRequest, RequestEvent, User, utcnow)
from .numbering import next_so_de_nghi
from .security import hash_password
from .template_engine import TemplateError, fill_template

log = logging.getLogger("invoice")

EXT_BY_MIME = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
AI = "AI"


class UploadError(ValueError):
    pass


class WorkflowError(ValueError):
    """Thao tác không hợp lệ ở trạng thái hiện tại của phiếu."""


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite trả về datetime không có múi giờ -> coi là UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def detect_invoice_type(data: bytes) -> str:
    """Nhận diện theo nội dung file (magic bytes), không tin đuôi file."""
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise UploadError("Chỉ nhận hóa đơn dạng PDF, JPG, PNG hoặc WEBP.")


def check_template(data: bytes | None):
    if data and not data.startswith(b"PK"):
        raise UploadError("Mẫu Excel phải là file .xlsx (không nhận .xls cũ).")


def _rel(p: Path) -> str:
    return p.relative_to(DATA_DIR).as_posix()


def abs_path(rel: str | None) -> Path | None:
    return (DATA_DIR / rel) if rel else None


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[:120] or "file"


def log_event(db: Session, req: PaymentRequest, actor: str, kind: str, message: str = ""):
    db.add(RequestEvent(request_id=req.id, actor=actor, kind=kind, message=message))


# ---------------------------------------------------------------------------
# Con người: chọn nhiều file hóa đơn
# ---------------------------------------------------------------------------
def create_request(db: Session, user: User, filename: str, content: bytes,
                   template: bytes | None, extra: dict) -> PaymentRequest:
    mime = detect_invoice_type(content)
    req = PaymentRequest(user_id=user.id, status=QUEUED, original_filename=filename or "hoa_don",
                         mime_type=mime, original_path="", extra=extra)
    db.add(req)
    db.flush()  # có id để đặt tên thư mục

    folder = FILES_DIR / str(req.id)
    folder.mkdir(parents=True, exist_ok=True)
    orig = folder / f"hoa_don{EXT_BY_MIME[mime]}"
    orig.write_bytes(content)
    req.original_path = _rel(orig)
    if template:
        t = folder / "mau.xlsx"
        t.write_bytes(template)
        req.template_path = _rel(t)
    log_event(db, req, user.label, "upload", f"Gửi hóa đơn {req.original_filename}")
    db.commit()
    return req


# ---------------------------------------------------------------------------
# Vòng lặp AI
# ---------------------------------------------------------------------------
def claim_next(db: Session) -> int | None:
    """Bước 1: nhận hóa đơn chờ đọc cũ nhất. Khóa bằng UPDATE có điều kiện nên an toàn
    khi chạy nhiều worker/tiến trình."""
    now = utcnow()
    ids = db.scalars(
        select(PaymentRequest.id)
        .where(PaymentRequest.status == QUEUED,
               or_(PaymentRequest.next_attempt_at.is_(None), PaymentRequest.next_attempt_at <= now))
        .order_by(PaymentRequest.created_at, PaymentRequest.id).limit(5)
    ).all()
    for rid in ids:
        res = db.execute(update(PaymentRequest)
                         .where(PaymentRequest.id == rid, PaymentRequest.status == QUEUED)
                         .values(status=PROCESSING, updated_at=now))
        db.commit()
        if res.rowcount == 1:
            return rid
    return None


def _summary_from(req: PaymentRequest, data: dict):
    req.so_hoa_don = str(data["so_hoa_don"]) if data.get("so_hoa_don") else None
    req.ngay_hoa_don = data.get("ngay_thang_nam")
    req.ben_ban = (data.get("ben_ban") or {}).get("ten_cong_ty") or None
    total = to_number(data.get("tong_tien_thanh_toan"))
    if total is None:
        vals = [to_number(i.get("thanh_tien")) for i in (data.get("hang_muc") or []) if isinstance(i, dict)]
        if vals and None not in vals:
            total = sum(vals) + (to_number(data.get("tien_thue")) or 0)
    req.tong_tien = total


def render_excel(req: PaymentRequest, data: dict):
    """Bước 3: xuất file Excel từ dữ liệu. Giữ số đề nghị đã cấp (nếu có)."""
    template = abs_path(req.template_path).read_bytes() if req.template_path else None
    extra = dict(req.extra or {})
    if req.so_de_nghi and not extra.get("so_de_nghi"):
        extra["so_de_nghi"] = req.so_de_nghi
    issued: dict = {}

    def provider():
        issued["v"] = next_so_de_nghi()
        return issued["v"]

    # Logo chọn khi gửi hóa đơn: chỉ thay vào mẫu mặc định, mẫu tự làm giữ ảnh của nó
    logo = None if template else logos.logo_bytes(extra.get("logo"))
    content, warnings = fill_template(data, template, extra, so_de_nghi_provider=provider, logo=logo)
    out = abs_path(req.original_path).parent / "de_nghi.xlsx"
    out.write_bytes(content)
    req.output_path = _rel(out)
    req.so_de_nghi = issued.get("v") or extra.get("so_de_nghi") or req.so_de_nghi
    req.warnings = warnings
    _summary_from(req, data)


def _to_needs_human(db, req, reason: str):
    req.status, req.error = NEEDS_HUMAN, reason
    log_event(db, req, AI, "needs_human", reason)


def process_request(req_id: int, extract=None):
    """Bước 2-5 cho một phiếu đã được claim (trạng thái PROCESSING)."""
    extract = extract or extract_invoice_data
    with SessionLocal() as db:
        req = db.get(PaymentRequest, req_id)
        if req is None or req.status != PROCESSING:
            return
        try:
            content = abs_path(req.original_path).read_bytes()
        except OSError:
            _to_needs_human(db, req, "Không còn file hóa đơn gốc trên server.")
            db.commit()
            return

        # ---- 2. Đọc + 4. Tự kiểm tra (đọc lại khi nghi sai) ----
        results, hint = [], None
        for n in range(1 + max(0, config.SELF_CHECK_REREADS)):
            try:
                data = extract(content, req.mime_type, hint=hint)
            except ExtractionError as e:
                if results:  # đã có một lần đọc được -> dùng nó, không bỏ phí
                    break
                if e.transient:
                    req.attempts = (req.attempts or 0) + 1
                    if req.attempts < config.MAX_TRANSIENT_ATTEMPTS:
                        wait = 30 * 2 ** (req.attempts - 1)  # 30s, 60s, 120s, 240s...
                        req.status = QUEUED
                        req.next_attempt_at = utcnow() + timedelta(seconds=wait)
                        req.error = f"Gemini tạm thời không phản hồi, AI sẽ thử lại sau {wait} giây. ({e})"
                        log_event(db, req, AI, "retry_later", f"Lần {req.attempts}: {e}")
                    else:
                        _to_needs_human(db, req, f"Gemini lỗi liên tục sau {req.attempts} vòng thử. {e}")
                else:
                    _to_needs_human(db, req, f"Không đọc được hóa đơn: {e}")
                db.commit()
                return
            except Exception as e:  # noqa: BLE001
                log.exception("Lỗi đọc phiếu %s", req_id)
                _to_needs_human(db, req, f"Lỗi khi đọc hóa đơn: {e}")
                db.commit()
                return

            req.ai_reads = (req.ai_reads or 0) + 1
            issues = check_invoice(data)
            results.append((data, issues))
            suspects = [i["message"] for i in issues if i["level"] == "suspect"]
            crit = [i["message"] for i in issues if i["level"] == "critical"]
            label = "Đọc" if n == 0 else f"Đọc lại lần {n}"
            if crit:
                log_event(db, req, AI, "ai_read", f"{label}: {'; '.join(crit)}")
            elif suspects:
                log_event(db, req, AI, "ai_read", f"{label}: tự kiểm tra thấy {len(suspects)} điểm nghi sai: "
                                                  + "; ".join(suspects))
            else:
                log_event(db, req, AI, "ai_read", f"{label}: tự kiểm tra tiền, thuế, ngày khớp.")
            if any(i["code"] == "unreadable" for i in issues) or not (suspects or crit):
                break
            hint = suspects + crit

        # chọn lần đọc tốt nhất (ít lỗi nhất; hòa thì lấy lần sau)
        best = min(range(len(results)), key=lambda i: (score(results[i][1]), -i))
        data, issues = results[best]
        req.data, req.attempts, req.next_attempt_at, req.edited_by_human = data, 0, None, False

        if has(issues, "critical"):
            req.warnings = [i["message"] for i in issues]
            _summary_from(req, data)
            _to_needs_human(db, req, " ".join(i["message"] for i in issues if i["level"] == "critical")
                            + " Xem hóa đơn gốc rồi nhập tay, hoặc gửi lại ảnh rõ hơn.")
            db.commit()
            return

        # ---- 3. Xuất Excel ----
        try:
            render_excel(req, data)
        except TemplateError as e:
            _to_needs_human(db, req, f"Mẫu Excel không hợp lệ: {e}")
            db.commit()
            return
        except Exception as e:  # noqa: BLE001
            log.exception("Lỗi xuất Excel phiếu %s", req_id)
            _to_needs_human(db, req, f"Lỗi khi tạo file Excel: {e}")
            db.commit()
            return

        # ---- 5. Báo cáo kết quả ----
        req.status, req.error = REVIEW, None
        n_warn = len(req.warnings or [])
        log_event(db, req, AI, "review",
                  f"Xuất Excel số {req.so_de_nghi or '(không dùng số)'}, chuyển chờ người xem xét"
                  + (f", còn {n_warn} điểm cần kiểm tra." if n_warn else "."))
        db.commit()


# ---------------------------------------------------------------------------
# Con người: xem và sửa trực tiếp, AI đọc lại (B)
# ---------------------------------------------------------------------------
def save_edit(db: Session, req: PaymentRequest, user: User, data: dict, extra: dict):
    if req.status not in EDITABLE:
        raise WorkflowError("Phiếu này không còn sửa được ở trạng thái hiện tại.")
    old = (req.data, req.extra)
    req.data, req.extra = data, extra
    try:
        render_excel(req, data)
    except TemplateError as e:
        req.data, req.extra = old
        raise WorkflowError(f"Mẫu Excel không hợp lệ: {e}") from e
    req.status, req.error, req.edited_by_human = REVIEW, None, True
    n = len(req.warnings or [])
    log_event(db, req, user.label, "edit",
              "Sửa dữ liệu trực tiếp, tạo lại file Excel" + (f" (còn {n} điểm cần kiểm tra)." if n else "."))
    db.commit()


def request_reread(db: Session, req: PaymentRequest, user: User):
    if req.status not in REREADABLE:
        raise WorkflowError("Chỉ đọc lại được phiếu đang chờ xem xét hoặc cần người xử lý.")
    req.status, req.error, req.attempts, req.next_attempt_at = QUEUED, None, 0, None
    log_event(db, req, user.label, "reread",
              "Yêu cầu AI đọc lại" + (" (bỏ phần đã sửa tay)" if req.edited_by_human else ""))
    db.commit()


def delete_request(db: Session, req: PaymentRequest):
    folder = abs_path(req.original_path).parent if req.original_path else None
    db.delete(req)
    db.commit()
    if folder and folder.is_dir() and folder.parent == FILES_DIR:
        shutil.rmtree(folder, ignore_errors=True)


def output_download_name(req: PaymentRequest) -> str:
    base = req.so_de_nghi or (f"HD{req.so_hoa_don}" if req.so_hoa_don else f"phieu_{req.id}")
    return f"giay_de_nghi_{_safe_name(base.replace('/', '-'))}.xlsx"


# ---------------------------------------------------------------------------
# Con người: xác nhận & gửi mail  ->  Quản lý: bấm Xác nhận
# ---------------------------------------------------------------------------
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def send_to_manager(db: Session, req: PaymentRequest, user: User, to: str, note: str, base_url: str) -> str:
    if req.status not in (REVIEW, SENT):
        raise WorkflowError("Chỉ gửi được phiếu đang chờ xem xét.")
    to = to.strip()
    if not EMAIL_RE.match(to):
        raise WorkflowError("Địa chỉ mail người quản lý không hợp lệ.")
    path = abs_path(req.output_path)
    if not path or not path.exists():
        raise WorkflowError("Phiếu chưa có file Excel.")

    token = secrets.token_urlsafe(32)
    sender = (req.extra or {}).get("nguoi_de_nghi") or user.label
    msg = mailer.build_message(
        to=to, sender_name=sender, reply_to=user.email, so_de_nghi=req.so_de_nghi or f"Phiếu #{req.id}",
        summary={"tong_tien": req.tong_tien, "ben_ban": req.ben_ban, "so_hoa_don": req.so_hoa_don,
                 "ngay_hoa_don": req.ngay_hoa_don},
        note=note.strip(), confirm_url=f"{base_url.rstrip('/')}/xac-nhan/{token}",
        attachment=path.read_bytes(), attachment_name=output_download_name(req),
    )
    how = mailer.send(msg)  # lỗi -> MailError, phiếu giữ nguyên trạng thái

    resend = req.status == SENT
    req.status, req.sent_at, req.sent_to, req.sent_by_id = SENT, utcnow(), to, user.id
    req.confirm_token_hash = _hash_token(token)
    req.confirm_expires_at = utcnow() + timedelta(days=config.CONFIRM_LINK_DAYS)
    log_event(db, req, user.label, "send",
              f"{'Gửi lại' if resend else 'Xác nhận và gửi'} mail cho {to} ({how})"
              + (f". Ghi chú: {note.strip()}" if note.strip() else ""))
    db.commit()
    return how


def recall(db: Session, req: PaymentRequest, user: User):
    """Thu hồi mail đã gửi (link xác nhận cũ hết hiệu lực) để quay lại sửa."""
    if req.status != SENT:
        raise WorkflowError("Chỉ thu hồi được phiếu đang chờ quản lý xác nhận.")
    req.status, req.confirm_token_hash, req.confirm_expires_at = REVIEW, None, None
    log_event(db, req, user.label, "recall", f"Thu hồi mail đã gửi cho {req.sent_to} để sửa lại.")
    db.commit()


def find_by_token(db: Session, token: str) -> PaymentRequest | None:
    if not token or len(token) > 100:
        return None
    return db.scalar(select(PaymentRequest).where(PaymentRequest.confirm_token_hash == _hash_token(token)))


def token_usable(req: PaymentRequest) -> bool:
    exp = as_utc(req.confirm_expires_at)
    return req.status == SENT and exp is not None and exp > utcnow()


def confirm_by_token(db: Session, req: PaymentRequest, ip: str):
    if req.status == CONFIRMED:
        return
    if not token_usable(req):
        raise WorkflowError("Đường dẫn xác nhận đã hết hạn hoặc đã bị thu hồi.")
    req.status, req.confirmed_at, req.confirmed_by = CONFIRMED, utcnow(), req.sent_to
    log_event(db, req, f"Quản lý {req.sent_to}", "confirm", f"Bấm Xác nhận trong mail (IP {ip}).")
    db.commit()


# ---------------------------------------------------------------------------
def on_startup():
    """Nối lại phiếu bị dừng giữa chừng, tạo admin đầu tiên từ .env, chuyển bộ đếm cũ."""
    import os

    with SessionLocal() as db:
        n = db.execute(update(PaymentRequest).where(PaymentRequest.status == PROCESSING)
                       .values(status=QUEUED)).rowcount
        if n:
            log.warning("Đưa %s phiếu đang đọc dở trở lại hàng đợi.", n)

        if db.scalar(select(User.id).limit(1)) is None:
            u, p = os.getenv("ADMIN_USERNAME"), os.getenv("ADMIN_PASSWORD")
            if u and p:
                db.add(User(username=u.strip().lower(), password_hash=hash_password(p),
                            full_name=u, is_admin=True))
                log.warning("Đã tạo tài khoản quản trị '%s' từ .env", u)
            else:
                log.warning("Chưa có tài khoản nào. Chạy: python -m app.manage create-user <ten> --admin")

        # CSDL cũ chưa có cột tìm kiếm -> điền cho các phiếu đã có
        for req in db.scalars(select(PaymentRequest).where(PaymentRequest.search_text.is_(None))):
            req.search_text = req.build_search_text()

        old = Path(__file__).resolve().parent / "data" / "so_de_nghi.json"
        if old.exists() and db.scalar(select(Counter.key).limit(1)) is None:
            try:
                st = json.loads(old.read_text(encoding="utf-8"))
                db.add(Counter(key=f"so_de_nghi:{st['period']}", value=int(st["stt"])))
            except Exception:  # noqa: BLE001
                pass
        db.commit()
