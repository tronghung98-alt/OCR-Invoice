"""
Schema JSON mà Gemini phải trả về khi đọc hóa đơn/chứng từ.
Dùng chung cho extractor.py (prompt) và context.py (đọc dữ liệu ra template).
"""

INVOICE_JSON_SCHEMA_DESCRIPTION = """{
  "loai_chung_tu": "string - loại chứng từ, ví dụ: Hóa đơn GTGT / Phiếu chi",
  "so_hoa_don": "string hoặc null",
  "ky_hieu": "string hoặc null",
  "ngay_thang_nam": "string hoặc null - giữ nguyên định dạng trên chứng từ",
  "ngay_iso": "string YYYY-MM-DD hoặc null - cùng ngày trên, chuẩn hóa",
  "ben_ban": {
    "ten_cong_ty": "string hoặc null",
    "ma_so_thue": "string hoặc null",
    "dia_chi": "string hoặc null",
    "dien_thoai": "string hoặc null",
    "so_tai_khoan": "string hoặc null - chỉ số tài khoản",
    "ngan_hang": "string hoặc null - tên ngân hàng/chi nhánh của tài khoản"
  },
  "ben_mua": {
    "ten_cong_ty": "string hoặc null",
    "ten_nguoi_mua": "string hoặc null",
    "dia_chi": "string hoặc null",
    "ma_so_thue": "string hoặc null"
  },
  "hang_muc": [
    {
      "stt": "number hoặc null",
      "ten_hang_hoa_dich_vu": "string",
      "don_vi_tinh": "string hoặc null",
      "so_luong": "number hoặc null",
      "don_gia": "number hoặc null",
      "thanh_tien": "number hoặc null",
      "ghi_chu": "string hoặc null"
    }
  ],
  "tong_tien_truoc_thue": "number hoặc null",
  "thue_suat": "string hoặc null, ví dụ 8%",
  "tien_thue": "number hoặc null",
  "tong_tien_thanh_toan": "number hoặc null",
  "so_tien_bang_chu": "string hoặc null",
  "kha_nang_doc": "tot | mot_phan | khong_doc_duoc - tự đánh giá: tot = đọc rõ toàn bộ; mot_phan = có chỗ mờ/che, không chắc; khong_doc_duoc = ảnh quá mờ hoặc không phải hóa đơn"
}"""
