"""
Chuẩn hóa JSON do AI trả về thành "context" để điền vào template:
  * ép kiểu số tiền (phòng khi AI vẫn trả "2.300.000"),
  * tách ngày/tháng/năm,
  * tạo các trường suy ra (nội dung thanh toán, ghi chú, số tiền bằng chữ ...),
  * kiểm tra tổng tiền và sinh cảnh báo.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .config import TIMEZONE

from .numbering import next_so_de_nghi
from .numwords import so_thanh_chu

MONEY_KEYS = ("tong_tien_truoc_thue", "tien_thue", "tong_tien_thanh_toan")
ITEM_NUM_KEYS = ("stt", "so_luong", "don_gia", "thanh_tien")

KNOWN_ITEM_KEYS = {
    "stt", "ten_hang_hoa_dich_vu", "don_vi_tinh", "so_luong", "don_gia", "thanh_tien", "ghi_chu",
}

# Tên placeholder hợp lệ (ngoài item.* và extra.*), kèm mô tả - dùng cho /placeholders
PLACEHOLDER_DOCS: dict[str, str] = {
    "loai_chung_tu": "Loại chứng từ",
    "so_hoa_don": "Số hóa đơn",
    "ky_hieu": "Ký hiệu hóa đơn",
    "ngay_thang_nam": "Ngày trên hóa đơn (nguyên văn)",
    "ngay.dd": "Ngày hóa đơn - ngày (2 chữ số)",
    "ngay.mm": "Ngày hóa đơn - tháng (2 chữ số)",
    "ngay.yyyy": "Ngày hóa đơn - năm",
    "ngay.iso": "Ngày hóa đơn dạng YYYY-MM-DD",
    "hom_nay.dd": "Ngày hôm nay - ngày",
    "hom_nay.mm": "Ngày hôm nay - tháng",
    "hom_nay.yyyy": "Ngày hôm nay - năm",
    "ben_ban.ten_cong_ty": "Bên bán - tên công ty",
    "ben_ban.ma_so_thue": "Bên bán - MST",
    "ben_ban.dia_chi": "Bên bán - địa chỉ",
    "ben_ban.dien_thoai": "Bên bán - điện thoại",
    "ben_ban.so_tai_khoan": "Bên bán - số tài khoản",
    "ben_ban.ngan_hang": "Bên bán - ngân hàng",
    "ben_mua.ten_cong_ty": "Bên mua - tên công ty",
    "ben_mua.ten_nguoi_mua": "Bên mua - người mua",
    "ben_mua.dia_chi": "Bên mua - địa chỉ",
    "ben_mua.ma_so_thue": "Bên mua - MST",
    "tong_tien_truoc_thue": "Tổng tiền trước thuế (số)",
    "thue_suat": "Thuế suất, vd 8%",
    "tien_thue": "Tiền thuế (số)",
    "tong_tien_thanh_toan": "Tổng thanh toán (số)",
    "so_tien_bang_chu": "Số tiền bằng chữ (tự sinh nếu hóa đơn không có)",
    "so_hang_muc": "Số dòng hàng mục",
    "tong_cong": "Tổng cộng = các hàng mục + thuế (số)",
    "so_tien_bo_sung": "Số tiền chi/thu bổ sung = Tổng cộng - Đã tạm ứng (số)",
    "so_de_nghi": "Số đề nghị tự tăng, vd 01-2809/ĐN-2026 (hoặc lấy từ extra.so_de_nghi nếu có)",
    "noi_dung_thanh_toan": "'Thanh toán hóa đơn số X - Tên bên bán'",
    "ghi_chu_hoa_don": "Ghi chú người dùng nhập trên trang sửa (bỏ trống thì để trống)",
    "ghi_chu_tu_dong": "'Theo hóa đơn số X, ký hiệu Y, ngày Z' (bỏ phần thiếu)",
    "tai_khoan_ben_ban": "'Số TK tại Ngân hàng' (bỏ phần thiếu)",
}

ITEM_PLACEHOLDER_DOCS = {
    f"item.{k}": v for k, v in {
        "stt": "STT (tự đánh nếu hóa đơn không có)",
        "ten_hang_hoa_dich_vu": "Tên hàng hóa/dịch vụ",
        "don_vi_tinh": "Đơn vị tính",
        "so_luong": "Số lượng",
        "don_gia": "Đơn giá",
        "thanh_tien": "Thành tiền - số trên hóa đơn (thiếu thì SL × ĐG)",
        "ghi_chu": "Ghi chú dòng",
    }.items()
}


def is_known_key(key: str) -> bool:
    return key in PLACEHOLDER_DOCS


def to_number(v):
    """'2.300.000' -> 2300000 ; '1,5' -> 1.5 ; 3425000 -> 3425000 ; rác -> None"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s or s in "-.,":
        return None
    if "." in s and "," in s:  # dấu xuất hiện sau cùng là dấu thập phân
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif s.count(".") > 1 or s.count(",") > 1:
        s = s.replace(".", "").replace(",", "")
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        head, tail = s.split(sep)
        # "2.300" = nghìn; nhưng "0,125" / ".5" (phần nguyên 0 hoặc trống) luôn là số thập phân
        thousands = len(tail) == 3 and head.lstrip("-") not in ("", "0")
        s = head + tail if thousands else head + "." + tail
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def to_number_vn(v):
    """Số nhập trên form theo quy ước Việt Nam: dấu phẩy là thập phân, dấu chấm là nghìn.
    '0,125' -> 0.125 ; '1,5' -> 1.5 ; '1.250,5' -> 1250.5 ; '1.500' -> 1500. Dùng cho ô Số lượng."""
    if v is None or isinstance(v, (bool, int, float)):
        return to_number(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if s.count(",") == 1:
        head, tail = s.split(",")
        try:
            f = float(head.replace(".", "") + "." + tail)
        except ValueError:
            return None
        return int(f) if f.is_integer() else f
    return to_number(s)


def parse_date(data: dict) -> tuple[str | None, str | None, str | None]:
    iso = data.get("ngay_iso")
    if isinstance(iso, str):
        m = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})", iso)
        if m:
            return m.group(3).zfill(2), m.group(2).zfill(2), m.group(1)
    raw = data.get("ngay_thang_nam")
    if not raw:
        return None, None, None
    nums = re.findall(r"\d+", str(raw))
    if len(nums) < 3:
        return None, None, None
    a, b, c = nums[0], nums[1], nums[2]
    if len(a) == 4:  # YYYY-MM-DD
        yyyy, mm, dd = a, b, c
    else:            # DD/MM/YYYY hoặc DD/MM/YY
        dd, mm, yyyy = a, b, c
        if len(yyyy) == 2:
            yyyy = "20" + yyyy
    try:
        date(int(yyyy), int(mm), int(dd))
    except ValueError:
        return None, None, None
    return dd.zfill(2), mm.zfill(2), yyyy


def _join(parts, sep):
    return sep.join(p for p in parts if p) or None


def build_item_context(item: dict, index: int) -> dict:
    ctx = {k: item.get(k) for k in KNOWN_ITEM_KEYS}
    for k in ITEM_NUM_KEYS:
        ctx[k] = to_number(ctx[k])
    if ctx["thanh_tien"] is None and ctx["so_luong"] is not None and ctx["don_gia"] is not None:
        ctx["thanh_tien"] = ctx["so_luong"] * ctx["don_gia"]
    if ctx["stt"] is None:
        ctx["stt"] = index + 1
    return ctx


def build_context(data: dict, extra: dict, so_de_nghi_provider=None):
    warnings: list[str] = []
    if not isinstance(data, dict):
        raise ValueError("Dữ liệu hóa đơn phải là một object JSON.")

    ben_ban = data.get("ben_ban") or {}
    ben_mua = data.get("ben_mua") or {}
    items = [
        it for it in (data.get("hang_muc") or [])
        if isinstance(it, dict) and any(v not in (None, "") for v in it.values())
    ]

    flat: dict = {k: data.get(k) for k in PLACEHOLDER_DOCS if "." not in k}
    for k in MONEY_KEYS:
        flat[k] = to_number(flat[k])
    for k in ("ten_cong_ty", "ma_so_thue", "dia_chi", "dien_thoai", "so_tai_khoan", "ngan_hang"):
        flat[f"ben_ban.{k}"] = ben_ban.get(k)
    for k in ("ten_cong_ty", "ten_nguoi_mua", "dia_chi", "ma_so_thue"):
        flat[f"ben_mua.{k}"] = ben_mua.get(k)

    dd, mm, yyyy = parse_date(data)
    flat.update({"ngay.dd": dd, "ngay.mm": mm, "ngay.yyyy": yyyy,
                 "ngay.iso": f"{yyyy}-{mm}-{dd}" if dd else None})
    t = datetime.now(TIMEZONE).date()
    flat.update({"hom_nay.dd": f"{t.day:02d}", "hom_nay.mm": f"{t.month:02d}",
                 "hom_nay.yyyy": str(t.year)})

    so_hd = data.get("so_hoa_don")
    flat["so_hang_muc"] = len(items)
    flat["noi_dung_thanh_toan"] = _join(
        [f"Thanh toán hóa đơn số {so_hd}" if so_hd else "Thanh toán hóa đơn",
         ben_ban.get("ten_cong_ty")], " - ")
    ghi_chu_tu_dong = _join(
        [f"Theo hóa đơn số {so_hd}" if so_hd else None,
         f"ký hiệu {data['ky_hieu']}" if data.get("ky_hieu") else None,
         f"ngày {data['ngay_thang_nam']}" if data.get("ngay_thang_nam") else None], ", ")
    # Ghi chú trên phiếu: CHỈ ghi khi người dùng nhập; bỏ trống -> phiếu để trống
    flat["ghi_chu_hoa_don"] = str((extra or {}).get("ghi_chu") or "").strip() or None
    flat["ghi_chu_tu_dong"] = ghi_chu_tu_dong  # vẫn dùng được nếu mẫu Excel nào cần
    flat["tai_khoan_ben_ban"] = _join(
        [ben_ban.get("so_tai_khoan"),
         f"tại {ben_ban['ngan_hang']}" if ben_ban.get("ngan_hang") else None], " ")

    # ---- Tự kiểm tra (dùng chung với bước 4 của AI) ----
    from .checks import check_invoice  # import muộn để tránh vòng lặp import
    warnings.extend(i["message"] for i in check_invoice(data))

    item_ctx = [build_item_context(it, i) for i, it in enumerate(items)]
    items_sum = (sum(c["thanh_tien"] for c in item_ctx)
                 if item_ctx and all(c["thanh_tien"] is not None for c in item_ctx) else None)
    if flat["tong_tien_thanh_toan"] is None and items_sum is not None:
        flat["tong_tien_thanh_toan"] = items_sum + (flat["tien_thue"] or 0)
    # Tổng cộng trên phiếu = cộng các dòng hàng mục + thuế (giống =SUM cũ trong mẫu)
    flat["tong_cong"] = (items_sum + (flat["tien_thue"] or 0)) if items_sum is not None \
        else flat["tong_tien_thanh_toan"]
    tam_ung = to_number((extra or {}).get("da_tam_ung")) or 0
    flat["so_tien_bo_sung"] = None if flat["tong_cong"] is None else flat["tong_cong"] - tam_ung

    if not flat.get("so_tien_bang_chu") and flat["tong_cong"] is not None:
        try:
            flat["so_tien_bang_chu"] = so_thanh_chu(flat["tong_cong"])
        except ValueError:
            pass

    # Số đề nghị: chỉ sinh số mới (và tăng bộ đếm) khi template thật sự dùng {{so_de_nghi}}
    flat["so_de_nghi"] = (extra or {}).get("so_de_nghi") or so_de_nghi_provider or next_so_de_nghi

    ctx = {"flat": flat, "extra": {str(k): v for k, v in (extra or {}).items()}}
    return ctx, items, warnings
