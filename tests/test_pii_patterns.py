import pytest

from fscan.pii.patterns import CREDENTIAL_TYPES, scan_text
from fscan.pii import validators as v

AADHAAR = "4991 8765 432" + v.verhoeff_check_digit("49918765432")


def found(text):
    return {(h.type, h.confidence) for h in scan_text(text)}


def types(text):
    return {h.type for h in scan_text(text)}


@pytest.mark.parametrize("text,expected", [
    ("PAN: ABCPE1234F", "PAN"),
    (f"Aadhaar {AADHAAR}", "AADHAAR"),
    ("GSTIN 27AAPFU0939F1ZV", "GSTIN"),
    ("Passport No: J8369854", "IN_PASSPORT"),
    ("Voter ID: ABC1234567", "IN_VOTER_ID"),
    ("Driving licence MH12 20110012345", "IN_DRIVING_LICENCE"),
    ("IFSC HDFC0001234", "IFSC"),
    ("A/c No. 50100123456789", "BANK_ACCOUNT"),
    ("pay to ramesh.k@okhdfcbank today", "UPI"),
    ("card 4111 1111 1111 1111 exp 12/29", "CARD"),
    ("IBAN GB82 WEST 1234 5698 7654 32", "IBAN"),
    ("SWIFT code HDFCINBBXXX", "SWIFT"),
    ("SSN 536-22-1234", "US_SSN"),
    ("ITIN 912-70-1234", "US_ITIN"),
    ("Employer Identification Number 12-3456789", "US_EIN"),
    ("US passport number 123456789", "US_PASSPORT"),
    ("mail ramesh.kumar@example.com", "EMAIL"),
    ("mobile +91 98765 43210", "PHONE_IN"),
    ("call (415) 555-2671", "PHONE_US"),
    ("Date of Birth: 14/03/1985", "DOB"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE", "PRIVATE_KEY"),
    ("key=AKIAIOSFODNN7EXAMPLE", "AWS_ACCESS_KEY"),
    ("token ghp_" + "a" * 36, "GITHUB_TOKEN"),
    ("xoxb-123456789012-abcdefghij", "SLACK_TOKEN"),
    ("AIza" + "B" * 35, "GOOGLE_API_KEY"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N", "JWT"),
    ("password = Welcome@123", "PASSWORD"),
    ("Server=db1;Database=hr;User Id=sa;Password=S3cret!;", "DB_CONNECTION"),
    ("postgres://app:hunter22@db.internal:5432/prod", "DB_CONNECTION"),
    ("DefaultEndpointsProtocol=https;AccountName=x;AccountKey=" + "A" * 86 + "==;", "AZURE_STORAGE_KEY"),
])
def test_each_type_is_found(text, expected):
    assert expected in types(text), (text, types(text))


@pytest.mark.parametrize("text,absent", [
    ("ABCXE1234F", "PAN"),                        # invalid holder type
    ("order 499187654320 shipped", "AADHAAR"),    # Verhoeff fails
    ("ref J8369854", "IN_PASSPORT"),              # no passport keyword
    ("invoice 50100123456789", "BANK_ACCOUNT"),   # no account keyword
    ("4111 1111 1111 1112", "CARD"),              # Luhn fails
    ("GB82 WEST 1234 5698 7654 33", "IBAN"),      # mod-97 fails
    ("id 666-12-3456", "US_SSN"),                 # invalid area
    ("call 9876543210", "PHONE_IN"),              # bare 10 digits, no prefix or keyword
    ("password: ********", "PASSWORD"),           # placeholder
    ("password = get_password()", "PASSWORD"),    # code, not a value
    ("released 14/03/1985", "DOB"),               # date without a birth keyword
])
def test_negative_cases(text, absent):
    assert absent not in types(text), (text, types(text))


def test_aadhaar_confidence_depends_on_format_or_keyword():
    plain = AADHAAR.replace(" ", "")
    assert ("AADHAAR", "high") in found(f"Aadhaar no {plain}")
    assert ("AADHAAR", "high") in found(f"id {AADHAAR}")  # spaced 4-4-4 format
    assert ("AADHAAR", "medium") in found(f"ref {plain}")


def test_gstin_is_not_also_reported_as_pan():
    assert types("27AAPFU0939F1ZV") == {"GSTIN"}


def test_overlapping_matches_keep_the_stronger_type():
    # A valid card number next to "account" must not also be a bank account.
    assert types("account 4111111111111111") == {"CARD"}


def test_masking_hides_the_value():
    text = (f"PAN ABCPE1234F Aadhaar {AADHAAR} card 4111 1111 1111 1111 "
            "SSN 536-22-1234 mail ramesh.kumar@example.com password = Welcome@123 "
            "mobile +91 98765 43210 A/c No. 50100123456789")
    hits = scan_text(text)
    masked = {h.type: h.masked for h in hits}
    assert masked["PAN"] == "ABCP*****F"
    assert masked["AADHAAR"] == "XXXX XXXX " + AADHAAR[-4:]
    assert masked["CARD"] == "XXXX-XXXX-XXXX-1111"
    assert masked["US_SSN"] == "XXX-XX-1234"
    assert masked["EMAIL"] == "r***@example.com"
    assert masked["PASSWORD"] == "[password]"
    for h in hits:
        assert h.value not in h.masked, h


def test_positions_and_credential_flag():
    text = "xx PAN ABCPE1234F yy"
    (h,) = scan_text(text)
    assert text[h.start:h.end] == "ABCPE1234F"
    assert "PASSWORD" in CREDENTIAL_TYPES and "PAN" not in CREDENTIAL_TYPES


def test_account_number_that_passes_luhn_is_not_a_card():
    # 14-digit HDFC-style account: Luhn-valid with a Maestro prefix
    assert types("A/c No. 50100123456789") == {"BANK_ACCOUNT"}
    assert "CARD" in types("Maestro card 50100123456789")
