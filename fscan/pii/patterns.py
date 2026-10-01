"""Rule-based detection of sensitive data in text.

Each rule = regex + optional validator + optional context keywords.
  high    checksum-valid, or a format that is unique on its own
  medium  format matches and a context keyword is near (or a weaker format)
Rules are listed strongest first; when two matches overlap, the earlier rule wins.
Raw values live only in memory; callers store `masked`.
"""
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import validators as v
from .mask import mask

_WINDOW_BEFORE, _WINDOW_AFTER = 60, 30


@dataclass(frozen=True)
class Hit:
    type: str
    value: str
    masked: str
    confidence: str
    start: int
    end: int


@dataclass
class Rule:
    type: str
    regex: re.Pattern
    confidence: str = "high"
    validate: Optional[Callable[[str], bool]] = None
    context: Optional[re.Pattern] = None       # required keyword near the match
    boost: Optional[re.Pattern] = None         # optional keyword that raises medium → high
    group: str = "v"


def _r(p, flags=0):
    return re.compile(p, flags)


def _kw(*words):
    return re.compile(r"(?<![a-z])(?:" + "|".join(words) + r")(?![a-z])", re.I)


_ACCOUNT_KW = _kw(r"a/c", "acct", "account", "acc no", "khata")
_CARD_KW = _kw("card", "visa", "mastercard", "master card", "maestro", "rupay", "credit", "debit", "cc", "exp", "cvv")
_AADHAAR_SPACED =re.compile(r"^\d{4}[ -]\d{4}[ -]\d{4}$")
_PLACEHOLDER = re.compile(r"^(?:\*+|x+|\.+|-+|_+|[$%{<\[].*|.*[()].*|null|none|true|false|"
                          r"password|changeme|yourpassword|<.*>)$", re.I)


def _real_password(val: str) -> bool:
    return not _PLACEHOLDER.match(val) and bool(re.search(r"[\d\W_]", val))


RULES: List[Rule] = [
    # --- secrets (unique formats) --------------------------------------------
    Rule("PRIVATE_KEY", _r(r"(?P<v>-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----)")),
    Rule("AZURE_STORAGE_KEY", _r(r"(?P<v>AccountKey=[A-Za-z0-9+/]{80,}={0,2})")),
    Rule("DB_CONNECTION", _r(r"(?P<v>(?:Server|Data Source|Host|Address)\s*=[^;\n]{1,200};[^\n]{0,300}?(?:Password|Pwd)\s*=\s*[^;\s\"']+)", re.I)),
    Rule("DB_CONNECTION", _r(r"(?P<v>\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|mssql|sqlserver|oracle)://[^\s:/@]+:[^\s@/]+@[^\s/]+)", re.I)),
    Rule("AWS_ACCESS_KEY", _r(r"\b(?P<v>(?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    Rule("AWS_SECRET_KEY", _r(r"aws.{0,20}secret.{0,20}[=:]\s*[\"']?(?P<v>[A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])", re.I)),
    Rule("GITHUB_TOKEN", _r(r"\b(?P<v>gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82})\b")),
    Rule("SLACK_TOKEN", _r(r"\b(?P<v>xox[abprs]-[A-Za-z0-9-]{10,})\b")),
    Rule("GOOGLE_API_KEY", _r(r"\b(?P<v>AIza[0-9A-Za-z_\-]{35})(?![0-9A-Za-z_\-])")),
    Rule("JWT", _r(r"\b(?P<v>eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")),
    Rule("PASSWORD", _r(r"(?<![a-z])(?:password|passwd|pwd|pass|secret)\s*[:=]\s*[\"']?(?P<v>[^\s\"'<>,;]{4,64})", re.I),
         confidence="medium", validate=_real_password),
    # --- IDs with checksums -----------------------------------------------------
    Rule("GSTIN", _r(r"\b(?P<v>\d{2}[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b"), validate=v.gstin_valid),
    Rule("CARD", _r(r"(?<![\d-])(?P<v>\d(?:[ -]?\d){12,18})(?![\d-])"), validate=v.card_valid),
    Rule("IBAN", _r(r"\b(?P<v>[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?)\b"), validate=v.iban_valid),
    Rule("AADHAAR", _r(r"(?<![\d-])(?P<v>[2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?![\d-])"), confidence="medium",
         validate=v.aadhaar_valid, boost=_kw("aadhaar", "aadhar", "uid", "uidai")),
    Rule("PAN", _r(r"\b(?P<v>[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z])\b")),
    Rule("US_ITIN", _r(r"(?<![\d-])(?P<v>9\d{2}-\d{2}-\d{4})(?![\d-])"), validate=v.itin_valid),
    Rule("US_SSN", _r(r"(?<![\d-])(?P<v>\d{3}-\d{2}-\d{4})(?![\d-])"), validate=v.ssn_valid),
    Rule("US_SSN", _r(r"(?<![\d-])(?P<v>\d{9})(?![\d-])"), confidence="medium", validate=v.ssn_valid,
         context=_kw("ssn", "social security", "soc sec")),
    # --- banking ----------------------------------------------------------------
    Rule("UPI", _r(r"(?<![\w.@-])(?P<v>[\w.\-]{2,64}@(?:ok(?:hdfcbank|icici|sbi|axis)|ybl|ibl|axl|paytm|upi|apl|"
                   r"pt(?:yes|axis|hdfc|sbi)|axisbank|icici|hdfcbank|sbi|kotak|yesbank|federal|idfcbank|pnb|"
                   r"boi|barodampay|unionbank|aubank|fbl|wa(?:axis|hdfcbank|sbi|icici)))(?![\w.])", re.I)),
    Rule("IFSC", _r(r"\b(?P<v>[A-Z]{4}0[A-Z0-9]{6})\b")),
    Rule("SWIFT", _r(r"\b(?P<v>[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b"), confidence="medium",
         context=_kw("swift", "bic")),
    Rule("BANK_ACCOUNT", _r(r"(?<![\d-])(?P<v>\d{9,18})(?![\d-])"), confidence="medium",
         context=_kw(r"a/c", r"a/c\.", "acct", "account", "account no", "bank account", "acc no", "khata")),
    # --- IDs needing a keyword ---------------------------------------------------
    Rule("IN_PASSPORT", _r(r"\b(?P<v>[A-PR-WY][1-9]\d ?\d{4}[1-9])\b"), confidence="medium", context=_kw("passport")),
    Rule("US_PASSPORT", _r(r"\b(?P<v>[A-Z]?\d{8,9})\b"), confidence="medium", context=_kw("passport")),
    Rule("IN_VOTER_ID", _r(r"\b(?P<v>[A-Z]{3}\d{7})\b"), confidence="medium",
         context=_kw("voter", "epic", "election card", "election commission")),
    Rule("IN_DRIVING_LICENCE", _r(r"\b(?P<v>[A-Z]{2}[ -]?\d{2}[ -]?(?:19|20)\d{2}\d{7})\b"), confidence="medium",
         context=_kw("licence", "license", "dl", "dl no", "driving")),
    Rule("US_EIN", _r(r"(?<![\d-])(?P<v>\d{2}-\d{7})(?![\d-])"), confidence="medium",
         context=_kw("ein", "employer identification", "fein", "tax id", "tin")),
    # --- contact / personal ------------------------------------------------------
    Rule("EMAIL", _r(r"(?<![\w.+-])(?P<v>[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24})\b")),
    Rule("PHONE_IN", _r(r"(?<![\d+])(?P<v>(?:\+91[ -]?|0091[ -]?)[6-9]\d{4}[ -]?\d{5})(?!\d)")),
    Rule("PHONE_IN", _r(r"(?<![\d+])(?P<v>0?[6-9]\d{4}[ -]?\d{5})(?!\d)"), confidence="medium",
         context=_kw("phone", "mobile", "mob", "mob no", "tel", "contact", "whatsapp", "cell", "ph")),
    Rule("PHONE_US", _r(r"(?<![\d+])(?P<v>(?:\+1[ .-]?)?\(?[2-9]\d{2}\)?[ .-][2-9]\d{2}[ .-]\d{4})(?!\d)"), confidence="medium"),
    Rule("DOB", _r(r"(?:\bdob\b|\bd\.o\.b\.?|date\s+of\s+birth|birth\s*date|born\s+on)\W{0,5}"
                   r"(?P<v>\d{1,2}[/.\-]\d{1,2}[/.\-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2}|"
                   r"\d{1,2}[ -](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,-]+(?:19|20)\d{2})",
                   re.I), confidence="medium"),
]

CREDENTIAL_TYPES = {"PRIVATE_KEY", "AZURE_STORAGE_KEY", "DB_CONNECTION", "AWS_ACCESS_KEY",
                    "AWS_SECRET_KEY", "GITHUB_TOKEN", "SLACK_TOKEN", "GOOGLE_API_KEY", "JWT", "PASSWORD"}


def _near(rx: re.Pattern, text: str, start: int, end: int) -> bool:
    return bool(rx.search(text, max(0, start - _WINDOW_BEFORE), min(len(text), end + _WINDOW_AFTER)))


def _confidence(rule: Rule, val: str, text: str, start: int, end: int) -> Optional[str]:
    """Confidence for an accepted match, or None to reject it."""
    if rule.validate and not rule.validate(val):
        return None
    if rule.context and not _near(rule.context, text, start, end):
        return None
    if (rule.type == "CARD" and v.card_brand(val) in v.WEAK_CARD_BRANDS
            and _near(_ACCOUNT_KW, text, start, end) and not _near(_CARD_KW, text, start, end)):
        return None  # e.g. a 14-digit HDFC account number that happens to pass Luhn
    if rule.type == "AADHAAR" and (_AADHAAR_SPACED.match(val) or _near(rule.boost, text, start, end)):
        return "high"
    return rule.confidence


def scan_text(text: str) -> List[Hit]:
    """All sensitive values in `text`, strongest rule first on overlaps, in text order."""
    accepted: List[Hit] = []
    taken: List[tuple] = []
    for rule in RULES:
        for m in rule.regex.finditer(text):
            start, end = m.span(rule.group)
            if any(start < e and s < end for s, e in taken):
                continue
            val = m.group(rule.group)
            conf = _confidence(rule, val, text, start, end)
            if conf:
                accepted.append(Hit(rule.type, val, mask(rule.type, val), conf, start, end))
                taken.append((start, end))
    accepted.sort(key=lambda h: h.start)
    return accepted
