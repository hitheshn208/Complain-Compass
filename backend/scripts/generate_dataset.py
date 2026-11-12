"""Generate a realistic synthetic complaint dataset with separate ground truth.

Outputs (in backend/data/):
  sample_complaints.csv  - pipeline input (no labels)
  ground_truth.csv       - row_number -> issue_family, severity, incident_key (evaluation ONLY)
  sample_population.csv  - optional segment population denominators

Run:  python scripts/generate_dataset.py [--seed 42]
"""
from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
END = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
DAYS = 60

# family -> (templates, base severity per template, relative volume, growth profile)
# Severity in templates: L/M/H/C. Slots: {order} {amount} {days} {txn}
FAMILIES: dict[str, dict] = {
    "payment_failure": {
        "volume": 1.3, "profile": "flat",
        "templates": [
            ("My payment failed at checkout but {amount} was deducted from my bank account. Order {order}.", "H"),
            ("Tried to pay three times, card keeps getting declined even though it works everywhere else.", "M"),
            ("Payment gateway error during checkout. Transaction {txn} shows failed but money is gone from my account.", "H"),
            ("UPI payment failed, amount debited but order not placed. Please help, this was {amount}.", "H"),
            ("Checkout just says 'payment could not be processed' every time I try to pay.", "M"),
            ("The payment page hangs after I enter my OTP and then shows transaction failed.", "M"),
            ("Money got debited twice for a failed payment, {amount} each, and no order was created. Transaction {txn}.", "H"),
            ("Card declined at checkout for no reason, bank says there is no issue on their side.", "M"),
        ],
    },
    "refund_delay": {
        "volume": 1.1, "profile": "flat",
        "templates": [
            ("I returned order {order} {days} days ago and still have not received my refund of {amount}.", "H"),
            ("Where is my refund? It has been {days} days since the return was picked up.", "M"),
            ("Refund was approved but the money never reached my account. Order {order}.", "H"),
            ("You promised a refund in 5-7 business days, it's been {days} days. Still nothing.", "M"),
            ("Still waiting on refund for cancelled order {order}, support keeps saying it is processing.", "M"),
            ("My refund of {amount} is stuck in 'initiated' status for weeks.", "H"),
            ("I cancelled my order and the refund hasn't been credited yet after {days} days.", "M"),
        ],
    },
    "delivery_delay": {
        "volume": 1.4, "profile": "flat",
        "templates": [
            ("My package {order} was supposed to arrive {days} days ago and tracking hasn't updated.", "M"),
            ("Delivery is delayed again. Order {order} still shows 'in transit'.", "M"),
            ("Order {order} has not been delivered, the expected date passed {days} days ago.", "M"),
            ("The courier keeps rescheduling my delivery, it is very late now.", "L"),
            ("Tracking says delivered but I never received my parcel {order}.", "H"),
            ("Shipping is taking forever, ordered two weeks ago and nothing has arrived.", "M"),
            ("My shipment is stuck at the warehouse for {days} days with no update.", "M"),
        ],
    },
    "damaged_product": {
        "volume": 0.9, "profile": "flat",
        "templates": [
            ("The item in order {order} arrived damaged, the box was crushed and the product is broken.", "M"),
            ("Received a cracked screen on my new phone, order {order}.", "H"),
            ("Product arrived defective and doesn't turn on at all.", "M"),
            ("The glass bottles were shattered when the package arrived.", "M"),
            ("My order came with torn packaging and a broken handle, very disappointed.", "L"),
            ("The blender I received smells like burning and started smoking on first use. Order {order}.", "C"),
            ("Item delivered with dents and scratches all over, clearly damaged in transit.", "L"),
        ],
    },
    "login_problem": {
        "volume": 1.0, "profile": "growing",
        "templates": [
            ("I can't log in to my account, it keeps saying invalid password even after resetting it.", "H"),
            ("Not receiving the OTP verification code so I cannot sign in.", "H"),
            ("Login page just spins forever after I enter my credentials.", "M"),
            ("Password reset link is expired every time I click it, locked out of my account.", "H"),
            ("Two-factor authentication code never arrives, cannot access my account.", "H"),
            ("Sign in fails with 'something went wrong' on both app and website.", "M"),
            ("I keep getting logged out and then can't log back in.", "M"),
        ],
    },
    "account_suspension": {
        "volume": 0.5, "profile": "flat",
        "templates": [
            ("My account was suspended without any explanation and I have an active order {order}.", "H"),
            ("Why was my account banned? I haven't done anything wrong.", "H"),
            ("Account deactivated suddenly, all my saved data and wallet balance of {amount} is inaccessible.", "C"),
            ("I got an email saying my account is blocked for suspicious activity, but it was me.", "H"),
            ("My seller account has been disabled and it is affecting my business income.", "C"),
        ],
    },
    "poor_support": {
        "volume": 0.9, "profile": "flat",
        "templates": [
            ("Customer service agent was rude and hung up on me.", "M"),
            ("I've been on hold for over an hour and nobody answered.", "M"),
            ("Raised a ticket {days} days ago and got no response at all.", "M"),
            ("Support keeps sending copy-paste replies without reading my issue.", "L"),
            ("The chat agent closed my conversation without solving anything.", "L"),
            ("Your support team is useless, every agent tells me something different.", "M"),
        ],
    },
    "subscription_cancel": {
        "volume": 0.7, "profile": "flat",
        "templates": [
            ("I cannot cancel my subscription, the cancel button gives an error.", "M"),
            ("Cancelled my membership but was still charged {amount} for renewal.", "H"),
            ("There is no option to turn off auto-renew for my plan.", "M"),
            ("I've tried to unsubscribe three times and I'm still being billed monthly.", "H"),
            ("Subscription cancellation page keeps redirecting me back to the plans page.", "M"),
        ],
    },
    "incorrect_billing": {
        "volume": 0.8, "profile": "flat",
        "templates": [
            ("I was charged twice for the same order {order}, please reverse the extra {amount}.", "H"),
            ("My invoice shows a higher amount than the price at checkout.", "M"),
            ("There is an extra charge of {amount} on my bill that I never authorized.", "H"),
            ("Billed for items I never ordered on invoice {order}.", "H"),
            ("The monthly bill has wrong tax calculation, I was overcharged.", "M"),
            ("There are unauthorized charges on my card from your company, this looks like fraud.", "C"),
        ],
    },
    "app_crash": {
        "volume": 0.8, "profile": "spike",
        "templates": [
            ("The app crashes every time I open the cart after the latest update.", "M"),
            ("App keeps closing by itself on my Android phone since yesterday.", "M"),
            ("Mobile app freezes on the home screen and then force closes.", "M"),
            ("After updating, the app crashes immediately on launch.", "H"),
            ("The iOS app shows a blank white screen and then crashes.", "M"),
            ("Can't use the app at all, it crashes whenever I try to checkout.", "H"),
        ],
    },
}

ISOLATED = [
    ("The font on your website is a bit too small to read comfortably.", "L"),
    ("Would be nice to have a dark mode option in the app.", "L"),
    ("The product description said cotton but the label says polyester.", "L"),
    ("Your parking lot at the pickup store has no lighting at night.", "M"),
    ("The gift wrapping option was missing from my order.", "L"),
    ("I received someone else's invoice with their name and address in my package.", "H"),
    ("Your newsletter emails come in Spanish even though I chose English.", "L"),
    ("The store opening hours on Google are wrong.", "L"),
    ("The loyalty points from my last purchase never showed up.", "L"),
    ("Your delivery driver blocked my driveway for twenty minutes.", "L"),
    ("The coupon code from your ad did not work at all.", "L"),
    ("Product size chart is inaccurate, the medium fits like a small.", "L"),
    ("The assembly instructions are missing step 4.", "L"),
    ("Your website accessibility for screen readers is very poor.", "M"),
    ("Got a survey request five times in one day.", "L"),
    ("The warranty registration page has a broken link.", "L"),
    ("Price dropped right after I bought it, can I get the difference?", "L"),
    ("Your recycling instructions for packaging are confusing.", "L"),
]

ESCALATIONS = [
    (" This is unacceptable, I'm considering legal action.", "C"),
    (" I am furious, this is urgent.", None),
    (" Please fix this asap.", None),
    (" Honestly a minor thing but wanted to report it.", "L"),
]

NOISE = [
    "<p>{t}</p>",
    "{t}\n\nSent from my iPhone",
    "{t} You can reach me at {email} or {phone}.",
    "   {t}   ",
    "{t}!!!",
    "Hi team,\n{t}\nThanks",
]

COUNTRIES = {
    "India": ["Karnataka", "Maharashtra", "Delhi", "Tamil Nadu", "Telangana"],
    "United States": ["California", "Texas", "New York", "Florida"],
    "United Kingdom": ["England", "Scotland"],
    "Canada": ["Ontario", "British Columbia"],
}
COUNTRY_WEIGHTS = [0.45, 0.3, 0.15, 0.1]
AGE_GROUPS = ["18-24", "25-34", "35-44", "45-54", "55+"]
INCOMES = ["<25k", "25k-50k", "50k-100k", "100k+"]
SOURCES = ["EMAIL", "CHAT", "VOICE", "SOCIAL_MEDIA"]
SOURCE_SPELLINGS = {"EMAIL": ["email", "EMAIL", "Email"], "CHAT": ["chat", "CHAT", "live chat"],
                    "VOICE": ["voice", "phone", "VOICE"], "SOCIAL_MEDIA": ["social_media", "twitter", "SOCIAL_MEDIA"]}
SEV = {"L": "LOW", "M": "MEDIUM", "H": "HIGH", "C": "CRITICAL"}


@dataclass
class Customer:
    customer_id: str
    country: str | None
    state: str | None
    age_group: str | None
    student_status: str | None
    income_bracket: str | None


def make_customers(rng: random.Random, n: int) -> list[Customer]:
    out = []
    for i in range(n):
        country = rng.choices(list(COUNTRIES), COUNTRY_WEIGHTS)[0]
        age = rng.choices(AGE_GROUPS, [0.25, 0.32, 0.2, 0.13, 0.1])[0]
        student = "Student" if (age == "18-24" and rng.random() < 0.7) or (age == "25-34" and rng.random() < 0.15) \
            else "Non-student"
        income = rng.choices(INCOMES, [0.5, 0.3, 0.15, 0.05] if student == "Student" else [0.15, 0.3, 0.35, 0.2])[0]
        c = Customer(f"CUST-{1000 + i}", country, rng.choice(COUNTRIES[country]), age, student, income)
        # incomplete demographics
        for field in ("state", "age_group", "student_status", "income_bracket"):
            if rng.random() < 0.12:
                setattr(c, field, None)
        if rng.random() < 0.04:
            c.country, c.state = None, None
        out.append(c)
    return out


def sample_time(rng: random.Random, profile: str) -> datetime:
    """Draw a timestamp in the 60-day window according to a volume profile."""
    while True:
        day = rng.uniform(0, DAYS)  # days before END
        if profile == "flat":
            break
        if profile == "growing" and rng.random() < (1 - day / DAYS) ** 1.5 + 0.15:
            break
        if profile == "spike":
            if day < 4 and rng.random() < 0.95:
                break
            if day >= 4 and rng.random() < 0.08:
                break
    ts = END - timedelta(days=day, hours=rng.uniform(0, 3))
    return ts.replace(microsecond=0)


def make_ids(rng: random.Random) -> dict[str, str]:
    return {
        "order": f"ORD-{rng.randint(100000, 999999)}",
        "txn": f"TXN-{rng.randint(10000000, 99999999)}",
        "amount": rng.choice(["$", "Rs ", "£"]) + str(rng.choice([19, 49, 89, 120, 249, 499, 1299, 2499])),
        "days": str(rng.choice([8, 10, 12, 15, 21, 30])),
    }


def fill(template: str, ids: dict[str, str]) -> str:
    """Fill a template with one incident's identifiers (all reports of an incident share them)."""
    return template.format(**ids)


def fmt_ts(rng: random.Random, ts: datetime) -> str:
    r = rng.random()
    if r < 0.75:
        return ts.isoformat().replace("+00:00", "Z")
    if r < 0.9:
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return ts.strftime("%Y/%m/%d %H:%M")


def add_noise(rng: random.Random, text: str, customer: Customer | None) -> str:
    if rng.random() < 0.18:
        tpl = rng.choice(NOISE)
        email = f"user{rng.randint(10, 999)}@example.com"
        phone = f"+1 555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"
        return tpl.format(t=text, email=email, phone=phone)
    return text


def generate(seed: int = 42, n_customers: int = 260, n_incidents: int = 450) -> None:
    rng = random.Random(seed)
    customers = make_customers(rng, n_customers)
    fam_names = list(FAMILIES)
    fam_weights = [FAMILIES[f]["volume"] for f in fam_names]
    rows: list[dict] = []
    truth: list[dict] = []
    incident_no = 0

    def emit(text, cust, source, ts, family, severity, incident_key, ref_dup=False):
        row = {
            "complaint_text": text,
            "customer_id": cust.customer_id if cust else "",
            "source": rng.choice(SOURCE_SPELLINGS[source]),
            "timestamp": fmt_ts(rng, ts),
            "country": (cust.country or "") if cust else "",
            "state": (cust.state or "") if cust else "",
            "age_group": (cust.age_group or "") if cust else "",
            "student_status": (cust.student_status or "") if cust else "",
            "income_bracket": (cust.income_bracket or "") if cust else "",
        }
        # Same customer's later reports sometimes omit demographics (upsert must not erase them)
        if cust and rng.random() < 0.2:
            for k in ("country", "state", "age_group", "student_status", "income_bracket"):
                row[k] = ""
        rows.append(row)
        truth.append({"issue_family": family, "severity": severity, "incident_key": incident_key,
                      "customer_id": row["customer_id"], "is_duplicate_report": int(ref_dup)})

    # A subset of "heavy" customers produces recurring incidents
    heavy = rng.sample(customers, 30)
    for _ in range(n_incidents):
        incident_no += 1
        family = rng.choices(fam_names, fam_weights)[0]
        spec = FAMILIES[family]
        anonymous = rng.random() < 0.08
        cust = None if anonymous else (rng.choice(heavy) if rng.random() < 0.2 else rng.choice(customers))
        template, sev_code = rng.choice(spec["templates"])
        ids = make_ids(rng)
        text = fill(template, ids)
        severity = SEV[sev_code]
        r = rng.random()
        if r < 0.05:
            esc, esc_sev = ESCALATIONS[0]
            text += esc
            severity = SEV[esc_sev]
        elif r < 0.15:
            text += ESCALATIONS[1][0]
        elif r < 0.2:
            text += ESCALATIONS[2][0]
        ts = sample_time(rng, spec["profile"])
        source = rng.choice(SOURCES)
        key = f"INC-{incident_no:04d}"
        emit(add_noise(rng, text, cust), cust, source, ts, family, severity, key)

        # Duplicate reports of the same incident through other channels (known customers only).
        # Follow-ups reuse the incident's order/transaction IDs; wording is usually different.
        if cust and rng.random() < 0.22:
            for _ in range(rng.choice([1, 1, 2])):
                other_source = rng.choice([s for s in SOURCES if s != source])
                dup_ts = min(ts + timedelta(hours=rng.uniform(1, 72)), END - timedelta(minutes=rng.randint(1, 60)))
                dup_template, _ = rng.choice(spec["templates"])
                dup_text = fill(dup_template, ids)
                if rng.random() < 0.5:
                    dup_text = rng.choice(["Second time contacting you about this. ", "Following up on my earlier message: ",
                                           "Still not resolved! "]) + dup_text
                emit(add_noise(rng, dup_text, cust), cust, other_source, dup_ts, family, severity, key, ref_dup=True)

        # Recurrence: some heavy customers hit the same problem again weeks later (a NEW incident)
        if cust in heavy and rng.random() < 0.25:
            incident_no += 1
            ids2 = make_ids(rng)
            t2, s2 = rng.choice(spec["templates"])
            later = ts - timedelta(days=rng.uniform(15, 30))
            if later > END - timedelta(days=DAYS):
                emit(add_noise(rng, fill(t2, ids2), cust), cust, rng.choice(SOURCES), later, family, SEV[s2],
                     f"INC-{incident_no:04d}")

    # Isolated, one-off complaints
    for text, sev in ISOLATED:
        incident_no += 1
        cust = rng.choice(customers) if rng.random() < 0.8 else None
        emit(text, cust, rng.choice(SOURCES), sample_time(rng, "flat"), "isolated", SEV[sev], f"INC-{incident_no:04d}")

    # Shuffle into time order to look like an export, keeping truth aligned
    order = sorted(range(len(rows)), key=lambda i: rows[i]["timestamp"].replace("/", "-").replace("T", " "))
    rows = [rows[i] for i in order]
    truth = [truth[i] for i in order]

    # A handful of invalid rows to exercise row-level validation
    bad_rows = [
        {"complaint_text": "", "customer_id": "CUST-1001", "source": "email", "timestamp": "2026-10-20T10:00:00Z"},
        {"complaint_text": "My order never arrived and nobody replies.", "customer_id": "CUST-1002", "source": "fax",
         "timestamp": "2026-10-21T10:00:00Z"},
        {"complaint_text": "App crashed again when paying.", "customer_id": "CUST-1003", "source": "chat",
         "timestamp": "yesterday-ish"},
    ]
    for b in bad_rows:
        pos = rng.randint(0, len(rows))
        full = {k: "" for k in rows[0]}
        full.update(b)
        rows.insert(pos, full)
        truth.insert(pos, {"issue_family": "INVALID_ROW", "severity": "", "incident_key": "", "customer_id": b["customer_id"],
                           "is_duplicate_report": 0})

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "sample_complaints.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(DATA_DIR / "ground_truth.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["row_number", "issue_family", "severity", "incident_key", "customer_id",
                                           "is_duplicate_report"])
        w.writeheader()
        for i, t in enumerate(truth, start=1):
            w.writerow({"row_number": i, **t})

    # Population denominators (illustrative customer base sizes per segment)
    pops = [("country", "India", 52000), ("country", "United States", 38000), ("country", "United Kingdom", 17000),
            ("country", "Canada", 11000), ("age_group", "18-24", 26000), ("age_group", "25-34", 39000),
            ("age_group", "35-44", 27000), ("age_group", "45-54", 16000), ("age_group", "55+", 10000),
            ("student_status", "Student", 24000), ("student_status", "Non-student", 94000),
            ("income_bracket", "<25k", 33000), ("income_bracket", "25k-50k", 36000),
            ("income_bracket", "50k-100k", 32000), ("income_bracket", "100k+", 17000)]
    with open(DATA_DIR / "sample_population.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["dimension", "value", "population"])
        w.writerows(pops)
    n_dup = sum(t["is_duplicate_report"] for t in truth)
    print(f"Wrote {len(rows)} rows ({n_dup} duplicate reports, {len(bad_rows)} invalid) to {DATA_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    generate(args.seed)
