(() => {
  // Bấm cả dòng trong bảng để mở phiếu
  document.querySelectorAll("tr[data-href]").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("a, button, form, input")) return;
      location.href = tr.dataset.href;
    });
  });

  // Xác nhận trước khi xóa
  document.querySelectorAll("form[data-confirm]").forEach((f) => {
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
  });

  // Trang chi tiết: hỏi trạng thái mỗi 3 giây khi đang đọc
  const poll = document.querySelector("[data-poll]");
  if (poll) {
    const tick = async () => {
      try {
        const r = await fetch(poll.dataset.poll, { headers: { Accept: "application/json" } });
        if (r.ok && !["queued", "processing"].includes((await r.json()).status)) return location.reload();
      } catch (_) { /* mạng chập chờn: thử lại lần sau */ }
      setTimeout(tick, 3000);
    };
    setTimeout(tick, 3000);
  }


  // Form tạo đề nghị
  const form = document.querySelector(".upload-form");
  if (!form) return;
  const input = form.querySelector("#files");
  const zone = form.querySelector(".dropzone");
  const list = form.querySelector("#file-list");
  const maxBytes = parseFloat(form.dataset.maxMb) * 1024 * 1024;
  const maxFiles = parseInt(form.dataset.maxFiles, 10);
  const okType = /\.(pdf|jpe?g|png|webp)$/i;
  const size = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.ceil(b / 1024) + " KB");

  // Danh sách file đã chọn: chọn thêm thì cộng dồn, bấm × để bỏ từng file.
  // input.files không sửa trực tiếp được -> dựng lại bằng DataTransfer.
  const canEdit = (() => { try { return !!new DataTransfer().items; } catch (_) { return false; } })();
  const fileKey = (f) => [f.name, f.size, f.lastModified].join("|");
  const dzTitle = zone.querySelector(".dz-title");
  const dzTitleHtml = dzTitle.innerHTML;
  let selected = [...input.files];

  const sync = () => {
    if (!canEdit) return;
    const dt = new DataTransfer();
    selected.forEach((f) => dt.items.add(f));
    input.files = dt.files;
  };

  const render = () => {
    list.innerHTML = "";
    selected.forEach((f, i) => {
      const li = document.createElement("li");
      let problem = "";
      if (!okType.test(f.name)) problem = "không phải PDF hoặc ảnh";
      else if (f.size > maxBytes) problem = "quá dung lượng";
      else if (i >= maxFiles) problem = `vượt quá ${maxFiles} file`;
      if (problem) li.className = "bad";
      const name = document.createElement("span");
      name.className = "file-name";
      name.textContent = f.name;
      const meta = document.createElement("small");
      meta.textContent = problem || size(f.size);
      li.append(name, meta);
      if (canEdit) {
        const x = document.createElement("button");
        x.type = "button";
        x.className = "file-x";
        x.dataset.i = i;
        x.textContent = "×";
        x.title = "Bỏ file này";
        x.setAttribute("aria-label", `Bỏ file ${f.name}`);
        li.append(x);
      }
      list.append(li);
    });
    if (canEdit && selected.length > 1) {
      const li = document.createElement("li");
      li.className = "file-all";
      const n = document.createElement("span");
      n.textContent = `${selected.length} file đã chọn`;
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "btn-link";
      clear.dataset.all = "1";
      clear.textContent = "Bỏ chọn tất cả";
      li.append(n, clear);
      list.append(li);
    }
    dzTitle.innerHTML = selected.length && canEdit
      ? "Kéo thêm hóa đơn vào đây hoặc <u>chọn thêm file</u>" : dzTitleHtml;
  };

  input.addEventListener("change", () => {
    if (!canEdit) { selected = [...input.files]; render(); return; }
    const seen = new Set(selected.map(fileKey));
    [...input.files].forEach((f) => {
      if (!seen.has(fileKey(f))) { selected.push(f); seen.add(fileKey(f)); }
    });
    sync();
    render();
  });

  list.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    if (btn.dataset.all) selected = [];
    else selected.splice(Number(btn.dataset.i), 1);
    sync();
    render();
    // giữ con trỏ bàn phím ở chỗ hợp lý sau khi xóa
    const next = list.querySelector(`.file-x[data-i="${Math.min(Number(btn.dataset.i) || 0, selected.length - 1)}"]`);
    (next || input).focus();
  });

  // Quay lại trang bằng nút Back: trình duyệt có thể còn giữ file cũ trong ô chọn
  window.addEventListener("pageshow", () => { selected = [...input.files]; render(); });
  render();

  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.add("is-over")));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-over")));

  // Nhớ tên người ký trên máy này
  form.querySelectorAll("[data-remember]").forEach((el) => {
    const key = "dntt:" + el.name;
    try { if (!el.value) el.value = localStorage.getItem(key) || ""; } catch (_) {}
    el.addEventListener("change", () => { try { localStorage.setItem(key, el.value); } catch (_) {} });
  });

  // Nhớ logo đã chọn trên máy này
  form.querySelectorAll("[data-remember-radio]").forEach((group) => {
    const key = "dntt:" + group.dataset.rememberRadio;
    try {
      const saved = localStorage.getItem(key);
      const radio = saved && group.querySelector(`input[type=radio][value="${CSS.escape(saved)}"]`);
      if (radio) radio.checked = true;
    } catch (_) {}
    group.addEventListener("change", (e) => {
      if (e.target.matches("input[type=radio]")) { try { localStorage.setItem(key, e.target.value); } catch (_) {} }
    });
  });

  form.addEventListener("submit", (e) => {
    if (!selected.length) {
      e.preventDefault();
      alert("Chọn ít nhất một hóa đơn.");
      return;
    }
    if (list.querySelector(".bad")) {
      e.preventDefault();
      alert("Có file không hợp lệ (đánh dấu đỏ). Bấm × để bỏ file đó rồi gửi.");
      return;
    }
    const btn = form.querySelector("#submit-btn");
    btn.disabled = true;
    btn.textContent = "Đang tải lên…";
  });
})();
// ---------- Trang xem và sửa trực tiếp ----------
// Sửa được: Số lượng, Đơn giá, Thành tiền, Thuế suất. Rời ô (đổi giá trị) -> tính lại mọi ô tổng.
// Chỉ xem (tự tính): Cộng tiền hàng, Tiền thuế, Tổng thanh toán, Số tiền bằng chữ.
(() => {
  const form = document.getElementById("edit-form");
  if (!form) return;
  const tbody = form.querySelector("#items tbody");
  const tpl = document.getElementById("row-tpl");
  const sumEl = document.getElementById("items-sum");
  const f = (n) => form.querySelector(`[name=${n}]`);

  // "1.333.333" -> 1333333 ; "1,5" -> 1.5 ; "" -> null (giống to_number phía server)
  const num = (s) => {
    s = (s || "").replace(/[^\d,.\-]/g, "");
    if (!s) return null;
    if (s.includes(".") && s.includes(",")) {
      const dec = s.lastIndexOf(".") > s.lastIndexOf(",") ? "." : ",";
      s = s.split(dec === "." ? "," : ".").join("").replace(dec, ".");
    } else if ((s.match(/\./g) || []).length > 1 || (s.match(/,/g) || []).length > 1) {
      s = s.replace(/[.,]/g, "");
    } else if (/[.,]/.test(s)) {
      const [h, t] = s.split(/[.,]/);
      // "2.300" = nghìn; "0,125" / ".5" (phần nguyên 0 hoặc trống) luôn là thập phân
      s = t.length === 3 && !["", "0", "-", "-0"].includes(h) ? h + t : h + "." + t;
    }
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : null;
  };
  // Ô Số lượng: dấu phẩy là thập phân, dấu chấm là nghìn ("0,125" -> 0.125, "1.500" -> 1500), giống to_number_vn
  const numVN = (s) => {
    s = (s || "").replace(/[^\d,.\-]/g, "");
    if ((s.match(/,/g) || []).length === 1) {
      const n = parseFloat(s.replace(/\./g, "").replace(",", "."));
      return Number.isFinite(n) ? n : null;
    }
    return num(s);
  };
  const fmt = (n) => (n == null ? "" : Math.round(n).toLocaleString("de-DE"));
  // "10%" -> 0.1 ; "8" -> 0.08 ; "KCT"/"0%"/trống -> 0 ; không hiểu -> null (giống parse_tax_rate)
  // Nhiều mức ("5%, 10%") -> mảng các mức (giống parse_tax_rates)
  const rate = (t) => {
    t = (t || "").trim().toUpperCase();
    if (!t || ["KCT", "KKKNT", "-", "0", "0%"].includes(t)) return 0;
    const m = t.match(/^(\d+(?:[.,]\d+)?)\s*%?$/);
    if (m) return parseFloat(m[1].replace(",", ".")) / 100;
    const pct = [...t.matchAll(/(\d+(?:[.,]\d+)?)\s*%/g)].map((x) => parseFloat(x[1].replace(",", ".")) / 100);
    const kct = (t.match(/\bKCT\b|\bKKKNT\b/g) || []).map(() => 0);
    const rest = t.replace(/\d+(?:[.,]\d+)?\s*%|\bKCT\b|\bKKKNT\b/g, "").replace(/(^|\s)V[ÀA](?=\s|$)/g, " ").replace(/[\s,;\/+&]/g, "");
    const all = [...new Set([...pct, ...kct])];
    if (!all.length || rest) return null;
    return all.length === 1 ? all[0] : all;
  };
  // Ô Tiền thuế: chỉ xem khi 1 mức thuế (tự tính), nhập tay khi nhiều mức thuế
  const taxInput = f("tien_thue");
  const setTaxManual = (manual) => {
    taxInput.readOnly = !manual;
    taxInput.classList.toggle("calc", !manual);
    taxInput.tabIndex = manual ? 0 : -1;
    taxInput.required = manual;
  };

  // Đọc số thành chữ - cùng quy tắc với app/numwords.py
  const DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"];
  const UNITS = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"];
  const readGroup = (n, full) => {
    const tram = Math.floor(n / 100), chuc = Math.floor(n / 10) % 10, dv = n % 10, w = [];
    if (full || tram) w.push(DIGITS[tram], "trăm");
    if (chuc === 0) { if (dv && w.length) w.push("lẻ"); }
    else if (chuc === 1) w.push("mười");
    else w.push(DIGITS[chuc], "mươi");
    if (dv) {
      if (dv === 1 && chuc > 1) w.push("mốt");
      else if (dv === 5 && chuc > 0) w.push("lăm");
      else if (dv === 4 && chuc > 1) w.push("tư");
      else w.push(DIGITS[dv]);
    }
    return w;
  };
  const words = (x) => {
    let n = Math.round(x);
    if (!Number.isFinite(n) || n < 0) return "";
    if (n === 0) return "Không đồng";
    const g = [];
    while (n) { g.push(n % 1000); n = Math.floor(n / 1000); }
    let w = [];
    for (let i = g.length - 1; i >= 0; i--) {
      if (!g[i]) continue;
      w = w.concat(readGroup(g[i], w.length > 0));
      if (UNITS[i]) w.push(UNITS[i]);
    }
    const s = w.join(" ");
    return s[0].toUpperCase() + s.slice(1) + " đồng";
  };

  const rows = () => [...tbody.querySelectorAll("tr")];
  const setCalc = (name, value) => {
    const el = f(name);
    if (el.value === value) return;
    el.value = value;
    el.classList.remove("just-calc");
    void el.offsetWidth;            // chạy lại hiệu ứng nháy màu
    el.classList.add("just-calc");
  };

  const recalcTotals = () => {
    let pre = 0, missing = 0;
    rows().forEach((tr) => {
      const v = num(tr.querySelector("[name=item_tt]").value);
      if (v == null) { if (tr.querySelector("[name=item_ten]").value.trim()) missing++; } else pre += v;
    });
    sumEl.textContent = `Cộng các dòng: ${fmt(pre)} đ` + (missing ? `, ${missing} dòng chưa có thành tiền` : "");
    const r = rate(f("thue_suat").value);
    f("thue_suat").setCustomValidity(r == null ? "Nhập dạng 10%, 8%, KCT hoặc 5%, 10%" : "");
    const multi = Array.isArray(r);
    setTaxManual(multi);
    const tax = multi ? (num(taxInput.value) || 0) : Math.round(pre * (r || 0));
    setCalc("tong_tien_truoc_thue", fmt(pre));
    if (!multi) setCalc("tien_thue", fmt(tax));
    setCalc("tong_tien_thanh_toan", fmt(pre + tax));
    setCalc("so_tien_bang_chu", words(pre + tax));
  };

  // Rời ô sau khi đổi giá trị ("change" chỉ chạy khi giá trị thật sự thay đổi)
  form.addEventListener("change", (e) => {
    const name = e.target.name;
    if (name === "item_sl" || name === "item_dg") {
      const tr = e.target.closest("tr");
      const q = numVN(tr.querySelector("[name=item_sl]").value), p = num(tr.querySelector("[name=item_dg]").value);
      if (q != null && p != null) tr.querySelector("[name=item_tt]").value = fmt(q * p);
    }
    if (["item_sl", "item_dg", "item_tt", "thue_suat", "tien_thue"].includes(name)) {
      if (["item_sl", "item_dg", "item_tt", "tien_thue"].includes(name) && e.target.value.trim()) {
        const n = name === "item_sl" ? numVN(e.target.value) : num(e.target.value);
        if (n != null) e.target.value = name === "item_sl" && !Number.isInteger(n)
          ? String(n).replace(".", ",") : fmt(n);   // định dạng lại 1300000 -> 1.300.000
      }
      recalcTotals();
    }
  });

  // Enter trong ô nhập: không gửi form, chỉ rời ô (để tính lại)
  form.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); e.target.blur(); }
  });

  tbody.addEventListener("click", (e) => {
    if (!e.target.classList.contains("row-del")) return;
    if (rows().length === 1) rows()[0].querySelectorAll("input").forEach((i) => (i.value = ""));
    else e.target.closest("tr").remove();
    recalcTotals();
  });
  document.getElementById("row-add").addEventListener("click", () => {
    tbody.append(tpl.content.cloneNode(true));
    rows().at(-1).querySelector("input").focus();
  });
  recalcTotals();

  // Nhắc khi rời trang mà chưa lưu
  let dirty = false;
  form.addEventListener("input", () => (dirty = true));
  form.addEventListener("submit", () => (dirty = false));
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
})();

// ---------- Màn hình chờ khi AI đang đọc: khóa trang, hỏi tiến độ mỗi 3 giây, xong thì mở lại ----------
(() => {
  const ov = document.getElementById("busy-overlay");
  if (!ov) return;
  // Chặn chuột + bàn phím (Tab) với mọi thứ phía sau màn hình chờ
  const main = ov.parentElement;
  [document.querySelector(".topbar"), ...main.children].forEach((el) => { if (el && el !== ov) el.inert = true; });
  document.body.style.overflow = "hidden";
  ov.focus();

  const text = document.getElementById("busy-text");
  const note = document.getElementById("busy-note");
  const tick = async () => {
    try {
      const r = await fetch(ov.dataset.pollUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (r.redirected) return (location.href = r.url);  // hết phiên đăng nhập
      const d = await r.json();
      if (d.busy === 0) return location.reload();      // xong hết -> mở khóa, hiện kết quả
      const parts = [];
      if (d.processing) parts.push(`AI đang đọc ${d.processing} phiếu`);
      if (d.queued) parts.push(`${d.queued} phiếu đang chờ`);
      text.textContent = parts.join(", ") + ". Vui lòng chờ, màn hình sẽ tự mở khi xong.";
      note.hidden = !d.note;
      note.textContent = d.note || "";
    } catch (_) {
      text.textContent = "Mất kết nối tới máy chủ, đang thử lại…";
    }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 3000);
})();
// ---------- Danh sách: bảng cao đúng N dòng (mặc định 10), nhiều hơn thì cuộn trong bảng ----------
(() => {
  const wrap = document.querySelector(".list-scroll");
  if (!wrap) return;
  const visible = parseInt(wrap.dataset.visibleRows || "10", 10);
  const fit = () => {
    const rows = wrap.querySelectorAll("tbody tr");
    if (rows.length <= visible) { wrap.classList.remove("is-scroll"); wrap.style.maxHeight = ""; return; }
    wrap.classList.add("is-scroll");
    const top = wrap.getBoundingClientRect().top - wrap.scrollTop;
    const bottom = rows[visible - 1].getBoundingClientRect().bottom;
    wrap.style.maxHeight = Math.ceil(bottom - top + 2) + "px";   // +2: viền khung
  };
  fit();
  window.addEventListener("resize", fit);
  document.fonts && document.fonts.ready.then(fit);   // font tải xong, chiều cao dòng có thể đổi
})();
