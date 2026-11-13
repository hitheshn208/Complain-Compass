"""Generate sample multi-channel input (email, chat, social media, voice) + a customer directory.

Builds on data/sample_complaints.csv so the channels overlap realistically:
  * cross-channel duplicates - customers from the CSV re-report the SAME problem (same order /
    transaction number) by email, chat, social media or phone. These should be merged into the
    existing incident, not counted again.
  * new complaints - fresh incidents from the same issue families, from directory customers,
    unknown senders (pseudonymous) and anonymous users.
  * items that must be rejected - an auto-reply, a bot-only chat, a retweet.

Outputs (backend/data/channels/):
  customer_directory.csv      customer_id -> email / phone / social_handle (+ demographics)
  email/*.eml, email/inbox_export.mbox
  chat_transcripts.json
  social_posts.jsonl
  voice/voice_manifest.csv, voice/voice_scripts.csv, voice/*.wav (only with --piper-python)
  channel_ground_truth.csv    evaluation only - never read by the pipeline

Contacts use reserved example domains / fictional 555 numbers.

Run:  python scripts/generate_channel_samples.py [--piper-python PATH --piper-model PATH.onnx]
"""
from __future__ import annotations

import argparse
import csv
import json
import mailbox
import random
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_dataset import END, FAMILIES, make_ids  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = DATA / "channels"
FIRST = ["Asha", "Ben", "Chen", "Divya", "Elena", "Farah", "Gabe", "Hiro", "Isha", "Jon", "Kavya", "Leo", "Maya",
         "Nikhil", "Omar", "Priya", "Quinn", "Rahul", "Sara", "Tom", "Uma", "Vik", "Wen", "Yash", "Zoe"]
REF = re.compile(r"\b(ORD|TXN)-(\d+)\b")
BOT_OPEN = "Hi! I'm ShopBot. How can I help you today?"
BOT_HANDOFF = "I'm sorry to hear that. Let me connect you with an agent."


def parse_ts(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00").replace("/", "-")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def spoken(text: str) -> str:
    """How a caller would say IDs: 'ORD-481234' -> 'order number 4 8 1 2 3 4'."""
    word = {"ORD": "order number", "TXN": "transaction number"}
    text = re.sub(r"\b(?:[Oo]rder|[Tt]ransaction)\s+(?=(?:ORD|TXN)-)", "", text)
    return REF.sub(lambda m: f"{word[m.group(1)]} {' '.join(m.group(2))}", text)


def strip_html(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).replace("Sent from my iPhone", "").strip()


DEMOGRAPHICS = ["country", "state", "age_group", "student_status", "income_bracket"]
DIRECTORY_COLUMNS = ["customer_id", "email", "phone", "social_handle", *DEMOGRAPHICS]


def add_demographics(directory: dict[str, dict], rows: list[dict], rng: random.Random) -> None:
    """Give every directory customer a full profile, as a CRM export would have.

    Values a customer already has in sample_complaints.csv are copied unchanged, so the CSV and the
    directory never contradict each other. Missing fields are drawn from the CSV's own distribution
    (state drawn within the customer's country)."""
    known: dict[str, dict[str, str]] = {}
    for r in rows:
        if r["customer_id"]:
            for k in DEMOGRAPHICS:
                if r.get(k):
                    known.setdefault(r["customer_id"], {})[k] = r[k]
    pool = {k: [r[k] for r in rows if r.get(k)] for k in DEMOGRAPHICS}
    states_by_country: dict[str, list[str]] = {}
    for r in rows:
        if r.get("country") and r.get("state"):
            states_by_country.setdefault(r["country"], []).append(r["state"])
    for cid in sorted(directory):
        profile = dict(known.get(cid, {}))
        profile.setdefault("country", rng.choice(pool["country"]))
        if "state" not in profile and states_by_country.get(profile["country"]):
            profile["state"] = rng.choice(states_by_country[profile["country"]])
        for k in ("age_group", "student_status", "income_bracket"):
            profile.setdefault(k, rng.choice(pool[k]))
        directory[cid].update(profile)


def write_directory(directory: dict[str, dict]) -> None:
    with open(OUT / "customer_directory.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DIRECTORY_COLUMNS)
        w.writeheader()
        for d in directory.values():
            w.writerow({k: d.get(k, "") for k in DIRECTORY_COLUMNS})


def chat_repeats(convs: list[dict], truth: list[dict], directory: dict[str, dict], rng: random.Random) -> list[dict]:
    """Follow-up conversations for two of the new, known-customer chats (uses its own RNG so that
    no other generated sample changes)."""
    fam_of = {t["item"]: t["family"] for t in truth if t["channel"] == "chat"}
    originals = [c for c in convs if c["conversation_id"].startswith("CHAT-N") and c.get("customer_id")]
    text_of = {c["conversation_id"]: next(m["text"] for m in c["messages"] if m["role"] == "customer") for c in originals}
    with_ref = [c for c in originals if REF.search(text_of[c["conversation_id"]])]
    without_ref = [c for c in originals if not REF.search(text_of[c["conversation_id"]])]
    picks = [(c, True) for c in with_ref[:1]] + [(c, False) for c in without_ref[:1]]
    out = []
    for k, (orig, with_order) in enumerate(picks, start=1):
        oid, cid = orig["conversation_id"], orig["customer_id"]
        fam, old_text = fam_of[oid], text_of[oid]
        # A different template of the same issue family = same problem, different wording.
        ids = make_ids(rng)
        fresh = [t for t, _ in FAMILIES[fam]["templates"] if re.sub(r"\{\w+\}", "", t)[:25] not in old_text]
        if with_order:
            text = rng.choice(fresh or [t for t, _ in FAMILIES[fam]["templates"]]).format(**ids)
        else:
            # No order number, so only meaning + time + topic can link it. Take the closest paraphrase
            # (most shared word stems): a plausible re-wording rather than a different symptom.
            fresh = [t for t in fresh if not REF.search(t.format(**ids))] or fresh
            stems = lambda x: {w[:4] for w in re.findall(r"[a-z]{4,}", x.lower())}  # noqa: E731
            text = max(fresh, key=lambda t: len(stems(t.format(**ids)) & stems(old_text))).format(**ids)
        if with_order:
            ref = REF.search(old_text).group(0)
            text = REF.sub(ref, text) if REF.search(text) else f"{text} It's about order {ref} again."
        start = parse_ts(orig["started_at"])
        ts = min(start + timedelta(days=2, hours=rng.uniform(1, 6)), END - timedelta(hours=1))
        if ts <= start:
            ts = start + timedelta(minutes=45)
        who = {"customer_email": directory[cid]["email"]} if with_order else {"customer_id": cid}
        conv_id = f"CHAT-R{k:02d}"
        msgs = [{"role": "bot", "text": BOT_OPEN, "timestamp": iso(ts)},
                {"role": "customer", "text": "Hi, it's me again.", "timestamp": iso(ts + timedelta(seconds=40))},
                {"role": "customer", "text": text, "timestamp": iso(ts + timedelta(seconds=75))},
                {"role": "bot", "text": BOT_HANDOFF, "timestamp": iso(ts + timedelta(seconds=80))},
                {"role": "agent", "text": "Sorry about this, I can see your earlier chat. Escalating it now.",
                 "timestamp": iso(ts + timedelta(minutes=4))}]
        out.append({"conversation_id": conv_id, **who, "started_at": iso(ts), "messages": msgs})
        truth.append({"channel": "chat", "item": conv_id, "kind": "repeat", "reference": oid, "customer_id": cid,
                      "family": fam})
    return out


# How a customer re-words each issue family when chasing it on another channel.
ECHO_PHRASES: dict[str, tuple[str, str]] = {  # family: (social post wording, chat wording)
    "payment_failure": ("my money was taken but the payment still shows as failed",
                        "the payment failed but the amount was still debited from my account"),
    "refund_delay": ("the refund you approved still hasn't reached my account",
                     "I'm still waiting for my refund to show up"),
    "account_suspension": ("my account is still locked and nobody will say why",
                           "my account is still suspended and I can't use it"),
    "incorrect_billing": ("I'm still being billed the wrong amount",
                          "the extra charge on my bill still hasn't been reversed"),
    "damaged_product": ("I'm still stuck with a broken product",
                        "the item arrived damaged and nobody has arranged a replacement"),
    "login_problem": ("I still can't get into my account",
                      "I'm still unable to log in to my account"),
    "delivery_delay": ("the parcel has been stuck 'in transit' for weeks",
                       "it still hasn't been delivered, tracking just says in transit"),
    "app_crash": ("the app is still crashing every time I open it",
                  "the app keeps crashing and I can't place anything"),
    "subscription_cancel": ("you're still charging me for a subscription I cancelled",
                            "I cancelled my subscription but I'm still being charged"),
    "poor_support": ("support still hasn't replied to me",
                     "nobody from support has actually helped me yet"),
}


def cross_channel_echo(r: dict, family: str, d: dict, after: datetime, rng: random.Random) -> tuple[dict, dict, list[dict]]:
    """The same customer chases the same problem on social media and then in chat after an email
    follow-up: one incident reported on CSV + email + social + chat. Uses its own RNG."""
    ref = REF.search(strip_html(r["complaint_text"])).group(0)
    social_text, chat_text = ECHO_PHRASES.get(family, ("this is still not fixed", "this still isn't fixed"))
    post_ts = after + timedelta(hours=rng.uniform(20, 30))
    chat_ts = after + timedelta(hours=rng.uniform(44, 54))
    post = {"post_id": "SM-X01", "platform": "twitter", "handle": d["social_handle"],  # handle only, no ID
            "text": f"@ShopCare emailed you twice about order {ref} and {social_text}. Does anyone read these?!",
            "created_at": iso(post_ts)}
    digits = ref.split("-", 1)[1]
    conv = {"conversation_id": "CHAT-X01", "customer_phone": d["phone"], "started_at": iso(chat_ts),  # phone only
            "messages": [
                {"role": "bot", "text": BOT_OPEN, "timestamp": iso(chat_ts)},
                {"role": "customer", "text": "I already emailed and tweeted about this.",
                 "timestamp": iso(chat_ts + timedelta(seconds=35))},
                {"role": "customer", "text": f"My order number is {digits} and {chat_text}.",  # no ORD- prefix
                 "timestamp": iso(chat_ts + timedelta(seconds=70))},
                {"role": "bot", "text": BOT_HANDOFF, "timestamp": iso(chat_ts + timedelta(seconds=75))},
                {"role": "agent", "text": "I can see your email and your post, escalating this now.",
                 "timestamp": iso(chat_ts + timedelta(minutes=3))}]}
    truth = [{"channel": "social", "item": "SM-X01", "kind": "cross_channel_duplicate", "reference": ref,
              "customer_id": r["customer_id"], "family": ""},
             {"channel": "chat", "item": "CHAT-X01", "kind": "cross_channel_duplicate", "reference": ref,
              "customer_id": r["customer_id"], "family": ""}]
    return post, conv, truth


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--piper-python", help="python executable with piper-tts installed (to synthesize voice wavs)")
    ap.add_argument("--piper-model", help="path to a piper .onnx voice")
    ap.add_argument("--directory-only", action="store_true",
                    help="only rewrite customer_directory.csv (keeps the email/chat/social/voice samples)")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    rows = list(csv.DictReader(open(DATA / "sample_complaints.csv", encoding="utf-8")))
    customers = sorted({r["customer_id"] for r in rows if r["customer_id"]})
    # ---------------------------------------------------------------- directory
    directory: dict[str, dict] = {}
    for i, cid in enumerate(rng.sample(customers, 90)):
        name = FIRST[i % len(FIRST)]
        directory[cid] = {"customer_id": cid, "name": name,
                          "email": f"{name.lower()}.{cid[-4:]}@example.com",
                          "phone": f"+1 555 {100 + i:03d} {rng.randint(1000, 9999)}",
                          "social_handle": f"@{name.lower()}_{cid[-4:]}"}
    in_dir = list(directory)
    # Separate RNG so adding demographics does not change any other generated sample.
    add_demographics(directory, rows, random.Random(args.seed + 1))
    if args.directory_only:
        write_directory(directory)
        print("wrote", OUT / "customer_directory.csv", f"({len(directory)} customers)")
        return

    for sub in ("email", "voice"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    for old in list((OUT / "email").glob("*")) + list((OUT / "voice").glob("*.wav")):
        old.unlink()

    # CSV complaints (with customer + reference id) whose owner is in the directory -> re-reports
    dup_sources = [r for r in rows if r["customer_id"] in directory and REF.search(r["complaint_text"])
                   and r["timestamp"].strip()]
    rng.shuffle(dup_sources)
    dup_sources = dup_sources[:20]
    truth: list[dict] = []

    def follow_ts(r: dict) -> datetime:
        return min(parse_ts(r["timestamp"]) + timedelta(hours=rng.uniform(2, 40)), END)

    def new_complaint(family: str | None = None) -> tuple[str, str, datetime]:
        family = family or rng.choice(list(FAMILIES))
        tpl, _sev = rng.choice(FAMILIES[family]["templates"])
        ts = END - timedelta(days=rng.uniform(0, 12), hours=rng.uniform(0, 5))
        return family, tpl.format(**make_ids(rng)), ts

    # ---------------------------------------------------------------- email
    emails: list[tuple[str, EmailMessage]] = []

    def make_email(sender: str, subject: str, body: str, ts: datetime, html: bool = False) -> EmailMessage:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = sender, "support@shop.example", subject
        m["Date"], m["Message-ID"] = format_datetime(ts), make_msgid(domain="example.com")
        if html:
            m.set_content(body, subtype="html")
        else:
            m.set_content(body)
        return m

    for n, r in enumerate(dup_sources[:7], start=1):
        d = directory[r["customer_id"]]
        orig = strip_html(r["complaint_text"])
        ref = REF.search(orig).group(0)
        body = (f"Hello,\n\nI reported this a while ago and have not heard back. {orig}\n\n"
                f"Please sort this out.\n\nThanks,\n{d['name']}\n\n"
                f"> On {format_datetime(parse_ts(r['timestamp']))}, Support wrote:\n> We have received your request.")
        emails.append((f"followup_{n:02d}.eml",
                       make_email(f"{d['name']} <{d['email']}>", f"Re: {ref} still not resolved", body, follow_ts(r))))
        truth.append({"channel": "email", "item": f"followup_{n:02d}.eml", "kind": "cross_channel_duplicate",
                      "reference": ref, "customer_id": r["customer_id"], "family": ""})
    # The last email follow-up's customer also chases the same problem on social media and in chat.
    echo_src = dup_sources[6]
    families = {int(g["row_number"]): g["issue_family"] for g in csv.DictReader(open(DATA / "ground_truth.csv"))}
    echo_post, echo_conv, echo_truth = cross_channel_echo(
        echo_src, families[rows.index(echo_src) + 1], directory[echo_src["customer_id"]],
        emails[-1][1]["Date"].datetime, random.Random(args.seed + 3))
    for n in range(1, 11):
        fam, text, ts = new_complaint()
        if n <= 6:
            cid = rng.choice(in_dir)
            sender, who = f"{directory[cid]['name']} <{directory[cid]['email']}>", cid
        else:
            sender, who = f"Customer <buyer{rng.randint(100, 999)}@example.org>", "pseudonymous"
        html = n == 3
        body = f"<html><body><p>Hi team,</p><p>{text}</p><p>Regards</p></body></html>" if html else \
            f"Hi team,\n\n{text}\n\nRegards"
        name = f"new_{n:02d}.eml"
        emails.append((name, make_email(sender, "Complaint", body, ts, html=html)))
        truth.append({"channel": "email", "item": name, "kind": "new", "reference": "", "customer_id": who,
                      "family": fam})
    auto = make_email(f"Bot <{directory[in_dir[0]]['email']}>", "Automatic reply: Out of office",
                      "I am out of the office until Monday.", END - timedelta(days=1))
    auto["Auto-Submitted"] = "auto-replied"
    emails.append(("auto_reply.eml", auto))
    truth.append({"channel": "email", "item": "auto_reply.eml", "kind": "reject", "reference": "", "customer_id": "",
                  "family": ""})

    box = mailbox.mbox(OUT / "email" / "inbox_export.mbox")
    box.lock()
    for name, msg in emails:
        if name.startswith("new_0") and int(name[4:6]) >= 7:  # unknown senders arrive as one mailbox export
            box.add(msg)
        else:
            (OUT / "email" / name).write_bytes(msg.as_bytes())
    box.flush()
    box.unlock()
    box.close()

    # ---------------------------------------------------------------- chat
    convs = []

    def conversation(cid_field: dict, turns: list[str], ts: datetime, conv_id: str) -> dict:
        msgs, t = [{"role": "bot", "text": BOT_OPEN, "timestamp": iso(ts)}], ts
        for turn in turns:
            t += timedelta(seconds=rng.randint(20, 90))
            msgs.append({"role": "customer", "text": turn, "timestamp": iso(t)})
            t += timedelta(seconds=5)
            msgs.append({"role": "bot", "text": BOT_HANDOFF, "timestamp": iso(t)})
        msgs.append({"role": "agent", "text": "Thanks for your patience, I have escalated this.",
                     "timestamp": iso(t + timedelta(minutes=3))})
        return {"conversation_id": conv_id, **cid_field, "started_at": iso(ts), "messages": msgs}

    for n, r in enumerate(dup_sources[7:12], start=1):
        orig = strip_html(r["complaint_text"])
        ref = REF.search(orig).group(0)
        field = {"customer_id": r["customer_id"]} if n % 2 else {"customer_email": directory[r["customer_id"]]["email"]}
        convs.append(conversation(field, ["hi", orig, "This is the second time I'm contacting you about this."],
                                  follow_ts(r), f"CHAT-D{n:02d}"))
        truth.append({"channel": "chat", "item": f"CHAT-D{n:02d}", "kind": "cross_channel_duplicate", "reference": ref,
                      "customer_id": r["customer_id"], "family": ""})
    for n in range(1, 10):
        fam, text, ts = new_complaint()
        if n <= 5:
            cid = rng.choice(in_dir)
            field, who = {"customer_id": cid, "country": None}, cid
            field.pop("country")
        else:
            field, who = {}, "anonymous"
        convs.append(conversation(field, [text], ts, f"CHAT-N{n:02d}"))
        truth.append({"channel": "chat", "item": f"CHAT-N{n:02d}", "kind": "new", "reference": "", "customer_id": who,
                      "family": fam})
    # Same customer, same problem, a second conversation worded differently: dedup *within* one channel.
    # R01 cites the same order and identifies the customer by email only (rule T1 via the directory);
    # R02 has no order number, so it must merge on time window + meaning + topic (rule T2).
    convs += chat_repeats(convs, truth, directory, random.Random(args.seed + 2))
    convs.append({"conversation_id": "CHAT-BOTONLY", "started_at": iso(END - timedelta(days=2)),
                  "messages": [{"role": "bot", "text": BOT_OPEN}, {"role": "bot", "text": "Are you still there?"}]})
    truth.append({"channel": "chat", "item": "CHAT-BOTONLY", "kind": "reject", "reference": "", "customer_id": "",
                  "family": ""})
    convs.append(echo_conv)
    truth.append(echo_truth[1])
    (OUT / "chat_transcripts.json").write_text(json.dumps({"conversations": convs}, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- social
    posts = []
    for n, r in enumerate(dup_sources[12:16], start=1):
        d = directory[r["customer_id"]]
        orig = strip_html(r["complaint_text"])
        ref = REF.search(orig).group(0)
        posts.append({"post_id": f"SM-D{n:02d}", "platform": "twitter", "handle": d["social_handle"],
                      "text": f"@ShopCare {orig} Still waiting!!", "created_at": iso(follow_ts(r))})
        truth.append({"channel": "social", "item": f"SM-D{n:02d}", "kind": "cross_channel_duplicate",
                      "reference": ref, "customer_id": r["customer_id"], "family": ""})
    for n in range(1, 17):
        fam, text, ts = new_complaint("app_crash" if n <= 5 else None)
        if n <= 5:
            ts = END - timedelta(hours=rng.uniform(1, 60))
        if n % 3 == 0:
            cid = rng.choice(in_dir)
            handle, who = directory[cid]["social_handle"], cid
        else:
            handle, who = f"@user{rng.randint(1000, 9999)}", "pseudonymous"
        posts.append({"post_id": f"SM-N{n:02d}", "platform": rng.choice(["twitter", "instagram", "facebook"]),
                      "handle": handle, "text": f"{text} #fail @ShopCare", "created_at": iso(ts)})
        truth.append({"channel": "social", "item": f"SM-N{n:02d}", "kind": "new", "reference": "",
                      "customer_id": who, "family": fam})
    posts.append({"post_id": "SM-RT01", "platform": "twitter", "handle": "@someone_else", "is_retweet": True,
                  "text": f"RT {posts[0]['handle']}: {posts[0]['text']}", "created_at": iso(END - timedelta(hours=3))})
    truth.append({"channel": "social", "item": "SM-RT01", "kind": "reject", "reference": "", "customer_id": "",
                  "family": ""})
    posts.append(echo_post)
    truth.append(echo_truth[0])
    with open(OUT / "social_posts.jsonl", "w", encoding="utf-8") as fh:
        for p in posts:
            fh.write(json.dumps(p) + "\n")

    # ---------------------------------------------------------------- voice
    scripts, manifest = [], []
    for n, r in enumerate(dup_sources[16:20], start=1):
        d = directory[r["customer_id"]]
        orig = strip_html(r["complaint_text"])
        ref = REF.search(orig).group(0)
        name = f"call_dup_{n:02d}.wav"
        scripts.append({"filename": name, "text": f"Hello, I'm calling again about a problem. {spoken(orig)} "
                                                  "Nobody has fixed it yet, please help."})
        manifest.append({"filename": name, "customer_id": "", "phone": d["phone"], "timestamp": iso(follow_ts(r))})
        truth.append({"channel": "voice", "item": name, "kind": "cross_channel_duplicate", "reference": ref,
                      "customer_id": r["customer_id"], "family": ""})
    for n in range(1, 5):
        fam, text, ts = new_complaint()
        name = f"call_new_{n:02d}.wav"
        scripts.append({"filename": name, "text": f"Hi, thanks for picking up. {spoken(text)} I'd like this resolved."})
        if n == 1:
            cid = rng.choice(in_dir)
            row, who = {"customer_id": cid, "phone": ""}, cid
        elif n == 2:
            row, who = {"customer_id": "", "phone": f"+1 555 9{rng.randint(10, 99)} {rng.randint(1000, 9999)}"}, \
                "pseudonymous"
        else:
            row, who = {"customer_id": "", "phone": ""}, "anonymous"
        if n < 4:  # the last call is deliberately missing from the manifest
            manifest.append({"filename": name, **row, "timestamp": iso(ts)})
        truth.append({"channel": "voice", "item": name, "kind": "new", "reference": "", "customer_id": who,
                      "family": fam})
    with open(OUT / "voice" / "voice_scripts.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "text"])
        w.writeheader()
        w.writerows(scripts)
    with open(OUT / "voice" / "voice_manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["filename", "customer_id", "phone", "timestamp"])
        w.writeheader()
        w.writerows(manifest)
    if args.piper_python and args.piper_model:
        for s in scripts:
            subprocess.run([args.piper_python, "-m", "piper", "-m", args.piper_model, "-f",
                            str(OUT / "voice" / s["filename"])], input=s["text"].encode(), check=True,
                           capture_output=True)
        print(f"synthesized {len(scripts)} voice recordings")
    else:
        print("voice: scripts + manifest written; pass --piper-python/--piper-model to synthesize .wav files")

    # ---------------------------------------------------------------- directory + truth
    write_directory(directory)
    with open(OUT / "channel_ground_truth.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["channel", "item", "kind", "reference", "customer_id", "family"])
        w.writeheader()
        w.writerows(truth)
    kinds = {}
    for t in truth:
        kinds[(t["channel"], t["kind"])] = kinds.get((t["channel"], t["kind"]), 0) + 1
    print("wrote", OUT, dict(sorted(kinds.items())))


if __name__ == "__main__":
    main()
