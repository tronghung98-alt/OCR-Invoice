"""API JSON/Excel cho tích hợp (yêu cầu đăng nhập bằng session như trang web)."""
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from . import logos, services
from .config import MAX_UPLOAD_MB
from .context import ITEM_PLACEHOLDER_DOCS, PLACEHOLDER_DOCS
from .extractor import ExtractionError, extract_invoice_data
from .security import require_api_user
from .template_engine import TemplateError, fill_template

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_user)])
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _read(f: UploadFile, label: str) -> bytes:
    limit = int(MAX_UPLOAD_MB * 1024 * 1024)
    data = await f.read(limit + 1)
    if not data:
        raise HTTPException(400, f"File {label} rỗng.")
    if len(data) > limit:
        raise HTTPException(413, f"File {label} vượt quá {MAX_UPLOAD_MB:g} MB.")
    return data


def _json_field(raw: str | None, name: str) -> dict:
    raw = (raw or "").strip()
    if not raw or raw == "string":  # Swagger tự điền "string" vào ô trống
        return {}
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    try:
        v = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Trường '{name}' không phải JSON hợp lệ: {e}") from e
    if not isinstance(v, dict):
        raise HTTPException(400, f"Trường '{name}' phải là một object JSON.")
    return v


@router.post("/extract/json", summary="Ảnh/PDF hóa đơn -> JSON dữ liệu")
async def extract_json(file: UploadFile = File(...)):
    data = await _read(file, "hóa đơn")
    try:
        mime = services.detect_invoice_type(data)
    except services.UploadError as e:
        raise HTTPException(400, str(e)) from e
    try:
        return await run_in_threadpool(extract_invoice_data, data, mime)
    except ExtractionError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/fill", summary="JSON dữ liệu -> file Excel (không gọi AI, để thử mẫu)")
async def fill(data: str = Form(...), template: UploadFile | None = File(None), extra: str | None = Form(None)):
    tpl = await _read(template, "mẫu") if template is not None and template.filename else None
    try:
        services.check_template(tpl)
        extra_d = _json_field(extra, "extra")
        content, warnings = await run_in_threadpool(
            fill_template, _json_field(data, "data"), tpl, extra_d,
            lambda: "SO-DE-NGHI-THU",  # thử mẫu không tiêu số đề nghị thật
            None if tpl else logos.logo_bytes(extra_d.get("logo")),
        )
    except (services.UploadError, TemplateError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    return Response(content, media_type=XLSX_MIME, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote('thu_mau.xlsx')}",
        "X-Warnings": quote(json.dumps(warnings, ensure_ascii=False)),
    })


@router.get("/placeholders", summary="Danh sách placeholder dùng được trong mẫu")
def placeholders():
    return {
        "truong_hoa_don": {f"{{{{{k}}}}}": v for k, v in PLACEHOLDER_DOCS.items()},
        "dong_hang_muc": {f"{{{{{k}}}}}": v for k, v in ITEM_PLACEHOLDER_DOCS.items()},
        "tu_nhap": {"{{extra.<ten_bat_ky>}}": "Thông tin nhập trên form (người đề nghị, bộ phận...)"},
    }
