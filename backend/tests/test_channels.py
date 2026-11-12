"""Multi-channel ingestion: email, chat, social media, voice, identity directory and cross-channel dedup."""
from __future__ import annotations

import csv
import json
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion.base import IngestionError, UploadedFile
from app.ingestion.chat_ingestion import ChatIngestionAdapter
from app.ingestion.directory import parse_directory
from app.ingestion.email_ingestion import EmailIngestionAdapter, html_to_text
from app.ingestion.identity import ContactResolver, normalize_contact, pseudonymous_id, resolve_identity
from app.ingestion.social_ingestion import SocialIngestionAdapter
from app.ingestion.transcription import Transcript, Transcriber
from app.ingestion.voice_ingestion import VoiceIngestionAdapter, join_spoken_digits
from app.main import create_app
from app.preprocessing.cleaner import clean_text

CH = Path(__file__).resolve().parents[1] / "data" / "channels"
SALT = "test-salt"


def resolver(directory: dict[tuple[str, str], str] | None = None) -> ContactResolver:
    """Resolver backed by an in-memory {(type, raw_value): customer_id} directory."""
    r = ContactResolver(lambda t, hs: {}, SALT)
    table = {(t, r.hash(t, v)): cid for (t, v), cid in (directory or {}).items()}
    r._lookup = lambda t, hs: {h: table[(t, h)] for h in hs if (t, h) in table}
    return r


class FakeTranscriber(Transcriber):
    name = "fake"

    def __init__(self, texts: dict[str, str]):
        self.texts = texts

    @property
    def available(self) -> bool:
        return True

    def transcribe(self, content: bytes, filename: str) -> Transcript:
        if filename not in self.texts:
            raise ValueError("Invalid data found when processing input")
        return Transcript(self.texts[filename], "en", 12.0)


# ------------------------------------------------------------------ identity
def test_contact_normalization():
    assert normalize_contact("email", "Asha <Asha.X@Example.COM>") == "asha.x@example.com"
    assert normalize_contact("email", "not an email") is None
    assert normalize_contact("phone", "+1 (555) 201-7781") == normalize_contact("phone", "555.201.7781")
    assert normalize_contact("phone", "12") is None
    assert normalize_contact("social_handle", "@Shopper_1") == "shopper_1"
    assert normalize_contact("social_handle", "https://twitter.com/Shopper_1") == "shopper_1"


def test_identity_resolution_order():
    r = resolver({("email", "a@example.com"): "CUST-1"})
    assert resolve_identity(r, "CUST-9", [("email", "a@example.com")]) == ("CUST-9", "explicit")
    assert resolve_identity(r, None, [("email", "A@Example.com")]) == ("CUST-1", "directory")
    cid, link = resolve_identity(r, None, [("email", "b@example.com")])
    assert link == "pseudonymous" and cid.startswith("EMAIL-") and "example" not in cid
    assert resolve_identity(r, None, [("email", "b@example.com")])[0] == cid  # stable
    assert resolve_identity(r, None, [("email", None)]) == (None, "anonymous")


def test_directory_parse_hashes_only_and_reports_errors():
    content = (b"customer_id,email,phone,social_handle\n"
               b"C1,a@example.com;a2@example.com,+1 555 100 2000,@aa\n"
               b"C2,a@example.com,,\n"      # same email as C1 -> conflict
               b",x@example.com,,\n"        # missing customer id
               b"C3,bad-email,,\n")
    out = parse_directory(content, "dir.csv", resolver())
    assert {c.customer_id for c in out.customers} == {"C1", "C2", "C3"}
    assert len(out.contacts) == 4
    assert all("@" not in h for _, h, _ in out.contacts)  # hashes only
    msgs = " ".join(e.message for e in out.errors)
    assert "already assigned" in msgs and "customer_id is empty" in msgs and "invalid email" in msgs
    with pytest.raises(IngestionError):
        parse_directory(b"customer_id,name\nC1,x\n", "dir.csv", resolver())


# ------------------------------------------------------------------ email
def _eml(sender: str, subject: str, body: str, date="Mon, 09 Nov 2026 10:00:00 +0000", **headers) -> bytes:
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = sender, subject, date
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m.as_bytes()


def test_email_adapter_links_sender_and_rejects_auto_replies():
    r = resolver({("email", "asha@example.com"): "CUST-1"})
    files = [
        UploadedFile("a.eml", _eml("Asha <asha@example.com>", "Refund ORD-123456 missing", "Still no refund.")),
        UploadedFile("b.eml", _eml("x@example.org", "Complaint", "The app keeps crashing on checkout.")),
        UploadedFile("c.eml", _eml("asha@example.com", "Out of office", "Away", Auto_Submitted="auto-replied")),
        UploadedFile("d.eml", b"From: asha@example.com\nDate: garbage\nSubject: Hi\n\nhelp me please\n"),
    ]
    res = EmailIngestionAdapter(r).parse_files(files)
    assert res.total_rows == 4 and len(res.complaints) == 2
    a, b = res.complaints
    assert a.customer_id == "CUST-1" and a.source.value == "EMAIL"
    assert a.original_text.startswith("Refund ORD-123456 missing") and a.timestamp.day == 9
    assert b.customer_id.startswith("EMAIL-") and b.original_text == "The app keeps crashing on checkout."
    assert {i.customer_link for i in res.items if i.accepted} == {"directory", "pseudonymous"}
    errs = " ".join(e.message for e in res.errors)
    assert "auto-generated" in errs and "Date header" in errs


def test_email_rejects_wrong_extension():
    with pytest.raises(IngestionError):
        EmailIngestionAdapter(resolver()).parse_files([UploadedFile("x.txt", b"hello")])


def test_html_email_body_is_converted():
    assert html_to_text("<html><body><p>Hi&nbsp;team</p><p>My order failed</p><script>x()</script></body></html>") \
        == "Hi\xa0team\n\nMy order failed"


@pytest.mark.skipif(not (CH / "email").exists(), reason="sample channel data not generated")
def test_email_sample_files_including_mbox():
    files = [UploadedFile(p.name, p.read_bytes()) for p in sorted((CH / "email").iterdir())]
    res = EmailIngestionAdapter(resolver()).parse_files(files)
    truth = [t for t in csv.DictReader(open(CH / "channel_ground_truth.csv")) if t["channel"] == "email"]
    assert res.total_rows == len(truth)
    assert len(res.complaints) == sum(t["kind"] != "reject" for t in truth)
    assert any(lbl.label.startswith("inbox_export.mbox#") for lbl in res.items)
    # quoted reply text is removed by preprocessing, not by the adapter (original kept verbatim)
    followup = next(c for c in res.complaints if "> On " in c.original_text)
    assert "Support wrote" not in clean_text(followup.original_text).text


# ------------------------------------------------------------------ chat
def test_chat_uses_only_customer_turns():
    data = [
        {"conversation_id": "A", "customer_email": "a@example.com", "started_at": "2026-11-01T10:00:00Z",
         "country": "India",
         "messages": [{"role": "bot", "text": "Hello! How can I help?"},
                      {"role": "customer", "text": "My card was charged twice", "timestamp": "2026-11-01T10:01:00Z"},
                      {"role": "agent", "text": "Sorry about that"},
                      {"role": "user", "text": "Transaction TXN-12345678"}]},
        {"conversation_id": "B", "messages": [{"role": "bot", "text": "Anyone there?"}]},
        {"conversation_id": "C"},
    ]
    res = ChatIngestionAdapter(resolver({("email", "a@example.com"): "CUST-7"})).parse_files(
        [UploadedFile("chats.json", json.dumps(data).encode())])
    assert len(res.complaints) == 1
    c = res.complaints[0]
    assert c.original_text == "My card was charged twice\nTransaction TXN-12345678"
    assert c.customer_id == "CUST-7" and c.source.value == "CHAT" and c.timestamp.minute == 1
    assert res.customers[0].country == "India"
    assert len(res.errors) == 2


def test_chat_invalid_json_is_file_level_error():
    with pytest.raises(IngestionError):
        ChatIngestionAdapter(resolver()).parse_files([UploadedFile("c.json", b"{not json")])


# ------------------------------------------------------------------ social
def test_social_posts_jsonl_and_csv():
    jsonl = "\n".join(json.dumps(p) for p in [
        {"post_id": "1", "handle": "@Asha_1", "text": "App crashes on login again", "created_at": "2026-11-05T10:00:00Z"},
        {"post_id": "2", "user": {"screen_name": "bob"}, "full_text": "Delivery late by a week"},
        {"post_id": "3", "handle": "@c", "text": "RT @Asha_1: App crashes on login again"},
    ]).encode()
    res = SocialIngestionAdapter(resolver({("social_handle", "asha_1"): "CUST-3"})).parse_files(
        [UploadedFile("posts.jsonl", jsonl),
         UploadedFile("reviews.csv", b"id,author,content,date\n9,@zed,Refund never came,2026-11-02\n")])
    ids = [c.customer_id for c in res.complaints]
    assert ids[0] == "CUST-3" and ids[1].startswith("SOCIAL-") and ids[2].startswith("SOCIAL-")
    assert all(c.source.value == "SOCIAL_MEDIA" for c in res.complaints)
    assert res.complaints[1].timestamp_inferred
    assert "repost" in res.errors[0].message


# ------------------------------------------------------------------ voice
def test_voice_adapter_with_manifest_and_failures():
    manifest = (b"filename,customer_id,phone,timestamp,country\n"
                b"a.wav,,+1 555 100 2000,2026-11-08T09:00:00Z,India\n"
                b"b.mp3,CUST-5,,2026-11-08T10:00:00Z,\n")
    tr = FakeTranscriber({"a.wav": "My order number 4 8 1 2 3 4 never arrived", "b.mp3": "Billing is wrong",
                          "c.ogg": "  "})
    adapter = VoiceIngestionAdapter(resolver({("phone", "5551002000"): "CUST-2"}), tr)
    res = adapter.parse_files([UploadedFile("a.wav", b"x"), UploadedFile("b.mp3", b"x"), UploadedFile("c.ogg", b"x"),
                               UploadedFile("d.wav", b"corrupt"), UploadedFile("calls.csv", manifest)])
    assert [c.customer_id for c in res.complaints] == ["CUST-2", "CUST-5"]
    assert res.complaints[0].original_text == "My order number 481234 never arrived"
    assert clean_text(res.complaints[0].original_text).reference_ids == ["ORD-481234"]
    assert res.customers[0].country == "India" and res.complaints[0].timestamp.hour == 9
    errs = {e.message for e in res.errors}
    assert any("no speech" in m for m in errs) and any("transcription failed" in m for m in errs)


def test_voice_requires_audio_and_available_transcriber():
    from app.ingestion.transcription import DisabledTranscriber

    with pytest.raises(IngestionError) as exc:
        VoiceIngestionAdapter(resolver(), DisabledTranscriber("off")).parse_files([UploadedFile("a.wav", b"x")])
    assert exc.value.status_code == 503
    with pytest.raises(IngestionError):
        VoiceIngestionAdapter(resolver(), FakeTranscriber({})).parse_files([UploadedFile("m.csv", b"filename\na.wav\n")])


def test_join_spoken_digits():
    assert join_spoken_digits("order 4, 8, 1, 2, 3, 4 please") == "order 481234 please"
    assert join_spoken_digits("I called 2 or 3 times") == "I called 2 or 3 times"


@pytest.mark.skipif(not list((CH / "voice").glob("*.wav")) if (CH / "voice").exists() else True,
                    reason="sample voice recordings not generated")
def test_local_whisper_transcribes_sample_call():
    """Real speech-to-text on a synthesized call (downloads the Whisper 'base' model on first run)."""
    pytest.importorskip("faster_whisper")
    from app.ingestion.transcription import LocalWhisperTranscriber

    script = {r["filename"]: r["text"] for r in csv.DictReader(open(CH / "voice" / "voice_scripts.csv"))}
    name = "call_dup_01.wav"
    out = LocalWhisperTranscriber("base").transcribe((CH / "voice" / name).read_bytes(), name)
    text = join_spoken_digits(out.text)
    expected_ref = [f"ORD-{''.join(ch for ch in script[name].split('order number ')[1][:11] if ch.isdigit())}"]
    assert out.language == "en" and len(text.split()) > 8
    assert clean_text(text).reference_ids == expected_ref


# ------------------------------------------------------------------ API + cross-channel dedup
CSV_BASE = """complaint_text,customer_id,source,timestamp,country
My refund for ORD-100001 has not arrived after two weeks,C1,email,2026-10-01T10:00:00Z,India
The app crashes when I open the cart,C2,chat,2026-10-02T10:00:00Z,India
"""


@pytest.fixture
def client(fake_settings):
    with TestClient(create_app(fake_settings)) as c:
        yield c


def _incident_of(client, complaint_id: str) -> str:
    return client.get(f"/api/v1/complaints/{complaint_id}").json()["data"]["incident_id"]


def test_cross_channel_duplicate_merges_into_one_incident(client):
    assert client.post("/api/v1/complaints/upload", files={"file": ("a.csv", CSV_BASE.encode(), "text/csv")}).status_code == 201
    d = client.post("/api/v1/customers/directory",
                    files={"file": ("dir.csv", b"customer_id,email,social_handle\nC1,c1@example.com,@cone\n", "text/csv")})
    assert d.status_code == 201 and d.json()["data"]["contacts_stored"] == 2

    eml = _eml("C One <c1@example.com>", "Refund", "Following up: order ORD-100001 refund still missing.",
               date="Fri, 02 Oct 2026 09:00:00 +0000")
    r = client.post("/api/v1/ingest/email", files=[("files", ("f.eml", eml, "message/rfc822"))])
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["channel"] == "EMAIL" and body["customer_links"] == {"directory": 1} and body["processing_started"]
    social = json.dumps({"post_id": "1", "handle": "@cone", "text": "Refund ORD-100001 still not here!",
                         "created_at": "2026-10-03T09:00:00Z"}).encode()
    assert client.post("/api/v1/ingest/social", files=[("files", ("p.jsonl", social))]).status_code == 201

    rows = client.get("/api/v1/complaints", params={"page_size": 50}).json()["data"]
    refund = [x for x in rows if "ORD-100001" in x["original_text"]]
    assert len(refund) == 3 and {x["source"] for x in refund} == {"EMAIL", "SOCIAL_MEDIA"}
    assert {x["customer_id"] for x in refund} == {"C1"}
    assert len({_incident_of(client, x["complaint_id"]) for x in refund}) == 1
    ov = client.get("/api/v1/analytics/overview").json()["data"]
    assert ov["total_reports"] == 4 and ov["total_incidents"] == 2

    # identical re-upload of the same email is rejected
    again = client.post("/api/v1/ingest/email", files=[("files", ("renamed.eml", eml, "message/rfc822"))])
    assert again.status_code == 409


def test_directory_upload_relinks_pseudonymous_complaints(client):
    post = json.dumps({"post_id": "1", "handle": "@late_joiner", "text": "My parcel ORD-200002 never came",
                       "created_at": "2026-10-03T09:00:00Z"}).encode()
    r = client.post("/api/v1/ingest/social", files=[("files", ("p.jsonl", post))])
    cid = r.json()["data"]["items"][0]
    assert cid["customer_link"] == "pseudonymous"
    complaint = client.get("/api/v1/complaints").json()["data"][0]
    assert complaint["customer_id"].startswith("SOCIAL-")
    d = client.post("/api/v1/customers/directory", params={"auto_recluster": False},
                    files={"file": ("dir.csv", b"customer_id,social_handle,country\nC42,@late_joiner,India\n")})
    assert d.json()["data"]["complaints_relinked"] == 1
    complaint = client.get(f"/api/v1/complaints/{complaint['complaint_id']}").json()["data"]
    assert complaint["customer_id"] == "C42"
    assert client.get("/api/v1/customers/directory").json()["data"]["by_type"]["social_handle"]["contacts"] == 1


def test_channel_endpoint_errors(client):
    assert client.post("/api/v1/ingest/fax", files=[("files", ("a.txt", b"x"))]).status_code == 404
    assert client.post("/api/v1/ingest/chat", files=[("files", ("a.csv", b"x"))]).status_code == 400
    r = client.get("/api/v1/ingest/channels").json()["data"]
    assert [c["channel"] for c in r] == ["csv", "email", "chat", "social", "voice"]
    batches = client.get("/api/v1/batches").json()["data"]
    assert batches == []
