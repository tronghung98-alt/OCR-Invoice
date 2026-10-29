"""
Bước 4 trong vòng lặp AI: tự kiểm tra dữ liệu đọc được (tiền, thuế, ngày).

Mỗi vấn đề có:
  code    - mã ngắn
  level   - "critical": không dùng được (ảnh mờ, không đọc được) -> cần người xử lý
            "suspect" : nghi AI đọc sai tiền/thuế/ngày -> AI tự đọc lại
            "info"    : thiếu thông tin phụ -> chỉ nhắc người xem xét
  message - câu hiển thị cho người dùng
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .config import TIMEZONE
from .context import parse_date, to_number


def _fmt(v) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _fmt_qty(v) -> str:
    if float(v).is_integer():
        return _fmt(v)
    return f"{v:,.3f}".rstrip("0").replace(",", "X").replace(".", ",").replace("X", ".")


def _close(a, b, tol_abs=2, tol_rel=0.0005) -> bool:
    return abs(a - b) <= max(tol_abs, abs(b) * tol_rel)


def _rate(text) -> float | None:
    if text is None:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(text))
    return float(m.group(1).replace(",", ".")) / 100 if m else None


def _rates(text) -> list[float]:
    """Mọi mức thuế dạng 'x%' trong chuỗi: '5%, 10%' -> [0.05, 0.1]."""
    if text is None:
        return []
    return sorted({float(x.replace(",", ".")) / 100 for x in re.findall(r"(\d+(?:[.,]\d+)?)\s*%", str(text))})


def check_invoice(data: dict, today: date | None = None) -> list[dict]:
    issues: list[dict] = []

    def add(code, level, message):
        issues.append({"code": code, "level": level, "message": message})

    if not isinstance(data, dict):
        add("invalid", "critical", "Dữ liệu đọc được không đúng định dạng.")
        return issues

    if data.get("kha_nang_doc") == "khong_doc_duoc":
        add("unreadable", "critical", "AI báo ảnh/PDF quá mờ hoặc không phải hóa đơn, không đọc được.")

    items = [it for it in (data.get("hang_muc") or [])
             if isinstance(it, dict) and any(v not in (None, "") for v in it.values())]
    amounts = [to_number(it.get("thanh_tien")) for it in items]
    pre = to_number(data.get("tong_tien_truoc_thue"))
    tax = to_number(data.get("tien_thue"))
    total = to_number(data.get("tong_tien_thanh_toan"))

    if not items and total is None:
        add("empty", "critical", "Không đọc được hàng mục và tổng tiền nào.")
    elif not items:
        add("no_items", "suspect", "Không đọc được hàng mục nào trên hóa đơn.")
    if total is None and items:
        add("no_total", "suspect", "Không đọc được tổng tiền thanh toán.")

    # ---- tiền từng dòng ----
    for i, it in enumerate(items, 1):
        q, p, a = to_number(it.get("so_luong")), to_number(it.get("don_gia")), to_number(it.get("thanh_tien"))
        if a is None:
            add("item_amount_missing", "suspect", f"Dòng {i} không có thành tiền.")
        elif q is not None and p is not None and not _close(q * p, a, tol_abs=2, tol_rel=0.001):
            add("item_mismatch", "suspect",
                f"Dòng {i}: số lượng × đơn giá = {_fmt(q * p)} khác thành tiền {_fmt(a)}.")
        if q is not None and q >= 1000:
            add("qty_large", "info", f"Dòng {i}: số lượng {_fmt_qty(q)} lớn bất thường, kiểm tra lại dấu thập phân "
                                     f"(vd 0,125 chứ không phải 125) và đơn vị tính.")
        if a is not None and a < 0:
            add("negative", "info", f"Dòng {i} có số tiền âm (chiết khấu?), kiểm tra lại.")

    # ---- tổng ----
    if items and None not in amounts:
        s = sum(amounts)
        if pre is not None and not _close(s, pre):
            add("sum_pre", "suspect",
                f"Tổng các hàng mục ({_fmt(s)}) khác tổng trước thuế trên hóa đơn ({_fmt(pre)}).")
        base = pre if pre is not None else s
        if total is not None and not _close(base + (tax or 0), total):
            add("sum_total", "suspect",
                f"Tiền hàng + thuế ({_fmt(base + (tax or 0))}) khác tổng thanh toán trên hóa đơn ({_fmt(total)}).")
    elif pre is not None and total is not None and not _close(pre + (tax or 0), total):
        add("sum_total", "suspect",
            f"Tiền hàng + thuế ({_fmt(pre + (tax or 0))}) khác tổng thanh toán ({_fmt(total)}).")

    # ---- thuế ----
    rates = _rates(data.get("thue_suat"))
    rate = rates[0] if len(rates) == 1 else None
    base = pre if pre is not None else (sum(amounts) if items and None not in amounts else None)
    if len(rates) > 1 and tax is not None and base:
        lo, hi = base * min(rates), base * max(rates)
        if not (lo - 2 <= tax <= hi + 2):
            add("tax_rate", "suspect",
                f"Thuế nhiều mức {data.get('thue_suat')} của {_fmt(base)} phải nằm trong khoảng "
                f"{_fmt(lo)} – {_fmt(hi)}, hóa đơn đọc được {_fmt(tax)}.")
    if rate is not None and tax is not None and base:
        expected = base * rate
        if not _close(tax, expected, tol_abs=2, tol_rel=0.01):
            add("tax_rate", "suspect",
                f"Thuế {data.get('thue_suat')} của {_fmt(base)} phải khoảng {_fmt(expected)}, "
                f"hóa đơn đọc được {_fmt(tax)} (có thể hóa đơn nhiều mức thuế).")
    if tax is not None and base and tax > base * 0.2:
        add("tax_high", "suspect", f"Tiền thuế {_fmt(tax)} lớn bất thường so với tiền hàng {_fmt(base)}.")

    # ---- ngày ----
    today = today or datetime.now(TIMEZONE).date()
    raw_date = data.get("ngay_thang_nam") or data.get("ngay_iso")
    dd, mm, yyyy = parse_date(data)
    if not raw_date:
        add("no_date", "suspect", "Không đọc được ngày hóa đơn.")
    elif not dd:
        add("bad_date", "suspect", f"Ngày hóa đơn '{raw_date}' không hợp lệ.")
    else:
        d = date(int(yyyy), int(mm), int(dd))
        if d > today:
            add("future_date", "suspect", f"Ngày hóa đơn {dd}/{mm}/{yyyy} nằm trong tương lai.")
        elif (today - d).days > 730:
            add("old_date", "info", f"Hóa đơn ngày {dd}/{mm}/{yyyy} đã hơn 2 năm.")

    # ---- thông tin phụ ----
    if not data.get("so_hoa_don"):
        add("no_number", "info", "Không đọc được số hóa đơn.")
    if not (data.get("ben_ban") or {}).get("ten_cong_ty"):
        add("no_seller", "info", "Không đọc được tên bên bán.")
    if data.get("kha_nang_doc") == "mot_phan":
        add("partial", "info", "AI báo một phần hóa đơn bị mờ, đối chiếu kỹ với bản gốc.")
    return issues


def score(issues: list[dict]) -> tuple[int, int, int]:
    """Càng nhỏ càng tốt - dùng để chọn kết quả đọc tốt nhất giữa các lần đọc."""
    return (sum(i["level"] == "critical" for i in issues),
            sum(i["level"] == "suspect" for i in issues),
            sum(i["level"] == "info" for i in issues))


def has(issues, level) -> bool:
    return any(i["level"] == level for i in issues)
