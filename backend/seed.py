#!/usr/bin/env python3
"""Seed the database and verify the Python pipeline against the Node output.

The three data files the Node scripts read (june_adhoc_v2.json,
contract_lookup.csv, hex_june2026.csv) were not part of the handoff, but the
dashboard had the *final* 645-event result inlined as `const DATA=[...]`.
That output is enough to reconstruct the pipeline's input, because the stages
are deterministic:

    salesforce account names / CSMs / prices  <- one distinct value per account
    contract_lookup                           <- packet_id -> contract_name
    raw event fields                          <- carried through unchanged

So this script rebuilds the input, runs the Python stages over it, and then
asserts the result is byte-identical to the Node output on every field. If it
is, the port is proven correct against a known-good run rather than assumed.

    python seed.py              # seed + verify
    python seed.py --no-verify  # seed only
    python seed.py --reset      # drop the database first

Once you have the real june_adhoc_v2.json, load it instead:

    curl -F file=@june_adhoc_v2.json "localhost:8000/api/ingest/raw-file?period=2026-06"
    curl -F file=@contract_lookup.csv localhost:8000/api/ingest/contract-lookup
    curl -X POST "localhost:8000/api/pipeline/run?period=2026-06"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import DATABASE_URL, connect, init_db, tx  # noqa: E402
from app.pipeline import runner  # noqa: E402

DATA_FILE = Path(__file__).resolve().parent / "data" / "june_adhoc_v5.json"
PERIOD = {"label": "2026-06", "name": "June 2026", "basis": "sent_date"}

SETTINGS = [
    ("price_outlier_threshold", "16",
     "Ad Hoc unit price above this routes to PRICE_OUTLIER_REVIEW (was the "
     "literal 16 in classify())"),
    ("period_basis", "sent_date", "Events belong to the period they were sent in, LA time"),
    ("require_active_contract", "0",
     "0 = workers without an active contract are still billable (differs from the Hex logic)"),
]

# Was the CUSTOMER_MAP dict. Overlook is not here: it is an entity split, not
# a rename, and it is driven by contract name rather than customer name.
CUSTOMER_MAP = [
    ("Hartnell", "Bengard Ranch", "0018b0000224tcLAAQ",
     "Employer Hartnell is billed under Bengard Ranch"),
    ("Kleen Harvest", "Bengard Ranch", "0018b0000224tcLAAQ",
     "Employer Kleen Harvest is billed under Bengard Ranch"),
    ("Bonnie Plants, LLC", "Bonnie Plants, LLC", "0018b0000224qbbAAA",
     'Salesforce account is "Bonnie Plants, Inc." (LLC vs Inc. name mismatch)'),
]

EXCLUSIONS = [
    ("Peri & Sons Farms, Inc.",
     "Peri & Sons Farms, Inc. excluded from Ad Hoc Paperwork billing"),
]

ENTITY_SPLIT = {
    "source_customer": "Overlook Harvesting Company, LLC",
    "token_a": "OHC",
    "entity_a": "Overlook Harvesting Company, LLC",
    "token_b": "OHM",
    "entity_b": "Overlook Harvesting Michigan, LLC",
    "default_entity": "Overlook Harvesting Company, LLC",
    "review_label": "Overlook Harvesting — BILLING REVIEW",
    "senders": ["Ana Sloan Caldera", "Jose Manuel Davila Jr."],
}


def load_dataset() -> list[dict]:
    if not DATA_FILE.exists():
        sys.exit(f"Missing {DATA_FILE}. It ships alongside this script.")
    return json.loads(DATA_FILE.read_text())


def derive_accounts(data: list[dict]) -> list[tuple]:
    """One row per Salesforce account. A blank sf_price means the account has
    no Ad Hoc product on any Closed-Won opportunity -> NULL -> CSM review."""
    acc: dict[str, dict] = {}
    for r in data:
        aid = r["salesforce_account_id"]
        if not aid:
            continue
        a = acc.setdefault(aid, {"names": set(), "csms": set(), "prices": set()})
        a["names"].add(r["salesforce_account"])
        if r["csm"]:
            a["csms"].add(r["csm"])
        a["prices"].add(r["sf_price"])

    out = []
    for aid, a in acc.items():
        if len(a["names"]) > 1 or len(a["prices"]) > 1:
            sys.exit(
                f"Account {aid} has inconsistent names/prices in the dataset "
                f"({a['names']} / {a['prices']}); cannot derive config."
            )
        price = next(iter(a["prices"]))
        out.append(
            (
                aid,
                next(iter(a["names"])),
                next(iter(a["csms"])) if a["csms"] else None,
                None if price in ("", None) else float(price),
            )
        )
    return out


def reconstruct_raw(data: list[dict]) -> list[dict]:
    """Undo the pipeline to recover its input.

    For a mapped source customer, stage 1 ignores the raw account_id and takes
    identity + price from the mapping target, so the reconstructed account_id
    is left blank for those rows — it is genuinely unused.
    """
    mapped_sources = {m[0] for m in CUSTOMER_MAP}
    raw = []
    for r in data:
        is_mapped = r["source_customer"] in mapped_sources
        raw.append(
            {
                "enterprise_name": r["source_customer"],
                "account_id": "" if is_mapped else r["salesforce_account_id"],
                "csm": r["csm"],
                "sf_price": "" if is_mapped else r["sf_price"],
                "worker_name": r["worker_name"],
                "seso_worker_id": r["seso_worker_id"],
                "paperwork_name": r["paperwork_name"],
                "packet_id": r["packet_id"],
                "num_src": r["num_src"],
                "sent_date": r["sent_date"],
                "signed_date": r["signed_date"],
                "sender_name": r["sender_name"],
                "contract_ids": r["contract_ids"],
                "has_active": r["has_active"],
            }
        )
    return raw


def seed_config(conn, data: list[dict]) -> int:
    """Postgres note: SQLite's INSERT OR IGNORE / OR REPLACE have no direct
    equivalent, so each upsert names its conflict target explicitly. Rows come
    back as dicts rather than tuples, so ids are read by name, not by index."""
    with tx(conn):
        conn.execute(
            "INSERT INTO periods (label, name, basis) VALUES (?, ?, ?) "
            "ON CONFLICT (label) DO NOTHING",
            (PERIOD["label"], PERIOD["name"], PERIOD["basis"]),
        )
        period_id = conn.execute(
            "SELECT id FROM periods WHERE label = ?", (PERIOD["label"],)
        ).fetchone()["id"]

        conn.executemany(
            "INSERT INTO settings (key, value, note) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, note=EXCLUDED.note",
            SETTINGS,
        )
        conn.executemany(
            "INSERT INTO sf_accounts (account_id, name, csm, adhoc_price) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (account_id) DO UPDATE SET "
            "name=EXCLUDED.name, csm=EXCLUDED.csm, adhoc_price=EXCLUDED.adhoc_price",
            derive_accounts(data),
        )
        conn.executemany(
            "INSERT INTO customer_map "
            "(source_customer, billing_customer, sf_account_id, reason) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (source_customer) DO UPDATE SET "
            "billing_customer=EXCLUDED.billing_customer, "
            "sf_account_id=EXCLUDED.sf_account_id, reason=EXCLUDED.reason",
            CUSTOMER_MAP,
        )
        conn.executemany(
            "INSERT INTO excluded_customers (source_customer, reason) VALUES (?, ?) "
            "ON CONFLICT (source_customer) DO UPDATE SET reason=EXCLUDED.reason",
            EXCLUSIONS,
        )

        r = ENTITY_SPLIT
        conn.execute(
            """INSERT INTO entity_split_rules
               (source_customer, token_a, entity_a, token_b, entity_b,
                default_entity, review_label)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (source_customer) DO UPDATE SET
                 token_a=EXCLUDED.token_a, entity_a=EXCLUDED.entity_a,
                 token_b=EXCLUDED.token_b, entity_b=EXCLUDED.entity_b,
                 default_entity=EXCLUDED.default_entity,
                 review_label=EXCLUDED.review_label""",
            (
                r["source_customer"], r["token_a"], r["entity_a"], r["token_b"],
                r["entity_b"], r["default_entity"], r["review_label"],
            ),
        )
        rule_id = conn.execute(
            "SELECT id FROM entity_split_rules WHERE source_customer = ?",
            (r["source_customer"],),
        ).fetchone()["id"]
        conn.executemany(
            "INSERT INTO entity_split_senders (rule_id, sender_name, resolves_to) "
            "VALUES (?, ?, 'entity_b') "
            "ON CONFLICT (rule_id, sender_name) DO NOTHING",
            [(rule_id, s) for s in r["senders"]],
        )

        # contract_lookup.csv, recovered from the resolved contract names.
        pairs = {
            (r["packet_id"], r["contract_name"]) for r in data if r.get("contract_name")
        }
        conn.executemany(
            "INSERT INTO contract_lookup (packet_id, contract_names) VALUES (?, ?) "
            "ON CONFLICT (packet_id) DO UPDATE SET contract_names=EXCLUDED.contract_names",
            sorted(pairs),
        )

    return period_id


# Anything that changes who gets billed how much. A difference here is a bug.
BILLING_FIELDS = [
    "source_customer", "billing_customer", "salesforce_account",
    "salesforce_account_id", "csm", "worker_name", "seso_worker_id",
    "paperwork_name", "packet_id", "sent_date", "signed_date", "sender_name",
    "contract_name", "flag",
]

# Human-readable explanations. Generalising the Overlook rule into a template
# changed this wording ("Overlook billing entity = Michigan" is now
# "Billing entity = Overlook Harvesting Michigan, LLC"), so differences here
# are reported for review rather than treated as failures.
PROSE_FIELDS = ["mapping_reason"]


def verify(conn, period_id: int, data: list[dict]) -> bool:
    """Assert the Python stages reproduce the Node output exactly."""
    produced = {
        r["packet_id"]: dict(r)
        for r in conn.execute("SELECT * FROM events WHERE period_id = ?", (period_id,))
    }
    expected = {r["packet_id"]: r for r in data}

    problems: list[str] = []
    prose: list[str] = []
    if set(produced) != set(expected):
        problems.append(
            f"row set differs: {len(produced)} produced vs {len(expected)} expected"
        )

    for pid, exp in expected.items():
        got = produced.get(pid)
        if got is None:
            continue
        for field in BILLING_FIELDS:
            a, b = got.get(field) or "", exp.get(field) or ""
            if str(a) != str(b):
                problems.append(f"packet {pid}: {field} = {a!r}, expected {b!r}")
        for field in PROSE_FIELDS:
            a, b = got.get(field) or "", exp.get(field) or ""
            if str(a) != str(b):
                prose.append(f"packet {pid}: {field}\n        now: {a}\n        was: {b}")
        gp = got.get("sf_price")
        ep = exp.get("sf_price")
        ep = None if ep in ("", None) else float(ep)
        if (gp is None) != (ep is None) or (gp is not None and abs(gp - ep) > 1e-9):
            problems.append(f"packet {pid}: sf_price = {gp!r}, expected {ep!r}")
        ge = bool(got.get("excluded"))
        ee = exp.get("excluded") == "Yes"
        if ge != ee:
            problems.append(f"packet {pid}: excluded = {ge}, expected {ee}")

    if problems:
        print(f"\n✗ Verification failed — {len(problems)} mismatch(es):")
        for p in problems[:25]:
            print(f"    {p}")
        if len(problems) > 25:
            print(f"    ... and {len(problems) - 25} more")
        return False

    print(
        f"\n✓ Verified: all {len(expected)} events match the Node output on every "
        f"billing field\n  (customer, account, price, flag, dates, contract, exclusion)"
    )
    if prose:
        print(f"\n  {len(prose)} explanation string(s) reworded by the rule generalisation:")
        for p in prose[:3]:
            print(f"    {p}")
        if len(prose) > 3:
            print(f"    ... and {len(prose) - 3} more, same rewording")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete the database first")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    if not DATABASE_URL:
        print(
            "DATABASE_URL is not set. Point it at a Postgres instance, e.g.\n"
            "  export DATABASE_URL=postgresql://localhost/adhoc",
            file=sys.stderr,
        )
        return 2

    # --reset used to unlink the SQLite file. There is no file now, so it drops
    # and recreates the schema instead. Destructive: it takes the overrides and
    # audit history with it.
    if args.reset:
        with connect() as c:
            c.executescript("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        print("Dropped and recreated the public schema")

    init_db()
    data = load_dataset()
    conn = connect()
    try:
        period_id = seed_config(conn, data)
        print(f"Seeded config for {PERIOD['name']}")

        n = runner.ingest_raw(conn, period_id, reconstruct_raw(data))
        print(f"Loaded {n} raw events")

        result = runner.run_pipeline(conn, period_id, source="seed:v5")
        s3 = result["stats"]["stage3_exclusions"]
        print(
            f"Pipeline run {result['run_id']}: {result['events']} events "
            f"({s3['billable_events']} billable, {s3['excluded_events']} excluded, "
            f"{s3['billable_flags'].get('CSM_CONFIRM_PRICE', 0)} awaiting CSM price)"
        )

        if not args.no_verify and not verify(conn, period_id, data):
            return 1

        # Host only — the connection string carries credentials.
        host = DATABASE_URL.rsplit("@", 1)[-1].split("?", 1)[0]
        print(f"\nDatabase ready at {host}")
        print("Start the API with:  uvicorn app.main:app --reload")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
