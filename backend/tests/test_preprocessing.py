from app.preprocessing.cleaner import clean_text
from app.preprocessing.pii_detection import extract_reference_ids, mask_pii


def test_masks_pii_but_keeps_reference_ids():
    r = clean_text("Card 4111 1111 1111 1111 charged. Mail me at jo@ex.com or +1 555-123-4567. Order ORD-123456")
    assert "[CARD]" in r.text and "[EMAIL]" in r.text and "[PHONE]" in r.text
    assert "ORD-123456" in r.text
    assert r.reference_ids == ["ORD-123456"]
    assert set(r.pii_found) == {"CARD", "EMAIL", "PHONE"}


def test_strips_markup_and_normalizes_whitespace_and_unicode():
    r = clean_text("<p>The app​   crashes&nbsp;again!!!</p>\n\nSent from my iPhone")
    assert r.text == "The app crashes again!"
    assert clean_text("ｆｕｌｌｗｉｄｔｈ text").text.startswith("fullwidth")


def test_does_not_remove_negations_or_case():
    r = clean_text("I did NOT receive my refund and never got a reply")
    assert "NOT" in r.text and "never" in r.text


def test_empty_detection():
    assert clean_text("   <br/> !!! ").is_empty
    assert clean_text("12345 ...").is_empty
    assert not clean_text("App broken").is_empty


def test_reference_extraction_variants():
    refs = extract_reference_ids("order #ORD-555555, transaction id TX99812 and TXN-12345678")
    assert "ORD-555555" in refs and "TXN-12345678" in refs and "TX99812" in refs


def test_mask_pii_ssn():
    text, found = mask_pii("ssn 123-45-6789")
    assert text == "ssn [SSN]" and found == ["SSN"]


def test_spoken_reference_ids_match_written_form():
    # numeric codes after a keyword get the written prefix, so channels agree on the same order
    assert extract_reference_ids("my order number is 481234") == ["ORD-481234"]
    assert extract_reference_ids("transaction 99887766 failed") == ["TXN-99887766"]
    # a keyword followed by a non-ID word must not swallow the real ID that follows
    assert extract_reference_ids("never ordered on invoice order number 239093") == ["ORD-239093"]
    assert extract_reference_ids("order number pending") == []
