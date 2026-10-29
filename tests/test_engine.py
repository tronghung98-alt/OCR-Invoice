"""Test phần đọc dữ liệu và điền mẫu Excel."""
import io
from datetime import date

import openpyxl
import pytest

from app import numbering
from app.context import parse_date, to_number
from app.numwords import so_thanh_chu
from app.template_engine import TemplateError, fill_template, shift_formula

SAMPLE = {
    "so_hoa_don": "123", "ky_hieu": "1C26TAA", "ngay_thang_nam": "05/10/2026",
    "ben_ban": {"ten_cong_ty": "Công ty ABC", "so_tai_khoan": "0123", "ngan_hang": "VCB"},
    "hang_muc": [
        {"ten_hang_hoa_dich_vu": "RAM", "so_luong": 2, "don_gia": 100000, "thanh_tien": 200000},
        {"ten_hang_hoa_dich_vu": "Phí lắp đặt", "thanh_tien": 50000},
        {"ten_hang_hoa_dich_vu": "Cáp", "so_luong": 1, "don_gia": 10000, "thanh_tien": 10000},
    ],
    "tong_tien_truoc_thue": 260000, "thue_suat": "8%", "tien_thue": 20800,
    "tong_tien_thanh_toan": 280800,
}


def wb(content):
    return openpyxl.load_workbook(io.BytesIO(content))


def custom_template(**cells):
    w = openpyxl.Workbook()
    for k, v in cells.items():
        w.active[k] = v
    buf = io.BytesIO()
    w.save(buf)
    return buf.getvalue()


def test_helpers():
    assert to_number("2.300.000") == 2300000
    assert to_number("1,5") == 1.5
    assert parse_date({"ngay_thang_nam": "2026-10-05"}) == ("05", "10", "2026")
    assert so_thanh_chu(3699000) == "Ba triệu sáu trăm chín mươi chín nghìn đồng"
    assert so_thanh_chu(1000005) == "Một triệu không trăm lẻ năm đồng"


def test_shift_formula():
    assert shift_formula("=SUM(K16:K18)", 16, 2, "S") == "=SUM(K16:K20)"
    assert shift_formula("=SUM(K10:K16)", 16, 2, "S") == "=SUM(K10:K18)"
    assert shift_formula('="K20"&K20', 16, 2, "S") == '="K20"&K22'
    assert shift_formula("=Other!K20", 16, 2, "S") == "=Other!K20"


def test_default_template():
    content, warnings = fill_template(SAMPLE, extra={"nguoi_de_nghi": "A"}, so_de_nghi_provider=lambda: "X1")
    assert warnings == []
    w = wb(content)
    assert w.sheetnames == ["Giay de nghi thanh toan"]
    ws = w.active
    assert ws["D6"].value == "Số（No.) X1"
    # Số tiền ghi số tĩnh (không công thức) để hiện đúng trên Outlook/Gmail/điện thoại - xem README (BUG-06)
    assert [ws[f"K{r}"].value for r in (16, 17, 18)] == [200000, 50000, 10000]
    assert ws["D19"].value == "VAT 8%" and ws["K19"].value == 20800
    assert ws["K21"].value == 280800 and ws["K23"].value == 280800       # tổng cộng, số tiền bổ sung
    assert ws["E24"].value == "Hai trăm tám mươi nghìn tám trăm đồng"
    assert "TODAY()" in ws["C8"].value                                     # công thức của mẫu được giữ
    assert ws.print_area.endswith("$M$36")


def test_self_check():
    from app.checks import check_invoice
    assert check_invoice(SAMPLE) == []
    codes = lambda d: {i["code"]: i["level"] for i in check_invoice(d)}  # noqa: E731
    assert codes(dict(SAMPLE, tong_tien_thanh_toan=999))["sum_total"] == "suspect"
    assert codes(dict(SAMPLE, tien_thue=99999))["tax_rate"] == "suspect"
    assert codes(dict(SAMPLE, ngay_thang_nam="05/10/2099"))["future_date"] == "suspect"
    assert codes(dict(SAMPLE, ngay_thang_nam="31/02/2026"))["bad_date"] == "suspect"
    assert codes({"kha_nang_doc": "khong_doc_duoc"})["unreadable"] == "critical"
    assert codes({"hang_muc": []})["empty"] == "critical"
    bad_item = dict(SAMPLE, hang_muc=[{"ten_hang_hoa_dich_vu": "A", "so_luong": 2, "don_gia": 100, "thanh_tien": 300}])
    assert codes(bad_item)["item_mismatch"] == "suspect"


def test_total_mismatch_warning():
    _, warnings = fill_template(dict(SAMPLE, tong_tien_thanh_toan=999), so_de_nghi_provider=lambda: "X")
    assert any("khác tổng thanh toán" in w for w in warnings)


def test_custom_template_and_unknown_placeholder():
    tpl = custom_template(A1="HĐ {{so_hoa_don}}", A3="{{item.ten_hang_hoa_dich_vu}}", C3="{{item.thanh_tien}}",
                          C5="=SUM(C3:C3)", B7="{{sai_ten}}")
    content, warnings = fill_template(SAMPLE, tpl)
    ws = wb(content).active
    assert ws["A1"].value == "HĐ 123"
    assert ws["C7"].value == "=SUM(C3:C5)"
    assert any("sai_ten" in w for w in warnings)


def test_item_placeholders_on_two_rows_rejected():
    with pytest.raises(TemplateError):
        fill_template(SAMPLE, custom_template(A1="{{item.stt}}", A2="{{item.ten_hang_hoa_dich_vu}}"))


def test_numbering_db_daily_reset():
    d1, d2 = date(2026, 9, 28), date(2026, 9, 29)
    assert numbering.next_so_de_nghi(d1) == "01-2809/ĐN-2026"
    assert numbering.next_so_de_nghi(d1) == "02-2809/ĐN-2026"
    assert numbering.next_so_de_nghi(d2) == "01-2909/ĐN-2026"


def test_numbering_not_consumed_when_template_has_no_placeholder():
    fill_template(SAMPLE, custom_template(A1="{{so_hoa_don}}"))
    assert numbering.next_so_de_nghi().startswith("01-")


# ---------------------------------------------------------------- logo
def _logo_of(content):
    """(ảnh logo trong file kết quả dạng bytes, anchor)."""
    ws = wb(content).worksheets[0]
    assert len(ws._images) == 1
    img = ws._images[0]
    return img._data(), img.anchor


def test_choose_logo_replaces_default_logo_keeps_position():
    from PIL import Image

    from app import logos
    plain, _ = fill_template(SAMPLE, None, {}, lambda: "X1")
    _, old_anchor = _logo_of(plain)
    for key in logos.LOGOS:
        content, warnings = fill_template(SAMPLE, None, {}, lambda: "X1", logo=logos.logo_bytes(key))
        assert not any("logo" in w for w in warnings)
        data, anchor = _logo_of(content)
        want = Image.open(io.BytesIO(logos.logo_bytes(key)))
        got = Image.open(io.BytesIO(data))
        assert got.size == want.size                       # đúng logo đã chọn
        assert (anchor._from.col, anchor._from.row) == (old_anchor._from.col, old_anchor._from.row)
        ratio = anchor.ext.width / anchor.ext.height       # không bị méo
        assert abs(ratio - want.width / want.height) < 0.01
        assert 400_000 < anchor.ext.height < 480_000       # cao bằng logo cũ (~0,46 cm)


def test_logo_on_template_without_image_warns():
    from app import logos
    _, warnings = fill_template(SAMPLE, custom_template(A1="{{so_hoa_don}}"), {}, logo=logos.logo_bytes("vietnam"))
    assert any("không có ảnh logo" in w for w in warnings)


def test_logo_key_normalize():
    from app import logos
    assert logos.normalize("VIETNAM") == "vietnam"
    assert logos.normalize("../../etc/passwd") == logos.DEFAULT_LOGO
    assert logos.normalize(None) == logos.DEFAULT_LOGO
