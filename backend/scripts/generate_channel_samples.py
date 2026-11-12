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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--piper-python", help="python executable with piper-tts installed (to synthesize voice wavs)")
    ap.add_argument("--piper-model", help="path to a piper .onnx voice")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    rows = list(csv.DictReader(open(DATA / "sample_complaints.csv", encoding="utf-8")))
    customers = sorted({r["customer_id"] for r in rows if r["customer_id"]})
    for sub in ("email", "voice"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    for old in list((OUT / "email").glob("*")) + list((OUT / "voice").glob("*.wav")):
        old.unlink()

    # ---------------------------------------------------------------- directory
    directory: dict[str, dict] = {}
    for i, cid in enumerate(rng.sample(customers, 90)):
        name = FIRST[i % len(FIRST)]
        directory[cid] = {"customer_id": cid, "name": name,
                          "email": f"{name.lower()}.{cid[-4:]}@example.com",
                          "phone": f"+1 555 {100 + i:03d} {rng.randint(1000, 9999)}",
                          "social_handle": f"@{name.lower()}_{cid[-4:]}"}
    in_dir = list(directory)

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
    BOT_OPEN = "Hi! I'm ShopBot. How can I help you today?"
    BOT_HANDOFF = "I'm sorry to hear that. Let me connect you with an agent."
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
    convs.append({"conversation_id": "CHAT-BOTONLY", "started_at": iso(END - timedelta(days=2)),
                  "messages": [{"role": "bot", "text": BOT_OPEN}, {"role": "bot", "text": "Are you still there?"}]})
    truth.append({"channel": "chat", "item": "CHAT-BOTONLY", "kind": "reject", "reference": "", "customer_id": "",
                  "family": ""})
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
    with open(OUT / "customer_directory.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["customer_id", "email", "phone", "social_handle"])
        w.writeheader()
        for d in directory.values():
            w.writerow({k: d[k] for k in ("customer_id", "email", "phone", "social_handle")})
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
