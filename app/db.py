"""Kết nối CSDL (SQLAlchemy). SQLite mặc định; MySQL/MariaDB hoặc PostgreSQL qua DATABASE_URL."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

def make_engine(url: str):
    """Tạo kết nối với thiết lập phù hợp từng loại CSDL."""
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, pool_pre_ping=True)

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")   # đọc/ghi đồng thời tốt hơn
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        return eng
    if url.startswith(("mysql", "mariadb")):
        # utf8mb4: lưu đủ tiếng Việt, tiếng Nhật; pool_recycle: MySQL tự cắt kết nối rảnh sau vài giờ
        args = {} if "charset=" in url else {"charset": "utf8mb4"}
        return create_engine(url, connect_args=args, pool_pre_ping=True, pool_recycle=1800)
    return create_engine(url, pool_pre_ping=True)


engine = make_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
