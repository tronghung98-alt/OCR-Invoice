# Đề nghị thanh toán (Invoice to Excel)

Web nội bộ: nhân viên đăng nhập, gửi ảnh hoặc PDF hóa đơn, Google Gemini đọc nội dung và
điền vào mẫu "Giấy đề nghị thanh toán / Quyết toán tạm ứng". Mọi phiếu được lưu lại để
xem, tải lại, tạo lại.

## Quy trình

```
Nhân viên                         AI / hệ thống
---------                         -------------
Đăng nhập  ---------------------> Hiện danh sách đã gửi, kèm trạng thái xác nhận
Chọn nhiều file hóa đơn
     |
     v      Vòng lặp AI (tự động, chạy liên tục, đọc từng file)
            1. Nhận hóa đơn chờ đọc (không có thì chờ)
            2. Đọc ảnh/PDF bằng Gemini
            4. Tự kiểm tra tiền, thuế, ngày; nghi sai thì tự đọc lại kèm gợi ý lỗi
               ảnh mờ, không đọc được -> "Cần người xử lý"
            3. Xuất file Excel
            5. Chuyển "Chờ xem xét"
     |
Xem và sửa trực tiếp  (B: bấm "AI đọc lại" -> vòng sau AI đọc lại)
     |
Xác nhận và gửi mail  (A: chưa đúng -> quay lại sửa)
     |  ----------------------------> Gửi mail kèm Excel, có nút Xác nhận
Quản lý: đọc mail, bấm Xác nhận ----> Danh sách: "Đã xác nhận đọc"
```

Trạng thái phiếu: **Chờ AI đọc**, **AI đang đọc**, **Cần người xử lý**, **Chờ xem xét**,
**Chờ quản lý xác nhận**, **Đã xác nhận đọc**. Trang danh sách có tab theo từng nhóm.
Mỗi phiếu có **nhật ký** ghi lại từng bước: ai làm gì, lúc nào, AI đọc mấy lần.

- Gemini quá tải (503): phiếu tự quay lại hàng đợi, AI thử lại sau 30 giây, 1, 2, 4 phút;
  quá `MAX_TRANSIENT_ATTEMPTS` lần thì chuyển "Cần người xử lý".
- Trang xác nhận của quản lý **không cần đăng nhập**. Mở đường dẫn chỉ hiển thị phiếu; phải bấm nút
  "Xác nhận đã đọc" mới ghi nhận, để trình quét link của Outlook/Gmail không vô tình xác nhận thay.
- Gửi lại mail hoặc "Thu hồi để sửa" làm đường dẫn cũ hết hiệu lực.
- Phiếu đã được quản lý xác nhận bị khóa: không sửa, không đọc lại; chỉ quản trị viên xóa được.

## Cấu hình gửi mail

Chưa cấu hình SMTP thì mail được lưu thành file `.eml` trong `data/outbox` (mở bằng Outlook để xem),
dùng để thử quy trình. Gửi thật: điền `SMTP_*` trong `.env` (xem `.env.example`).

- **Gmail**: bật xác minh 2 bước, tạo *App Password* tại https://myaccount.google.com/apppasswords,
  dùng mật khẩu 16 ký tự đó cho `SMTP_PASSWORD`.
- **Microsoft 365**: `smtp.office365.com:587`; quản trị viên M365 cần bật *SMTP AUTH* cho hộp thư gửi.
- Đặt `PUBLIC_BASE_URL` là địa chỉ quản lý mở được từ máy của họ (vd `http://192.168.1.25:8000`),
  nếu không nút Xác nhận sẽ trỏ về `localhost`.
- Người nhận: quản trị viên vào **Người dùng**, nhập mail và bấm **Đặt làm quản lý**.
  Khi gửi, nhân viên chọn quản lý trong danh sách hoặc gõ địa chỉ khác.

## Cấu trúc

```
app/
  main.py            khởi động app, session, xử lý lỗi
  web.py             các trang web
  api.py             API JSON (cần đăng nhập)
  services.py        các bước của quy trình (nhận, đọc, kiểm tra, xuất Excel, gửi, xác nhận)
  worker.py          vòng lặp AI chạy liên tục
  checks.py          bước tự kiểm tra tiền, thuế, ngày
  mailer.py          gửi mail kèm Excel và nút Xác nhận
  migrate.py         nâng cấp CSDL cũ, giữ nguyên dữ liệu
  models.py, db.py   bảng CSDL, kết nối
  security.py        mật khẩu (scrypt), đăng nhập, CSRF, chống dò mật khẩu
  numbering.py       số đề nghị tự tăng
  manage.py          lệnh quản trị (tạo tài khoản, đặt lại mật khẩu)
  copy_db.py         chuyển dữ liệu sang CSDL khác (SQLite -> MySQL/PostgreSQL)
  extractor.py       gọi Gemini
  template_engine.py, context.py, numwords.py   điền mẫu Excel
  templates/default.xlsx   mẫu mặc định
  web_templates/, static/  giao diện
data/                (tự tạo) app.db, file hóa đơn và Excel của từng phiếu
```

## Cài đặt và chạy (Windows PowerShell)

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # rồi mở .env, điền GEMINI_API_KEY và ADMIN_PASSWORD
uvicorn app.main:app --reload
```

Mở http://localhost:8000 và đăng nhập bằng `ADMIN_USERNAME` / `ADMIN_PASSWORD` trong `.env`.
Tài khoản này chỉ được tạo khi CSDL chưa có tài khoản nào. Đăng nhập xong, vào
**Tài khoản** để đổi mật khẩu, rồi xóa hai dòng `ADMIN_*` khỏi `.env`.

Cách khác để tạo tài khoản quản trị (hỏi mật khẩu trực tiếp, không lưu trong file):

```powershell
python -m app.manage create-user admin --admin --name "Phan Ái Nguyễn"
python -m app.manage reset-password admin     # quên mật khẩu
python -m app.manage list-users
```

### Cho máy khác trong mạng LAN dùng

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Máy khác mở `http://<IP-máy-chạy-server>:8000`. Windows Firewall sẽ hỏi cho phép Python; chọn
**Private networks**. Khi chạy thật (không sửa code), bỏ `--reload`.

## Cơ sở dữ liệu

Mặc định dùng **SQLite**: một file `data/app.db`, không cần cài gì, đủ cho vài chục người dùng.
Bảng: `users` (tài khoản, mail, quyền quản lý), `payment_requests` (phiếu, dữ liệu hóa đơn dạng JSON,
trạng thái, thông tin gửi/xác nhận), `request_events` (nhật ký từng bước), `counters` (bộ đếm số đề nghị).
Khi khởi động, bảng và cột mới được tự thêm; dữ liệu của bản cũ được giữ nguyên
(phiếu "Xong" cũ chuyển thành "Chờ xem xét", phiếu "Lỗi" cũ thành "Cần người xử lý").

File hóa đơn gốc và file Excel nằm trong `data/files/<mã phiếu>/`.
**Sao lưu = chép cả thư mục `data/`** (nên tắt server trước khi chép).

### Dùng MySQL / MariaDB

1. Tạo CSDL với bảng mã **utf8mb4** (bắt buộc, để lưu đúng tiếng Việt và tiếng Nhật):
   ```sql
   CREATE DATABASE dntt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'dntt'@'localhost' IDENTIFIED BY 'matkhau';
   GRANT ALL PRIVILEGES ON dntt.* TO 'dntt'@'localhost';
   ```
   (hoặc `docker compose --profile mysql up -d`)
2. `pip install pymysql`
3. Chuyển dữ liệu đang có trong SQLite sang (bỏ qua nếu bắt đầu mới):
   ```powershell
   python -m app.copy_db --to "mysql+pymysql://dntt:matkhau@localhost:3306/dntt"
   ```
4. Thêm vào `.env`: `DATABASE_URL=mysql+pymysql://dntt:matkhau@localhost:3306/dntt`, khởi động lại server.

Bảng được tự tạo khi khởi động. File hóa đơn và Excel vẫn nằm trong `data/files/` (CSDL chỉ lưu đường dẫn),
nên sao lưu cần cả CSDL MySQL (`mysqldump`) **và** thư mục `data/`.

### Dùng PostgreSQL

`docker compose --profile postgres up -d`, `pip install "psycopg[binary]"`, rồi
`DATABASE_URL=postgresql+psycopg://dntt:dntt_matkhau@localhost:5432/dntt`.
Chuyển dữ liệu cũ bằng `python -m app.copy_db --to "<DATABASE_URL>"` như trên.

## Số đề nghị tự động

Ô `{{so_de_nghi}}` (ô D6 trong mẫu mặc định) được sinh tự động khi tạo phiếu, dạng
`01-2809/ĐN-2026`: số thứ tự phiếu, ngày + tháng tạo phiếu, năm tạo phiếu.
Số thứ tự đếm lại từ 01 mỗi ngày. Bộ đếm lưu trong bảng `counters` của CSDL.

Tùy chỉnh trong `.env`:

```
SO_DE_NGHI_FORMAT={stt:02d}-{dd}{mm}/ĐN-{yyyy}
SO_DE_NGHI_RESET=day        # day | month | year | never
```

Khi bấm "Tạo lại file Excel" hoặc "Thử lại", phiếu giữ nguyên số đã cấp.

## Tự làm mẫu Excel

Mở một file `.xlsx` bất kỳ (hoặc tải template mặc định về) và gõ placeholder vào ô:

- **Trường hóa đơn:** `{{so_hoa_don}}`, `{{ben_ban.ten_cong_ty}}`, `{{ngay.dd}}`, `{{tong_tien_thanh_toan}}`...
  Ô chỉ chứa đúng một placeholder giữ nguyên kiểu số (định dạng số của ô vẫn áp dụng).
  Ô có chữ đi kèm (`Ngày {{ngay.dd}} tháng {{ngay.mm}}`) được thay thành chuỗi.
- **Dòng hàng mục:** đặt các `{{item.stt}}`, `{{item.ten_hang_hoa_dich_vu}}`, `{{item.so_luong}}`,
  `{{item.don_gia}}`, `{{item.thanh_tien}}`... trên **một dòng duy nhất**. Dòng này được nhân bản
  theo số hàng mục; mọi thứ bên dưới (công thức, ô gộp, chiều cao dòng, ảnh, vùng in) tự dịch xuống.
  Công thức như `=SUM(K16:K18)` tự giãn giống khi chèn dòng trong Excel.
- **Thành tiền và các ô tổng:** ghi **số tĩnh** đúng như trên hóa đơn (dòng thiếu thành tiền nhưng có số lượng
  và đơn giá thì ghi SL × ĐG). Mẫu mặc định cố ý không dùng công thức `=SL*ĐG` / `=SUM(...)` cho số tiền, vì
  công thức không hiện kết quả trong khung xem trước của Outlook/Gmail, Protected View và trên điện thoại —
  quản lý sẽ thấy ô trống. Dùng `{{tong_cong}}`, `{{so_tien_bo_sung}}` cho các ô tổng. Mẫu tự làm vẫn có thể
  đặt công thức riêng; công thức đó được giữ và tự giãn theo số dòng.
- **Thông tin không có trên hóa đơn:** `{{extra.ten_bat_ky}}`, lấy từ trường `extra` khi gọi API.
- **Sheet tên bắt đầu bằng `_`** (vd `_Huong dan`) bị xóa khỏi file kết quả — dùng để ghi chú cho chính mình.

Danh sách đầy đủ xem ở `GET /placeholders` hoặc sheet `_Huong dan` trong template mặc định.
Nếu không gửi template, hệ thống dùng `app/templates/default.xlsx`. Muốn đổi mẫu mặc định,
chỉ cần thay file này.

Giới hạn: định dạng có điều kiện (conditional formatting) bên dưới dòng hàng mục không tự dịch
theo; chỉ hỗ trợ `.xlsx` (không hỗ trợ `.xls` cũ và `.xlsm`).

## Bảo mật

- Mật khẩu băm bằng scrypt; sai 5 lần liên tiếp khóa đăng nhập 5 phút.
- Mọi form có mã CSRF; cookie phiên `SameSite=Lax`, hết hạn sau `SESSION_HOURS` giờ.
- `SECRET_KEY` tự sinh và lưu ở `data/secret_key` nếu không đặt trong `.env`. Đổi khóa này sẽ đăng xuất mọi người.
- Đưa ra Internet: đặt sau HTTPS (vd Caddy, nginx) và bật `COOKIE_SECURE=true`.

## Lưu ý

- Tắt server khi AI đang đọc: lúc khởi động lại, phiếu tự quay về hàng đợi và được đọc tiếp.
- Chạy nhiều tiến trình (`--workers N`) vẫn an toàn: mỗi hóa đơn chỉ được một vòng lặp nhận.
- Luôn đối chiếu với hóa đơn gốc và đọc các cảnh báo trên trang phiếu trước khi nộp.

## Chạy test

```powershell
pytest -q
# chạy test trên MySQL (CSDL test sẽ bị xóa sạch, KHÔNG dùng CSDL thật):
$env:TEST_DATABASE_URL = "mysql+pymysql://dntt:matkhau@localhost/dntt_test"; pytest -q
```

- Bộ test **không đọc file `.env`** (đặt `DOTENV_DISABLED=1`): luôn dùng CSDL SQLite tạm, không gửi mail thật,
  không gọi Gemini, không tạo tài khoản từ `ADMIN_*`. Nếu vì lý do nào đó bộ test trỏ sang CSDL khác CSDL
  tạm hoặc có `SMTP_HOST`, nó dừng ngay trước khi chạy.
- **Thư mục dự án có ký tự `[` hoặc `]`** (vd `D:\[M]_WORK\...`): pytest báo
  `path cannot contain [] parametrization` và không chạy test nào. Đây là giới hạn của pytest. Cách xử lý:
  đổi tên thư mục, hoặc tạo ổ ảo trỏ vào thư mục rồi chạy từ đó:
  ```powershell
  subst X: "D:\[M]_WORK\De Tai AI"
  cd X:\invoice-to-excel; pytest -q
  subst X: /D        # gỡ ổ ảo khi xong
  ```
- Biến môi trường đặt sẵn trên máy (hoặc trong Docker/dịch vụ Windows) được **ưu tiên hơn** giá trị trong `.env`.
