"""Masking: enough of a value to recognise and review it, never the whole value."""
import re

from .validators import digits


def _keep(s, head, tail, stars=5):
    return s[:head] + "*" * stars + (s[-tail:] if tail else "")


def _email(v):
    local, _, domain = v.partition("@")
    return f"{local[:1]}***@{domain}"


def _upi(v):
    local, _, handle = v.partition("@")
    return f"{local[:2]}***@{handle}"


def _dob(v):
    year = re.search(r"(19|20)\d{2}", v)
    return f"**/**/{year.group(0)}" if year else "**/**/****"


def _phone_in(v):
    d = digits(v)[-10:]
    return f"+91 {d[:2]}*****{d[-3:]}"


def _phone_us(v):
    d = digits(v)[-10:]
    return f"+1 {d[:3]}***{d[-4:]}"


def _account(v):
    d = digits(v)
    return "X" * min(8, len(d) - 4) + d[-4:]


_MASKS = {
    "PAN": lambda v: _keep(v, 4, 1),
    "AADHAAR": lambda v: "XXXX XXXX " + digits(v)[-4:],
    "GSTIN": lambda v: _keep(v, 6, 3),
    "IN_PASSPORT": lambda v: _keep(v.replace(" ", ""), 3, 1, 4),
    "IN_VOTER_ID": lambda v: _keep(v, 3, 3, 4),
    "IN_DRIVING_LICENCE": lambda v: _keep(re.sub(r"[ -]", "", v), 4, 2, 9),
    "IFSC": lambda v: v,          # branch code, not personal
    "SWIFT": lambda v: v,         # bank code, not personal
    "BANK_ACCOUNT": _account,
    "UPI": _upi,
    "CARD": lambda v: "XXXX-XXXX-XXXX-" + digits(v)[-4:],
    "IBAN": lambda v: (lambda s: f"{s[:4]} **** **** {s[-4:]}")(re.sub(r"\s", "", v)),
    "US_SSN": lambda v: "XXX-XX-" + digits(v)[-4:],
    "US_ITIN": lambda v: "XXX-XX-" + digits(v)[-4:],
    "US_EIN": lambda v: "XX-XXX" + digits(v)[-4:],
    "US_PASSPORT": lambda v: "*****" + v[-4:],
    "EMAIL": _email,
    "PHONE_IN": _phone_in,
    "PHONE_US": _phone_us,
    "DOB": _dob,
    "PRIVATE_KEY": lambda v: "[private key]",
    "PASSWORD": lambda v: "[password]",
    "DB_CONNECTION": lambda v: "[connection string]",
}


def mask(data_type: str, value: str) -> str:
    fn = _MASKS.get(data_type)
    if fn:
        return fn(value)
    return value[:4] + "****"   # API keys and tokens: the prefix identifies the kind
