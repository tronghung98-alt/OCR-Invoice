"""Đọc số tiền thành chữ tiếng Việt: 3699000 -> 'Ba triệu sáu trăm chín mươi chín nghìn đồng'."""

DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
UNITS = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]


def _read_group(n: int, full: bool) -> list[str]:
    tram, chuc, dv = n // 100, (n // 10) % 10, n % 10
    words = []
    if full or tram:
        words += [DIGITS[tram], "trăm"]
    if chuc == 0:
        if dv and words:
            words.append("lẻ")
    elif chuc == 1:
        words.append("mười")
    else:
        words += [DIGITS[chuc], "mươi"]
    if dv:
        if dv == 1 and chuc > 1:
            words.append("mốt")
        elif dv == 5 and chuc > 0:
            words.append("lăm")
        elif dv == 4 and chuc > 1:
            words.append("tư")
        else:
            words.append(DIGITS[dv])
    return words


def so_thanh_chu(amount, currency: str = "đồng") -> str:
    n = int(round(float(amount)))
    if n < 0:
        raise ValueError("Số tiền âm")
    if n >= 1000 ** len(UNITS):
        raise ValueError("Số quá lớn")
    if n == 0:
        return f"Không {currency}"
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        words += _read_group(g, full=bool(words))
        if UNITS[i]:
            words.append(UNITS[i])
    text = " ".join(words)
    return f"{text[0].upper()}{text[1:]} {currency}"
