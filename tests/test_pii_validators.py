from fscan.pii import validators as v


def test_verhoeff_wikipedia_example():
    assert v.verhoeff_valid("2363")
    assert not v.verhoeff_valid("2364")
    assert v.verhoeff_check_digit("236") == "3"


def test_aadhaar():
    body = "49918765432"
    good = body + v.verhoeff_check_digit(body)
    assert v.aadhaar_valid(good)
    assert v.aadhaar_valid(good[:4] + " " + good[4:8] + " " + good[8:])
    bad = body + str((int(v.verhoeff_check_digit(body)) + 1) % 10)
    assert not v.aadhaar_valid(bad)
    assert not v.aadhaar_valid("1" + good[1:])       # can't start with 0/1
    assert not v.aadhaar_valid("999999999999")       # repeated digits


def test_luhn_and_card_prefixes():
    assert v.card_valid("4111 1111 1111 1111")       # Visa test number
    assert v.card_valid("5555-5555-5555-4444")       # Mastercard
    assert v.card_valid("378282246310005")           # Amex
    assert v.card_valid("6011111111111117")          # Discover
    assert not v.card_valid("4111111111111112")      # Luhn fails
    assert not v.card_valid("9111111111111110")      # no known card prefix


def test_iban():
    assert v.iban_valid("GB82 WEST 1234 5698 7654 32")
    assert v.iban_valid("DE89370400440532013000")
    assert not v.iban_valid("GB82WEST12345698765433")
    assert not v.iban_valid("GB82WEST1234569876543")  # wrong length for GB


def test_gstin():
    assert v.gstin_valid("27AAPFU0939F1ZV")
    assert not v.gstin_valid("27AAPFU0939F1ZW")
    assert not v.gstin_valid("99AAPFU0939F1ZV")       # no such state code


def test_pan_holder_type():
    assert v.pan_valid("ABCPE1234F")
    assert not v.pan_valid("ABCXE1234F")             # X isn't a holder type


def test_ssn_rules():
    assert v.ssn_valid("536-22-1234")
    assert not v.ssn_valid("123-45-6789")            # famous sample number, blocked
    for bad in ["000-12-3456", "666-12-3456", "912-12-3456", "123-00-4567",
                "123-45-0000", "078-05-1120"]:
        assert not v.ssn_valid(bad), bad


def test_itin():
    assert v.itin_valid("912-70-1234")
    assert v.itin_valid("900-94-1234")
    assert not v.itin_valid("912-93-1234")           # 93 isn't an ITIN group
    assert not v.itin_valid("123-70-1234")
