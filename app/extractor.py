"""
Gọi Google Gemini (SDK mới `google-genai`) để trích xuất dữ liệu có cấu trúc từ
ảnh hoặc PDF hóa đơn/chứng từ.
"""
import json
import os
import time
from functools import lru_cache

from google import genai
from google.genai import errors, types

from .schemas import INVOICE_JSON_SCHEMA_DESCRIPTION

# Danh sách model thay đổi theo thời gian - kiểm tra model hiện có tại
# https://ai.google.dev/gemini-api/docs/models và đặt GEMINI_MODEL trong .env
DEFAULT_MODEL = "gemini-3.8-flash"

EXTRACTION_PROMPT = f"""Bạn là trợ lý trích xuất dữ liệu hóa đơn/chứng từ tiếng Việt.
Đọc kỹ tài liệu đính kèm (ảnh hoặc PDF; có thể là hóa đơn GTGT, phiếu chi, giấy đề nghị
thanh toán hoặc chứng từ tương tự) và trả về DUY NHẤT một đối tượng JSON theo schema:

{INVOICE_JSON_SCHEMA_DESCRIPTION}

Quy tắc quan trọng:
- Mọi giá trị tiền và số lượng PHẢI là số, bỏ dấu phân cách nghìn ("2.300.000" -> 2300000).
- Không đọc được hoặc không có thì để null, TUYỆT ĐỐI không bịa số liệu.
- hang_muc: mỗi dòng hàng hóa/dịch vụ/chi phí là một phần tử, bỏ dòng trống.
  KHÔNG đưa dòng thuế, dòng cộng tiền, dòng chiết khấu tổng vào hang_muc.
- Nếu là PDF nhiều trang thì các trang thuộc cùng một hóa đơn (bảng hàng mục có thể
  kéo dài sang trang sau). Nếu tài liệu chứa nhiều hóa đơn khác nhau, chỉ lấy hóa đơn đầu tiên.
- Chỉ trả về JSON hợp lệ, không thêm văn bản nào khác.
"""


class ExtractionError(RuntimeError):
    """transient=True: lỗi tạm thời (Google quá tải, mạng) -> vòng sau thử lại.
    transient=False: lỗi cố định (sai key, sai model, JSON hỏng) -> cần người xử lý."""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


REREAD_PROMPT = """
LẦN ĐỌC TRƯỚC bị hệ thống tự kiểm tra phát hiện các điểm nghi sai sau:
{issues}
Hãy đọc lại THẬT KỸ từng con số (số lượng, đơn giá, thành tiền, tiền thuế, tổng cộng) và ngày
tháng trên chứng từ, phân biệt rõ dấu chấm phân cách nghìn. Nếu chỗ nào mờ không chắc chắn,
để null và đặt kha_nang_doc = "mot_phan" thay vì đoán.
"""


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key or key.startswith(("AIzaSyxxxx", "dien_api_key")):
        raise ExtractionError("Chưa cấu hình GEMINI_API_KEY trong file .env")
    return genai.Client(api_key=key)


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"AI trả về dữ liệu không đúng JSON: {e}. Nội dung: {raw[:500]}"
        ) from e
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ExtractionError("AI trả về JSON nhưng không phải object hóa đơn.")
    return data


def extract_invoice_data(file_bytes: bytes, mime_type: str, hint: list[str] | None = None) -> dict:
    """Gửi ảnh/PDF tới Gemini, trả về dict dữ liệu hóa đơn.
    `hint`: các điểm nghi sai của lần đọc trước (bước tự kiểm tra) để AI đọc lại kỹ hơn.
    Tự thử lại vài lần khi Google quá tải (503) hoặc vượt giới hạn tạm thời (429)."""
    prompt = EXTRACTION_PROMPT
    if hint:
        prompt += REREAD_PROMPT.format(issues="\n".join(f"- {h}" for h in hint))

    models = [os.getenv("GEMINI_MODEL") or DEFAULT_MODEL]
    fallback = os.getenv("GEMINI_FALLBACK_MODEL")
    if fallback:
        models.append(fallback)

    retries = int(os.getenv("GEMINI_RETRIES", "3"))
    last_error, transient = None, False
    for model in models:
        for attempt in range(retries + 1):
            try:
                response = _client().models.generate_content(
                    model=model,
                    contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
                )
                return _parse_json(response.text)
            except ExtractionError:
                raise
            except errors.APIError as e:
                last_error = f"model '{model}': {e}"
                transient = e.code in (429, 500, 502, 503, 504)
                if not transient:
                    break  # sai key, sai model... -> không thử lại
                if attempt < retries:
                    time.sleep(2 ** (attempt + 1))  # đợi 2s, 4s, 8s...
            except Exception as e:  # lỗi mạng
                last_error, transient = f"model '{model}': {e}", True
                break
    raise ExtractionError(f"Gọi Gemini thất bại ({last_error})", transient=transient)
