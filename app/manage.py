"""
Công cụ dòng lệnh.
  python -m app.manage create-user <ten_dang_nhap> [--admin] [--manager --email a@b.vn] [--name "Họ tên"] [--dept "Bộ phận"]
  python -m app.manage reset-password <ten_dang_nhap>
  python -m app.manage list-users
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from . import config  # noqa: F401
from .db import SessionLocal
from .migrate import upgrade
from .models import User
from .security import hash_password, validate_new_password, validate_username


def _ask_password() -> str:
    while True:
        p1 = getpass.getpass("Mật khẩu (không hiện khi gõ): ")
        if err := validate_new_password(p1):
            print(err)
            continue
        if p1 != getpass.getpass("Nhập lại mật khẩu: "):
            print("Hai lần nhập không khớp.")
            continue
        return p1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("username")
    c.add_argument("--admin", action="store_true")
    c.add_argument("--name", default="")
    c.add_argument("--dept", default="")
    c.add_argument("--email", default="")
    c.add_argument("--manager", action="store_true", help="Quản lý nhận mail xác nhận (cần --email)")
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    sub.add_parser("list-users")
    args = ap.parse_args(argv)

    upgrade()
    with SessionLocal() as db:
        if args.cmd == "list-users":
            for u in db.scalars(select(User).order_by(User.username)):
                roles = ",".join(r for r, on in (("admin", u.is_admin), ("quan-ly", u.is_manager)) if on) or "nhan-vien"
                print(f"{u.username:20} {roles:16} {u.email or '':30} {'' if u.is_active else '(da khoa)'}")
            return
        username = args.username.strip().lower()
        user = db.scalar(select(User).where(User.username == username))
        if args.cmd == "create-user":
            if err := validate_username(username):
                sys.exit(err)
            if user:
                sys.exit(f"Tài khoản '{username}' đã tồn tại.")
            if args.manager and not args.email:
                sys.exit("Quản lý cần có --email.")
            db.add(User(username=username, password_hash=hash_password(_ask_password()),
                        full_name=args.name or username, department=args.dept, is_admin=args.admin,
                        email=args.email or None, is_manager=args.manager))
            db.commit()
            print(f"Đã tạo tài khoản '{username}'{' (quản trị viên)' if args.admin else ''}.")
        elif args.cmd == "reset-password":
            if not user:
                sys.exit(f"Không có tài khoản '{username}'.")
            user.password_hash = hash_password(_ask_password())
            user.is_active = True
            db.commit()
            print(f"Đã đặt lại mật khẩu cho '{username}'.")


if __name__ == "__main__":
    main()
