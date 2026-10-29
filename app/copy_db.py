"""
Chuyển toàn bộ dữ liệu sang CSDL khác (vd từ SQLite sang MySQL), giữ nguyên mã phiếu, mật khẩu,
nhật ký, bộ đếm số đề nghị.

  python -m app.copy_db --to "mysql+pymysql://dntt:matkhau@localhost:3306/dntt"
  python -m app.copy_db --from "sqlite:///data/app.db" --to "mysql+pymysql://..."

Mặc định lấy nguồn là data/app.db. CSDL đích phải TRỐNG (chưa có tài khoản/phiếu nào).
File hóa đơn và file Excel vẫn nằm trong thư mục data/files, không cần chuyển.
"""
import argparse
import sys

from sqlalchemy import func, select, text

from . import config  # noqa: F401  (nạp .env)
from . import models  # noqa: F401
from .db import Base, make_engine
from .migrate import upgrade

CHUNK = 500


def _mask(url: str) -> str:
    """Ẩn mật khẩu khi in đường dẫn CSDL."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        cred, host = rest.rsplit("@", 1)
        user = cred.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


def copy(src_url: str, dst_url: str, force: bool = False) -> dict:
    src, dst = make_engine(src_url), make_engine(dst_url)

    upgrade(src)  # CSDL nguồn là bản cũ -> nâng cấp cấu trúc trước khi đọc
    Base.metadata.create_all(dst)

    tables = Base.metadata.sorted_tables  # đúng thứ tự khóa ngoại: users trước payment_requests...
    with dst.connect() as c:
        existing = {t.name: c.scalar(select(func.count()).select_from(t)) for t in tables}
        if dst.dialect.name in ("mysql", "mariadb"):
            charset = c.scalar(text("SELECT @@character_set_database"))
            if charset != "utf8mb4":
                raise SystemExit(
                    f"CSDL đích đang dùng bảng mã '{charset}', tiếng Việt/tiếng Nhật sẽ bị lỗi.\n"
                    "Tạo lại CSDL bằng: CREATE DATABASE ten_csdl CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
    if any(existing.values()) and not force:
        raise SystemExit(
            "CSDL đích đã có dữ liệu: " + ", ".join(f"{k}={v}" for k, v in existing.items() if v) +
            "\nĐể tránh ghi đè, hãy dùng CSDL trống (hoặc thêm --force để XÓA dữ liệu đích rồi chép lại).")

    counts = {}
    with src.connect() as s, dst.begin() as d:
        if force:
            for t in reversed(tables):
                d.execute(t.delete())
        for t in tables:
            rows, n = [], 0
            for row in s.execute(select(t).order_by(*t.primary_key.columns)):
                rows.append(dict(row._mapping))
                if len(rows) >= CHUNK:
                    d.execute(t.insert(), rows)
                    n += len(rows)
                    rows = []
            if rows:
                d.execute(t.insert(), rows)
                n += len(rows)
            counts[t.name] = n

        if dst.dialect.name == "postgresql":  # PostgreSQL: chỉnh lại bộ tự tăng id sau khi chép id cũ
            for t in tables:
                if "id" in t.c:
                    d.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                                   f"COALESCE((SELECT MAX(id) FROM {t.name}), 0) + 1, false)"))

    # đối chiếu số dòng
    with dst.connect() as c:
        for t in tables:
            got = c.scalar(select(func.count()).select_from(t))
            if got != counts[t.name]:
                raise SystemExit(f"Lỗi: bảng {t.name} chép {counts[t.name]} dòng nhưng CSDL đích có {got}.")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.copy_db", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", default=f"sqlite:///{(config.DATA_DIR / 'app.db').as_posix()}",
                    help="CSDL nguồn (mặc định data/app.db)")
    ap.add_argument("--to", dest="dst", required=True, help="CSDL đích, vd mysql+pymysql://user:pass@host:3306/db")
    ap.add_argument("--force", action="store_true", help="Xóa dữ liệu đang có ở CSDL đích rồi chép lại")
    args = ap.parse_args(argv)
    if args.src == args.dst:
        sys.exit("Nguồn và đích trùng nhau.")
    print(f"Nguồn: {_mask(args.src)}\nĐích:  {_mask(args.dst)}")
    counts = copy(args.src, args.dst, force=args.force)
    for name, n in counts.items():
        print(f"  {name:18} {n:6} dòng")
    print("Xong. Đặt DATABASE_URL trong .env thành CSDL đích rồi khởi động lại server.")


if __name__ == "__main__":
    main()
