"""Logo in trên phiếu Excel. Người dùng chọn 1 logo khi gửi hóa đơn (lưu trong extra["logo"]).

Muốn thêm/đổi logo: bỏ file PNG vào app/static/logos/ rồi khai báo trong LOGOS.
Logo chỉ được thay vào mẫu mặc định (mẫu tự làm giữ nguyên ảnh của mẫu đó).
"""
from pathlib import Path

LOGO_DIR = Path(__file__).resolve().parent / "static" / "logos"

# key -> (tên hiển thị, file trong app/static/logos)
LOGOS: dict[str, tuple[str, str]] = {
    "axlab": ("Sorimachi AX LAB", "sorimachi-axlab.png"),
    "vietnam": ("Sorimachi Việt Nam", "sorimachi-vietnam.png"),
}
DEFAULT_LOGO = "axlab"  # trùng với logo đang có trong mẫu mặc định -> phiếu cũ không đổi


def normalize(key) -> str:
    key = str(key or "").strip().lower()
    return key if key in LOGOS else DEFAULT_LOGO


def logo_bytes(key) -> bytes:
    return (LOGO_DIR / LOGOS[normalize(key)][1]).read_bytes()
