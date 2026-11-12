import pytest

from app.database.models import Source
from app.ingestion.base import IngestionError
from app.ingestion.csv_ingestion import CSVIngestionAdapter
from app.ingestion.normalizer import normalize_source, normalize_timestamp

adapter = CSVIngestionAdapter(max_bytes=1024 * 1024)


def test_rejects_non_csv_extension():
    with pytest.raises(IngestionError) as e:
        adapter.parse(b"complaint_text\nhello there", "data.xlsx")
    assert e.value.status_code == 400


def test_rejects_empty_and_oversized_files():
    with pytest.raises(IngestionError):
        adapter.parse(b"", "a.csv")
    small = CSVIngestionAdapter(max_bytes=10)
    with pytest.raises(IngestionError) as e:
        small.parse(b"complaint_text\n" + b"x" * 100, "a.csv")
    assert e.value.status_code == 413


def test_missing_required_column():
    with pytest.raises(IngestionError) as e:
        adapter.parse(b"text,customer_id\nhello,C1\n", "a.csv")
    assert e.value.status_code == 422
    assert "complaint_text" in e.value.message


def test_header_only_file_rejected():
    with pytest.raises(IngestionError):
        adapter.parse(b"complaint_text,customer_id\n", "a.csv")


def test_row_level_errors_keep_valid_rows():
    csv = (
        "complaint_text,customer_id,source,timestamp,country\n"
        "Payment failed for my order,C1,email,2026-10-01 10:00,India\n"
        ",C2,chat,2026-10-01,India\n"
        "Delivery is late again,C3,fax,2026-10-01,\n"
        "Refund still pending,C4,chat,not-a-date,\n"
        "App crashes on launch,,,,\n"
    ).encode()
    r = adapter.parse(csv, "a.csv")
    assert r.total_rows == 5
    assert len(r.complaints) == 2
    assert {e.row_number for e in r.errors} == {2, 3, 4}
    fields = {e.row_number: e.field for e in r.errors}
    assert fields == {2: "complaint_text", 3: "source", 4: "timestamp"}
    anon = [c for c in r.complaints if c.customer_id is None][0]
    assert anon.source == Source.CSV  # default when missing
    assert anon.timestamp_inferred is True


def test_original_text_preserved_exactly_and_uuid_generated():
    text = "  Weird   spacing <b>and</b> HTML!!  "
    r = adapter.parse(f'complaint_text\n"{text}"\n'.encode(), "a.csv")
    assert r.complaints[0].original_text == text
    assert len(r.complaints[0].complaint_id) == 36


def test_customer_profiles_merged_without_losing_values():
    csv = (
        "complaint_text,customer_id,country,age_group\n"
        "first complaint text,C1,India,18-24\n"
        "second complaint text,C1,,\n"
    ).encode()
    r = adapter.parse(csv, "a.csv")
    assert len(r.customers) == 1
    assert r.customers[0].country == "India" and r.customers[0].age_group == "18-24"


def test_numeric_customer_ids_normalized():
    r = adapter.parse(b"complaint_text,customer_id\nhello world,101.0\n", "a.csv")
    assert r.complaints[0].customer_id == "101"


@pytest.mark.parametrize("raw,expected", [("e-mail", Source.EMAIL), ("Live Chat", Source.CHAT), ("phone", Source.VOICE),
                                          ("twitter", Source.SOCIAL_MEDIA), ("OTHER", Source.OTHER), (None, Source.CSV)])
def test_source_aliases(raw, expected):
    assert normalize_source(raw) == expected


def test_timestamp_normalization_to_utc():
    ts = normalize_timestamp("2026-10-01T10:00:00+05:30")
    assert ts.utcoffset().total_seconds() == 0
    assert ts.hour == 4 and ts.minute == 30
    assert normalize_timestamp("") is None
    with pytest.raises(ValueError):
        normalize_timestamp("1890-01-01")
