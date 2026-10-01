"""Checksum and structure checks that turn 'looks like an ID' into 'is a valid ID'."""
import re

_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
      [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
      [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
      [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
      [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
      [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def aadhaar_valid(s: str) -> bool:
    d = digits(s)
    return (len(d) == 12 and d[0] not in "01" and len(set(d)) > 1
            and verhoeff_valid(d))


def luhn_valid(d: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# (brand, prefix regex, allowed lengths). Order matters: first match wins.
_CARD_BRANDS = [
    ("visa", re.compile(r"^4"), {13, 16, 19}),
    ("mastercard", re.compile(r"^(5[1-5]|2(2[2-9]|[3-6]\d|7[01]|720))"), {16}),
    ("amex", re.compile(r"^3[47]"), {15}),
    ("discover", re.compile(r"^(6011|64[4-9]|65)"), set(range(16, 20))),
    ("jcb", re.compile(r"^35(2[89]|[3-8]\d)"), set(range(16, 20))),
    ("diners", re.compile(r"^3(0[0-5]|[68])"), set(range(14, 20))),
    ("rupay", re.compile(r"^(60|8[12]|508)"), {16}),
    ("unionpay", re.compile(r"^62"), set(range(16, 20))),
    ("maestro", re.compile(r"^(5[06-8]|6[37])"), set(range(12, 20))),
]
# Brands whose prefixes collide with ordinary account numbers.
WEAK_CARD_BRANDS = {"maestro"}


def card_brand(s: str):
    d = digits(s)
    if len(set(d)) < 2 or not luhn_valid(d):
        return None
    for brand, rx, lengths in _CARD_BRANDS:
        if rx.match(d):
            return brand if len(d) in lengths else None
    return None


def card_valid(s: str) -> bool:
    return card_brand(s) is not None


_IBAN_LEN = {
    "AD": 24, "AE": 23, "AT": 20, "BE": 16, "BG": 22, "BH": 22, "CH": 21, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HR": 21,
    "HU": 28, "IE": 22, "IL": 23, "IS": 26, "IT": 27, "KW": 30, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PT": 25, "QA": 29,
    "RO": 24, "SA": 24, "SE": 24, "SI": 19, "SK": 24, "TR": 26, "BR": 29, "MU": 30, "EG": 29,
}


def iban_valid(s: str) -> bool:
    iban = re.sub(r"\s", "", s).upper()
    if _IBAN_LEN.get(iban[:2]) != len(iban) or not iban.isalnum():
        return False
    moved = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GST_STATES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}


def gstin_valid(s: str) -> bool:
    s = s.upper()
    if len(s) != 15 or s[:2] not in _GST_STATES or not pan_valid(s[2:12]):
        return False
    total = 0
    for i, ch in enumerate(s[:14]):
        product = _B36.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _B36[(36 - total % 36) % 36] == s[14]


def pan_valid(s: str) -> bool:
    # 4th character = holder type: Person, Company, HUF, Firm, AOP, Trust, BOI,
    # Local authority, Juridical person, Government
    return bool(re.fullmatch(r"[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]", s))


_SSN_BLOCKLIST = {"078051120", "219099999", "123456789"}


def ssn_valid(s: str) -> bool:
    d = digits(s)
    if len(d) != 9 or d in _SSN_BLOCKLIST:
        return False
    area, group, serial = d[:3], d[3:5], d[5:]
    return (area not in ("000", "666") and area[0] != "9"
            and group != "00" and serial != "0000")


def itin_valid(s: str) -> bool:
    d = digits(s)
    if len(d) != 9 or d[0] != "9":
        return False
    g = int(d[3:5])
    return 50 <= g <= 65 or 70 <= g <= 88 or 90 <= g <= 92 or 94 <= g <= 99
