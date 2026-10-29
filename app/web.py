"""Các trang web, theo sơ đồ quy trình (xem services.py)."""
import math
import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Undefined
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, logos, mailer, services
from .config import BASE_DIR, MAX_FILES_PER_UPLOAD, MAX_UPLOAD_MB, TIMEZONE
from .context import build_item_context, parse_date, to_number, to_number_vn
from .db import get_db
from .models import (CONFIRMED, EDITABLE, NEEDS_HUMAN, PROCESSING, QUEUED, REREADABLE, REVIEW, SENT,
                     STATUS_LABELS, PaymentRequest, User, fold, utcnow)
from .security import (check_csrf, csrf_token, current_user_optional, hash_password, login_locked,
                       record_login, require_admin, require_user, validate_new_password, validate_username,
                       verify_password)
from .template_engine import DEFAULT_TEMPLATE_PATH
from .worker import worker

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web_templates"))
PAGE_SIZE = 20
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Tab trên trang danh sách
TABS = [
    ("", "Tất cả", None),
    ("can-xu-ly", "Chờ xem xét", (REVIEW, NEEDS_HUMAN)),
    ("ai-dang-doc", "Đang xử lý", (QUEUED, PROCESSING)),
    ("cho-quan-ly", "Chờ phê duyệt", (SENT,)),
    ("da-xac-nhan", "Hoàn tất", (CONFIRMED,)),
]
EXTRA_FIELDS = {
    "nguoi_de_nghi": "Người đề nghị", "bo_phan": "Bộ phận", "da_tam_ung": "Đã tạm ứng",
    "nguoi_phe_duyet": "Người phê duyệt", "ke_toan": "Kế toán", "truong_bo_phan": "Trưởng bộ phận",
    "ghi_chu": "Ghi chú",
}


# ---------- bộ lọc hiển thị ----------
def _vnd(v):
    if v is None or v == "" or isinstance(v, Undefined):
        return ""
    try:
        return f"{float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(v)


def _num_input(v):
    """Số -> chuỗi cho ô nhập (1333333 -> '1.333.333', 1.5 -> '1,5')."""
    if v is None or v == "" or isinstance(v, Undefined):
        return ""
    n = to_number(v)
    if n is None:
        return str(v)
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").rstrip("0").rstrip(",")
    return f"{int(n):,}".replace(",", ".")


def _local(dt, fmt="%d/%m/%Y %H:%M"):
    if not dt:
        return ""
    return services.as_utc(dt).astimezone(TIMEZONE).strftime(fmt)


templates.env.filters.update(vnd=_vnd, local=_local, num_input=_num_input)
templates.env.globals.update(STATUS_LABELS=STATUS_LABELS, EXTRA_FIELDS=EXTRA_FIELDS,
                             LOGOS=logos.LOGOS, DEFAULT_LOGO=logos.DEFAULT_LOGO)


def render(request: Request, name: str, user: User | None = None, status_code=200, **ctx):
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(
        request, name,
        {"user": user, "csrf": csrf_token(request), "flash": flash_msg, "now_path": request.url.path, **ctx},
        status_code=status_code,
    )


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def _safe_next(nxt: str | None) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/"


def _get_owned(db: Session, user: User, req_id: int) -> PaymentRequest:
    req = db.get(PaymentRequest, req_id)
    if req is None or (req.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Không tìm thấy phiếu.")
    return req


def _back(req_id: int):
    return RedirectResponse(f"/de-nghi/{req_id}", 303)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ---------- đăng nhập ----------
@router.get("/login")
def login_page(request: Request, next: str = "/de-nghi", user=Depends(current_user_optional)):
    if user:
        return RedirectResponse(_safe_next(next), 303)
    return render(request, "login.html", next=_safe_next(next))


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""),
          next: str = Form("/de-nghi"), csrf: str = Form(None), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    username = username.strip().lower()
    ip = _client_ip(request)
    wait = login_locked(ip, username)
    if wait:
        return render(request, "login.html", next=next, username=username, status_code=429,
                      error=f"Sai mật khẩu quá nhiều lần. Thử lại sau {math.ceil(wait / 60)} phút.")
    user = db.scalar(select(User).where(User.username == username))
    ok = bool(user and user.is_active and verify_password(password, user.password_hash))
    record_login(ip, username, ok)
    if not ok:
        return render(request, "login.html", next=next, username=username, status_code=401,
                      error="Sai tên đăng nhập hoặc mật khẩu.")
    request.session.clear()  # chống session fixation
    request.session["user_id"] = user.id
    user.last_login_at = utcnow()
    db.commit()
    # Sơ đồ: đăng nhập xong -> hiển thị danh sách đã gửi
    return RedirectResponse(_safe_next(next), 303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form(None)):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", 303)


# ---------- chọn nhiều file hóa đơn ----------
@router.get("/")
def new_request_page(request: Request, user: User = Depends(require_user)):
    return render(request, "upload.html", user, max_mb=MAX_UPLOAD_MB, max_files=MAX_FILES_PER_UPLOAD)


def _extra_from_form(form) -> tuple[dict, list[str]]:
    errors, extra = [], {}
    for k in EXTRA_FIELDS:
        v = (form.get(k) or "").strip()
        if not v:
            continue
        if k == "da_tam_ung":
            n = to_number(v)
            if n is None:
                errors.append("Ô “Đã tạm ứng” phải là số.")
                continue
            extra[k] = n
        else:
            extra[k] = v
    extra["logo"] = logos.normalize(form.get("logo"))  # logo in trên phiếu
    return extra, errors


@router.post("/de-nghi")
async def create_requests(request: Request, files: list[UploadFile] | None = File(None),
                          template: UploadFile | None = File(None),
                          user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    files = [f for f in (files or []) if f and f.filename]

    def fail(msg):
        return render(request, "upload.html", user, status_code=400, error=msg,
                      max_mb=MAX_UPLOAD_MB, max_files=MAX_FILES_PER_UPLOAD)

    if not files:
        return fail("Chọn ít nhất một hóa đơn.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        return fail(f"Mỗi lần gửi tối đa {MAX_FILES_PER_UPLOAD} hóa đơn.")
    extra, errs = _extra_from_form(form)
    if errs:
        return fail(errs[0])

    limit = int(MAX_UPLOAD_MB * 1024 * 1024)
    tpl = None
    if template is not None and template.filename:
        tpl = await template.read(limit + 1)
        if len(tpl) > limit:
            return fail(f"Mẫu Excel vượt quá {MAX_UPLOAD_MB:g} MB.")
        try:
            services.check_template(tpl)
        except services.UploadError as e:
            return fail(str(e))

    payloads = []
    for f in files:
        content = await f.read(limit + 1)
        if not content:
            return fail(f"File '{f.filename}' rỗng.")
        if len(content) > limit:
            return fail(f"File '{f.filename}' vượt quá {MAX_UPLOAD_MB:g} MB.")
        try:
            services.detect_invoice_type(content)
        except services.UploadError as e:
            return fail(f"'{f.filename}': {e}")
        payloads.append((f.filename, content))

    for name, content in payloads:
        services.create_request(db, user, name, content, tpl, extra)
    worker.notify()  # đánh thức vòng lặp AI
    flash(request, f"Đã nhận {len(payloads)} hóa đơn. AI sẽ đọc lần lượt từng file; "
                   "phiếu đọc xong sẽ chuyển sang “Chờ xem xét”.")
    return RedirectResponse("/de-nghi?tab=ai-dang-doc", 303)


# ---------- danh sách đã gửi ----------
@router.get("/de-nghi")
def list_requests(request: Request, tab: str = "", q: str = "", tu_ngay: str = "", den_ngay: str = "",
                  nguoi_gui: str = "", page: int = 1,
                  user: User = Depends(require_user), db: Session = Depends(get_db)):
    scope = select(PaymentRequest)
    if not user.is_admin:
        scope = scope.where(PaymentRequest.user_id == user.id)
    elif nguoi_gui.isdigit():
        scope = scope.where(PaymentRequest.user_id == int(nguoi_gui))
    if q.strip():
        # so sánh trên cột đã chuẩn hóa: không phân biệt hoa/thường, có dấu hay không dấu (mọi loại CSDL)
        needle = fold(q.strip()).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        scope = scope.where(PaymentRequest.search_text.like(f"%{needle}%", escape="\\"))

    def _day(s, end=False):
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None
        return datetime.combine(d + timedelta(days=1) if end else d, time.min, TIMEZONE).astimezone(timezone.utc)

    if (start := _day(tu_ngay)) is not None:
        scope = scope.where(PaymentRequest.created_at >= start)
    if (end := _day(den_ngay, end=True)) is not None:
        scope = scope.where(PaymentRequest.created_at < end)

    sub = scope.subquery()
    counts = dict(db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all())
    tab_info = [(key, label, sum(counts.get(s, 0) for s in sts) if sts else sum(counts.values()))
                for key, label, sts in TABS]
    statuses = next((sts for key, _, sts in TABS if key == tab), None)
    stmt = scope.where(PaymentRequest.status.in_(statuses)) if statuses else scope

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    money = stmt.subquery()
    sum_total = db.scalar(select(func.coalesce(func.sum(money.c.tong_tien), 0)).select_from(money))
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(page, 1), pages)
    rows = db.scalars(stmt.order_by(PaymentRequest.created_at.desc(), PaymentRequest.id.desc())
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()

    users = db.scalars(select(User).order_by(User.full_name, User.username)).all() if user.is_admin else []
    filters = {"q": q, "tu_ngay": tu_ngay, "den_ngay": den_ngay, "nguoi_gui": nguoi_gui}
    qs = lambda **kw: urlencode({k: v for k, v in {**filters, "tab": tab, **kw}.items() if v})  # noqa: E731
    return render(request, "list.html", user, rows=rows, total=total, sum_total=sum_total,
                  page=page, pages=pages, page_size=PAGE_SIZE, users=users, tab=tab, tabs=tab_info, qs=qs,
                  has_filter=any(filters.values()),
                  my_busy=_my_busy_count(db, user), **filters)


def _my_busy_count(db: Session, user: User) -> int:
    """Số phiếu CỦA CHÍNH người đang xem còn chờ AI / AI đang đọc."""
    return db.scalar(select(func.count()).select_from(PaymentRequest).where(
        PaymentRequest.user_id == user.id, PaymentRequest.status.in_((QUEUED, PROCESSING)))) or 0


@router.get("/trang-thai-xu-ly")
def busy_status(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Màn hình chờ trên trang danh sách hỏi trạng thái mỗi 3 giây."""
    rows = db.execute(select(PaymentRequest.status, PaymentRequest.error).where(
        PaymentRequest.user_id == user.id, PaymentRequest.status.in_((QUEUED, PROCESSING)))).all()
    processing = sum(1 for r in rows if r.status == PROCESSING)
    note = next((r.error for r in rows if r.status == QUEUED and r.error), None)  # vd Gemini quá tải
    return JSONResponse({"busy": len(rows), "processing": processing, "queued": len(rows) - processing,
                         "note": note}, headers={"Cache-Control": "no-store"})


# ---------- chi tiết ----------
def _items(data):
    return [build_item_context(it, i) for i, it in enumerate((data or {}).get("hang_muc") or [])
            if isinstance(it, dict)]


@router.get("/de-nghi/{req_id}")
def detail(request: Request, req_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    req = _get_owned(db, user, req_id)
    data = req.data or {}
    return render(request, "detail.html", user, r=req, d=data, items=_items(data),
                  tien_thue=to_number(data.get("tien_thue")),
                  tong_truoc_thue=to_number(data.get("tong_tien_truoc_thue")),
                  EDITABLE=EDITABLE, REREADABLE=REREADABLE, worker_alive=worker.alive or not config.WORKER_ENABLED)


@router.get("/de-nghi/{req_id}/trang-thai")
def status_json(req_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    req = _get_owned(db, user, req_id)
    return JSONResponse({"status": req.status})


@router.get("/de-nghi/{req_id}/excel")
def download_excel(req_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    req = _get_owned(db, user, req_id)
    path = services.abs_path(req.output_path)
    if not path or not path.exists() or req.status in (QUEUED, PROCESSING):
        raise HTTPException(404, "Phiếu chưa có file Excel.")
    return FileResponse(path, media_type=XLSX_MIME, filename=services.output_download_name(req))


@router.get("/de-nghi/{req_id}/hoa-don")
def view_original(req_id: int, download: bool = False,
                  user: User = Depends(require_user), db: Session = Depends(get_db)):
    req = _get_owned(db, user, req_id)
    return _serve_original(req, download)


def _serve_original(req: PaymentRequest, download: bool):
    path = services.abs_path(req.original_path)
    if not path or not path.exists():
        raise HTTPException(404, "Không còn file hóa đơn gốc.")
    disp = "attachment" if download else "inline"
    return FileResponse(path, media_type=req.mime_type, headers={
        "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(req.original_filename)}",
        "X-Content-Type-Options": "nosniff",
    })


# ---------- B: AI đọc lại ----------
@router.post("/de-nghi/{req_id}/doc-lai")
def reread(request: Request, req_id: int, csrf: str = Form(None),
           user: User = Depends(require_user), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    req = _get_owned(db, user, req_id)
    try:
        services.request_reread(db, req, user)
    except services.WorkflowError as e:
        flash(request, str(e), "error")
        return _back(req_id)
    worker.notify()
    flash(request, "Đã đưa hóa đơn vào hàng đợi, AI sẽ đọc lại ở vòng sau.")
    return _back(req_id)


# ---------- xem và sửa trực tiếp ----------
TEXT_FIELDS = ("so_hoa_don", "ky_hieu", "thue_suat", "so_tien_bang_chu")
MONEY_FIELDS = {"tong_tien_truoc_thue": "Cộng tiền hàng", "tien_thue": "Tiền thuế",
                "tong_tien_thanh_toan": "Tổng thanh toán"}
def parse_tax_rate(text) -> float | None:
    """'10%' -> 0.1 ; '8' -> 0.08 ; 'KCT' / '0%' / trống -> 0 ; không hiểu hoặc nhiều mức -> None."""
    rates = parse_tax_rates(text)
    return rates[0] if rates and len(rates) == 1 else None


_RATE_PCT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def parse_tax_rates(text) -> list[float] | None:
    """Các mức thuế trên hóa đơn: '10%' -> [0.1] ; '5%, 10%' -> [0.05, 0.1] ; 'KCT, 8%' -> [0.0, 0.08] ;
    '8' -> [0.08] ; trống/KCT -> [0.0] ; không hiểu -> None."""
    t = str(text or "").strip().upper()
    if not t or t in ("KCT", "KKKNT", "-", "0", "0%"):
        return [0.0]
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*%?", t)
    if m:
        return [float(m.group(1).replace(",", ".")) / 100]
    rates = [float(x.replace(",", ".")) / 100 for x in _RATE_PCT.findall(t)]
    rates += [0.0] * len(re.findall(r"\bKCT\b|\bKKKNT\b", t))
    rest = re.sub(r"\d+(?:[.,]\d+)?\s*%|\bKCT\b|\bKKKNT\b", "", t)
    if not rates or re.sub(r"[\s,;/+&]|\bVÀ\b|\bVA\b", "", rest):
        return None
    return sorted(set(rates))


SELLER_FIELDS = ("ten_cong_ty", "ma_so_thue", "dia_chi", "so_tai_khoan", "ngan_hang")


def _parse_edit_form(form, old: dict) -> tuple[dict, dict, list[str]]:
    errors: list[str] = []
    data = dict(old or {})
    for k in TEXT_FIELDS:
        data[k] = (form.get(k) or "").strip() or None
    for k, label in MONEY_FIELDS.items():
        raw = (form.get(k) or "").strip()
        n = to_number(raw) if raw else None
        if raw and n is None:
            errors.append(f"Ô “{label}” phải là số.")
        data[k] = n

    raw_date = (form.get("ngay_thang_nam") or "").strip()
    if raw_date:
        dd, mm, yyyy = parse_date({"ngay_thang_nam": raw_date})
        if not dd:
            errors.append("Ngày hóa đơn không hợp lệ, nhập theo dạng ngày/tháng/năm (vd 30/09/2026).")
            data["ngay_thang_nam"], data["ngay_iso"] = raw_date, None
        else:
            data["ngay_thang_nam"], data["ngay_iso"] = f"{dd}/{mm}/{yyyy}", f"{yyyy}-{mm}-{dd}"
    else:
        data["ngay_thang_nam"] = data["ngay_iso"] = None

    seller = dict(old.get("ben_ban") or {})
    for k in SELLER_FIELDS:
        seller[k] = (form.get(f"ben_ban_{k}") or "").strip() or None
    data["ben_ban"] = seller

    cols = {k: form.getlist(f"item_{k}") for k in ("ten", "dvt", "sl", "dg", "tt", "gc")}
    n_rows = max((len(v) for v in cols.values()), default=0)
    items = []
    for i in range(n_rows):
        get = lambda k: (cols[k][i] if i < len(cols[k]) else "").strip()  # noqa: E731
        if not any(get(k) for k in cols):
            continue  # dòng trống
        row = {"stt": len(items) + 1, "ten_hang_hoa_dich_vu": get("ten") or None,
               "don_vi_tinh": get("dvt") or None, "ghi_chu": get("gc") or None}
        for key, k, label in (("so_luong", "sl", "Số lượng"), ("don_gia", "dg", "Đơn giá"),
                              ("thanh_tien", "tt", "Thành tiền")):
            parse = to_number_vn if key == "so_luong" else to_number  # SL: dấu phẩy là thập phân
            n = parse(get(k)) if get(k) else None
            if get(k) and n is None:
                errors.append(f"Dòng {len(items) + 1}: ô “{label}” phải là số.")
            row[key] = n
        if not row["ten_hang_hoa_dich_vu"]:
            errors.append(f"Dòng {len(items) + 1}: chưa nhập diễn giải.")
        items.append(row)
    if not items:
        errors.append("Cần ít nhất một dòng hàng mục.")
    data["hang_muc"] = items
    
    # Tổng tiền: server luôn tự tính (các ô này chỉ xem trên web, không tin số trình duyệt gửi lên)
    for it in items:
        if it["thanh_tien"] is None and it["so_luong"] is not None and it["don_gia"] is not None:
            it["thanh_tien"] = round(it["so_luong"] * it["don_gia"])
    typed_tax = data.get("tien_thue")  # ô Tiền thuế người dùng/AI đã có
    rates = parse_tax_rates(data.get("thue_suat"))
    pre = sum(it["thanh_tien"] or 0 for it in items)
    if rates is None:
        errors.append("Thuế suất không hợp lệ. Nhập dạng 10%, 8%, KCT hoặc nhiều mức như 5%, 10%.")
        tax = 0
    elif len(rates) == 1:
        tax = round(pre * rates[0])           # một mức thuế: server tự tính
    elif typed_tax is None:
        errors.append("Hóa đơn nhiều mức thuế: nhập Tiền thuế đúng như trên hóa đơn.")
        tax = 0
    else:
        tax = typed_tax                       # nhiều mức thuế: giữ tiền thuế theo hóa đơn
    data["tong_tien_truoc_thue"], data["tien_thue"], data["tong_tien_thanh_toan"] = pre, tax, pre + tax
    data["so_tien_bang_chu"] = None  # để hệ thống tự đọc lại theo tổng mới
    data["kha_nang_doc"] = "nguoi_nhap"  # người đã xem và sửa -> bỏ cảnh báo ảnh mờ

    # Người dùng sửa số tiền nhưng không sửa ô "bằng chữ" -> bỏ chữ cũ để hệ thống tự đọc lại
    def _total(d):
        amounts = [to_number(i.get("thanh_tien")) for i in d.get("hang_muc") or [] if isinstance(i, dict)]
        return (to_number(d.get("tong_tien_thanh_toan")), tuple(amounts), to_number(d.get("tien_thue")))
    if data.get("so_tien_bang_chu") and data.get("so_tien_bang_chu") == (old or {}).get("so_tien_bang_chu") \
            and _total(data) != _total(old or {}):
        data["so_tien_bang_chu"] = None

    extra, extra_errs = _extra_from_form(form)
    return data, extra, errors + extra_errs


@router.get("/de-nghi/{req_id}/sua")
def edit_page(request: Request, req_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    req = _get_owned(db, user, req_id)
    if req.status not in EDITABLE:
        flash(request, f"Phiếu đang ở trạng thái “{req.status_label}”, không sửa được.", "error")
        return _back(req_id)
    data = req.data or {}
    return render(request, "edit.html", user, r=req, d=data, items=_items(data), extra=req.extra or {})


@router.post("/de-nghi/{req_id}/sua")
async def edit_save(request: Request, req_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    req = _get_owned(db, user, req_id)
    data, extra, errors = _parse_edit_form(form, req.data or {})
    if errors:
        return render(request, "edit.html", user, status_code=400, r=req, d=data,
                      items=_items(data), extra=extra, errors=errors)
    try:
        await run_in_threadpool(services.save_edit, db, req, user, data, extra)
    except services.WorkflowError as e:
        flash(request, str(e), "error")
        return _back(req_id)
    if form.get("then") == "send":
        return RedirectResponse(f"/de-nghi/{req_id}/gui", 303)
    flash(request, "Đã lưu và tạo lại file Excel.")
    return _back(req_id)


# ---------- xác nhận & gửi mail (A: quay lại sửa) ----------
def _base_url(request: Request) -> str:
    return config.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")


@router.get("/de-nghi/{req_id}/gui")
def send_page(request: Request, req_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    req = _get_owned(db, user, req_id)
    if req.status not in (REVIEW, SENT):
        flash(request, f"Phiếu đang ở trạng thái “{req.status_label}”, chưa gửi được.", "error")
        return _back(req_id)
    managers = db.scalars(select(User).where(User.is_manager.is_(True), User.is_active.is_(True),
                                             User.email.is_not(None))).all()
    last_to = db.scalar(select(PaymentRequest.sent_to).where(PaymentRequest.sent_by_id == user.id,
                                                             PaymentRequest.sent_to.is_not(None))
                        .order_by(PaymentRequest.sent_at.desc()).limit(1))
    default_to = req.sent_to or last_to or (managers[0].email if managers else "")
    base = _base_url(request)
    local_link = not config.PUBLIC_BASE_URL and any(h in base for h in ("localhost", "127.0.0.1"))
    data = req.data or {}
    return render(request, "send.html", user, r=req, d=data, items=_items(data), managers=managers,
                  default_to=default_to, base=base, local_link=local_link, smtp=bool(config.SMTP_HOST))


@router.post("/de-nghi/{req_id}/gui")
async def send(request: Request, req_id: int, to: str = Form(""), note: str = Form(""),
               da_kiem_tra: bool = Form(False), csrf: str = Form(None),
               user: User = Depends(require_user), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    req = _get_owned(db, user, req_id)
    if req.warnings and not da_kiem_tra:
        flash(request, "Phiếu còn điểm cần kiểm tra. Tick “Tôi đã kiểm tra” hoặc quay lại sửa.", "error")
        return RedirectResponse(f"/de-nghi/{req_id}/gui", 303)
    try:
        how = await run_in_threadpool(services.send_to_manager, db, req, user, to, note, _base_url(request))
    except (services.WorkflowError, mailer.MailError) as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/de-nghi/{req_id}/gui", 303)
    msg = f"Đã gửi mail cho {req.sent_to}. Phiếu chờ quản lý xác nhận."
    if not config.SMTP_HOST:
        msg = f"Chưa cấu hình SMTP nên mail chưa được gửi thật: {how}."
    flash(request, msg, "ok" if config.SMTP_HOST else "warn")
    return _back(req_id)


@router.post("/de-nghi/{req_id}/thu-hoi")
def recall(request: Request, req_id: int, csrf: str = Form(None),
           user: User = Depends(require_user), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    req = _get_owned(db, user, req_id)
    try:
        services.recall(db, req, user)
    except services.WorkflowError as e:
        flash(request, str(e), "error")
        return _back(req_id)
    flash(request, "Đã thu hồi. Đường dẫn xác nhận trong mail cũ không còn dùng được; sửa xong hãy gửi lại.")
    return RedirectResponse(f"/de-nghi/{req_id}/sua", 303)


@router.post("/de-nghi/{req_id}/xoa")
def delete(request: Request, req_id: int, csrf: str = Form(None),
           user: User = Depends(require_user), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    req = _get_owned(db, user, req_id)
    if req.status in (CONFIRMED, PROCESSING) and not user.is_admin:
        flash(request, "Không xóa được phiếu đang đọc hoặc đã được quản lý xác nhận.", "error")
        return _back(req_id)
    label = req.so_de_nghi or req.original_filename
    services.delete_request(db, req)
    flash(request, f"Đã xóa phiếu {label}.")
    return RedirectResponse("/de-nghi", 303)


# ---------- quản lý: bấm Xác nhận trong mail (không cần đăng nhập) ----------
def _token_req(db, token) -> PaymentRequest:
    req = services.find_by_token(db, token)
    if req is None:
        raise HTTPException(404, "Đường dẫn xác nhận không đúng hoặc đã bị thu hồi.")
    return req


@router.get("/xac-nhan/{token}")
def confirm_page(request: Request, token: str, db: Session = Depends(get_db)):
    req = _token_req(db, token)
    data = req.data or {}
    # GET chỉ hiển thị: trình quét link của Outlook/Gmail không vô tình xác nhận thay quản lý
    return render(request, "confirm.html", None, r=req, d=data, items=_items(data), token=token,
                  usable=services.token_usable(req))


@router.post("/xac-nhan/{token}")
def confirm(request: Request, token: str, csrf: str = Form(None), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    req = _token_req(db, token)
    try:
        services.confirm_by_token(db, req, _client_ip(request))
    except services.WorkflowError as e:
        raise HTTPException(410, str(e)) from e
    return RedirectResponse(f"/xac-nhan/{token}", 303)


@router.get("/xac-nhan/{token}/excel")
def confirm_excel(token: str, db: Session = Depends(get_db)):
    req = _token_req(db, token)
    path = services.abs_path(req.output_path)
    if not path or not path.exists():
        raise HTTPException(404, "Không còn file Excel.")
    return FileResponse(path, media_type=XLSX_MIME, filename=services.output_download_name(req))


@router.get("/xac-nhan/{token}/hoa-don")
def confirm_original(token: str, db: Session = Depends(get_db)):
    return _serve_original(_token_req(db, token), download=False)


# ---------- tài khoản của tôi ----------
@router.get("/tai-khoan")
def profile(request: Request, user: User = Depends(require_user)):
    return render(request, "profile.html", user)


@router.post("/tai-khoan")
def update_profile(request: Request, full_name: str = Form(""), department: str = Form(""),
                   email: str = Form(""), csrf: str = Form(None),
                   user: User = Depends(require_user), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    email = email.strip()
    if email and not services.EMAIL_RE.match(email):
        return render(request, "profile.html", user, status_code=400, info_error="Địa chỉ mail không hợp lệ.")
    user.full_name, user.department, user.email = full_name.strip(), department.strip(), email or None
    db.commit()
    flash(request, "Đã lưu thông tin.")
    return RedirectResponse("/tai-khoan", 303)


@router.post("/tai-khoan/mat-khau")
def change_password(request: Request, current: str = Form(""), new: str = Form(""), confirm: str = Form(""),
                    csrf: str = Form(None), user: User = Depends(require_user), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    if not verify_password(current, user.password_hash):
        err = "Mật khẩu hiện tại không đúng."
    elif new != confirm:
        err = "Hai lần nhập mật khẩu mới không khớp."
    else:
        err = validate_new_password(new)
    if err:
        return render(request, "profile.html", user, pw_error=err, status_code=400)
    user.password_hash = hash_password(new)
    db.commit()
    flash(request, "Đã đổi mật khẩu.")
    return RedirectResponse("/tai-khoan", 303)


# ---------- quản lý người dùng (admin) ----------
@router.get("/nguoi-dung")
def users_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(PaymentRequest.user_id, func.count()).group_by(PaymentRequest.user_id)).all())
    users = db.scalars(select(User).order_by(User.is_active.desc(), User.username)).all()
    return render(request, "users.html", admin, users=users, counts=counts)


@router.post("/nguoi-dung")
def create_user(request: Request, username: str = Form(""), full_name: str = Form(""),
                department: str = Form(""), email: str = Form(""), password: str = Form(""),
                is_admin: bool = Form(False), is_manager: bool = Form(False), csrf: str = Form(None),
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    username, email = username.strip().lower(), email.strip()
    def _validate() -> str | None:
        if err := validate_username(username):
            return err
        if db.scalar(select(User.id).where(User.username == username)):
            return f"Tên đăng nhập '{username}' đã có người dùng."
        if email and not services.EMAIL_RE.match(email):
            return "Địa chỉ mail không hợp lệ."
        if is_manager and not email:
            return "Quản lý cần có địa chỉ mail để nhận mail xác nhận."
        return validate_new_password(password)

    err = _validate()
    if err:
        flash(request, err, "error")
        return RedirectResponse("/nguoi-dung", 303)
    db.add(User(username=username, full_name=full_name.strip(), department=department.strip(),
                email=email or None, password_hash=hash_password(password),
                is_admin=is_admin, is_manager=is_manager))
    db.commit()
    flash(request, f"Đã tạo tài khoản {username}.")
    return RedirectResponse("/nguoi-dung", 303)


@router.post("/nguoi-dung/{uid}")
def update_user(request: Request, uid: int, action: str = Form(...), password: str = Form(""),
                email: str = Form(""), csrf: str = Form(None),
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    check_csrf(request, csrf)
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404)
    if u.id == admin.id and action in ("toggle_active", "toggle_admin"):
        flash(request, "Không thể tự khóa hoặc tự bỏ quyền quản trị của chính mình.", "error")
        return RedirectResponse("/nguoi-dung", 303)
    if action == "toggle_active":
        u.is_active = not u.is_active
        msg = f"Đã {'mở khóa' if u.is_active else 'khóa'} tài khoản {u.username}."
    elif action == "toggle_admin":
        u.is_admin = not u.is_admin
        msg = f"{u.username} {'đã là' if u.is_admin else 'không còn là'} quản trị viên."
    elif action == "toggle_manager":
        if not u.is_manager and not u.email:
            flash(request, f"Nhập mail cho {u.username} trước khi đặt làm quản lý.", "error")
            return RedirectResponse("/nguoi-dung", 303)
        u.is_manager = not u.is_manager
        msg = f"{u.username} {'đã là' if u.is_manager else 'không còn là'} quản lý nhận mail xác nhận."
    elif action == "set_email":
        email = email.strip()
        if email and not services.EMAIL_RE.match(email):
            flash(request, "Địa chỉ mail không hợp lệ.", "error")
            return RedirectResponse("/nguoi-dung", 303)
        if not email and u.is_manager:
            flash(request, "Quản lý phải có mail. Bỏ quyền quản lý trước.", "error")
            return RedirectResponse("/nguoi-dung", 303)
        u.email = email or None
        msg = f"Đã lưu mail của {u.username}."
    elif action == "reset_password":
        if err := validate_new_password(password):
            flash(request, err, "error")
            return RedirectResponse("/nguoi-dung", 303)
        u.password_hash = hash_password(password)
        msg = f"Đã đặt lại mật khẩu cho {u.username}."
    else:
        raise HTTPException(400)
    db.commit()
    flash(request, msg)
    return RedirectResponse("/nguoi-dung", 303)


@router.get("/mau-mac-dinh")
def default_template(user: User = Depends(require_user)):
    return FileResponse(DEFAULT_TEMPLATE_PATH, media_type=XLSX_MIME, filename="mau_giay_de_nghi.xlsx")
