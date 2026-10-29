"""Gửi mail kèm file Excel và nút Xác nhận cho quản lý."""
import html
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import config


class MailError(RuntimeError):
    pass


def _vnd(v):
    return "" if v is None else f"{v:,.0f}".replace(",", ".") + " đ"


def build_message(*, to: str, sender_name: str, reply_to: str | None, so_de_nghi: str,
                  summary: dict, note: str, confirm_url: str,
                  attachment: bytes, attachment_name: str) -> EmailMessage:
    msg = EmailMessage()
    from_addr = config.MAIL_FROM or "de-nghi-thanh-toan@localhost"
    msg["From"] = formataddr(("Đề nghị thanh toán", from_addr))
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = f"[Đề nghị thanh toán] {so_de_nghi} - {sender_name} - {_vnd(summary.get('tong_tien'))}"
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1] or "localhost")

    rows = [("Số đề nghị", so_de_nghi), ("Người đề nghị", sender_name),
            ("Bên bán", summary.get("ben_ban")), ("Số hóa đơn", summary.get("so_hoa_don")),
            ("Ngày hóa đơn", summary.get("ngay_hoa_don")), ("Tổng thanh toán", _vnd(summary.get("tong_tien")))]
    rows = [(k, v) for k, v in rows if v]

    text_body = "\n".join([
        "Kính gửi anh/chị,", "",
        f"{sender_name} gửi giấy đề nghị thanh toán (file Excel đính kèm).", "",
        *[f"{k}: {v}" for k, v in rows], "",
        *( [f"Ghi chú: {note}", ""] if note else [] ),
        "Sau khi xem, vui lòng mở đường dẫn sau và bấm Xác nhận:", confirm_url, "",
        f"Đường dẫn có hiệu lực {config.CONFIRM_LINK_DAYS} ngày.",
    ])

    e = html.escape
    table = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#5b6878">{e(k)}</td>'
        f'<td style="padding:4px 0;font-weight:600">{e(str(v))}</td></tr>' for k, v in rows)
    note_html = (f'<p style="margin:16px 0;padding:12px;background:#f6f8fb;border-left:3px solid #1d5faf">'
                 f'{e(note)}</p>' if note else "")
    html_body = f"""<!doctype html><html><body style="margin:0;padding:24px;background:#f6f8fb;
font-family:Segoe UI,Arial,sans-serif;color:#1b2430;font-size:15px;line-height:1.5">
<div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #d5dde7;border-radius:8px;padding:28px">
<p style="margin:0 0 16px">Kính gửi anh/chị,</p>
<p style="margin:0 0 16px"><strong>{e(sender_name)}</strong> gửi giấy đề nghị thanh toán. File Excel đính kèm trong mail này.</p>
<table style="border-collapse:collapse;margin:0 0 8px">{table}</table>
{note_html}
<p style="margin:24px 0 8px">Sau khi xem, bấm nút dưới đây để xác nhận đã đọc:</p>
<p style="margin:0 0 24px"><a href="{e(confirm_url)}" style="display:inline-block;background:#c23b2e;color:#fff;
text-decoration:none;font-weight:700;padding:12px 28px;border-radius:4px">Xác nhận</a></p>
<p style="margin:0;color:#5b6878;font-size:13px">Nút không bấm được? Mở đường dẫn: <br>
<a href="{e(confirm_url)}" style="color:#1d5faf;word-break:break-all">{e(confirm_url)}</a><br>
Đường dẫn có hiệu lực {config.CONFIRM_LINK_DAYS} ngày.</p>
</div></body></html>"""

    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    msg.add_attachment(attachment, maintype="application",
                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       filename=attachment_name)
    return msg


def send(msg: EmailMessage) -> str:
    """Gửi mail. Trả về mô tả ngắn cách đã gửi (để ghi nhật ký)."""
    if not config.SMTP_HOST:
        config.OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = config.OUTBOX_DIR / f"{datetime.now():%Y%m%d_%H%M%S_%f}.eml"
        path.write_bytes(bytes(msg))
        return f"chưa cấu hình SMTP, mail được lưu tại {path}"
    try:
        if config.SMTP_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        with server:
            if config.SMTP_SECURITY == "starttls":
                server.starttls(context=ssl.create_default_context())
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Không gửi được mail qua {config.SMTP_HOST}:{config.SMTP_PORT}: {e}") from e
    return f"đã gửi qua {config.SMTP_HOST}"
