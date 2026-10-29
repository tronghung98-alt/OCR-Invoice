"""
Nâng cấp CSDL cũ lên cấu trúc mới mà không mất dữ liệu (SQLite, MySQL/MariaDB, PostgreSQL):
  * tạo bảng mới (request_events),
  * thêm cột còn thiếu vào bảng cũ (ALTER TABLE ... ADD COLUMN),
  * đổi trạng thái cũ sang quy trình mới (done -> review, failed -> needs_human).
"""
import logging

from sqlalchemy import inspect, text

from . import models
from .db import Base, engine as default_engine

log = logging.getLogger("invoice")

OLD_STATUS = {"done": models.REVIEW, "failed": models.NEEDS_HUMAN}


def upgrade(engine=None):
    engine = engine or default_engine
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    q = engine.dialect.identifier_preparer.quote  # "cot" (SQLite/PG), `cot` (MySQL)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            added = False
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {q(table.name)} ADD COLUMN {q(col.name)} {ddl_type}"))
                default = getattr(col.default, "arg", None)
                if default is not None and not callable(default):
                    conn.execute(text(f"UPDATE {q(table.name)} SET {q(col.name)} = :v"), {"v": default})
                log.warning("Đã thêm cột %s.%s", table.name, col.name)
                added = True
            if added:
                for idx in table.indexes:  # index của cột mới thêm
                    idx.create(conn, checkfirst=True)
        for old, new in OLD_STATUS.items():
            r = conn.execute(text(f"UPDATE {q('payment_requests')} SET status = :new WHERE status = :old"),
                             {"new": new, "old": old})
            if r.rowcount:
                log.warning("Đã chuyển %s phiếu từ trạng thái '%s' sang '%s'", r.rowcount, old, new)
