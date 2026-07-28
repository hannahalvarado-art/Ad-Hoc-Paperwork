"""Stage 2 — contract names and legal-entity split.

Port of 02_contract_names_and_overlook_split.js.

Two changes from the original, both deliberate:

  * contract_lookup.csv is now a table, so the hand-rolled parseCSV() is gone.
  * the Overlook-specific constants (OHC/OHM tokens, the two entity names,
    the two senders that resolve a both-entity duplicate) are rows in
    entity_split_rules / entity_split_senders. The flag is therefore
    ENTITY_BILLING_REVIEW rather than OVERLOOK_BILLING_REVIEW — one customer's
    name no longer appears in the enum. Legacy flag values are translated on
    read, so existing exports still resolve.

The token match remains case-insensitive substring, as in the JS regex /OHM/i.
"""

from __future__ import annotations

import re

from ..db import rows
from ..pricing import FLAG_ENTITY_REVIEW


def load_rules(conn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rule in rows(conn, "SELECT * FROM entity_split_rules WHERE active = 1"):
        senders = rows(
            conn,
            "SELECT sender_name, resolves_to FROM entity_split_senders WHERE rule_id = ?",
            (rule["id"],),
        )
        rule["senders"] = {s["sender_name"].strip(): s["resolves_to"] for s in senders}
        out[rule["source_customer"]] = rule
    return out


def load_contract_names(conn) -> dict[str, str]:
    return {
        r["packet_id"]: r["contract_names"]
        for r in rows(conn, "SELECT packet_id, contract_names FROM contract_lookup")
    }


def run(conn, events: list[dict]) -> tuple[list[dict], dict]:
    names = load_contract_names(conn)
    rules = load_rules(conn)
    tally = {"entity_a": 0, "entity_b": 0, "resolved_by_sender": 0, "review": 0}

    out: list[dict] = []
    for r in events:
        e = dict(r)
        # contract_lookup wins where it has an entry, as it always did. The
        # fallback is for rows that arrive from the warehouse already carrying a
        # contract name: an unconditional overwrite would blank those, and a
        # blank contract name silently disables the entity split that depends
        # on it.
        e["contract_name"] = names.get(r.get("packet_id") or "") or r.get("contract_name") or ""

        rule = rules.get(r["source_customer"])
        if rule:
            e.update(_split(e, rule, tally))
        out.append(e)

    return out, tally


def _split(e: dict, rule: dict, tally: dict) -> dict:
    name = e.get("contract_name") or ""
    has_a = bool(re.search(re.escape(rule["token_a"]), name, re.I))
    has_b = bool(re.search(re.escape(rule["token_b"]), name, re.I))
    patch: dict = {"customer_mapping_applied": 1}

    if has_a and has_b:
        # Contract spans both entities. Only a known sender resolves it.
        sender = (e.get("sender_name") or "").strip()
        resolves = rule["senders"].get(sender)
        if resolves:
            entity = rule["entity_b"] if resolves == "entity_b" else rule["entity_a"]
            patch["billing_customer"] = entity
            patch["mapping_reason"] = (
                f"{rule['token_a']}/{rule['token_b']} duplicate resolved by "
                f"Sent By ({sender}) → {entity}"
            )
            tally["entity_b" if resolves == "entity_b" else "entity_a"] += 1
            tally["resolved_by_sender"] += 1
        else:
            patch["billing_customer"] = rule["review_label"]
            patch["flag"] = FLAG_ENTITY_REVIEW
            patch["mapping_reason"] = (
                f"{rule['token_a']}/{rule['token_b']} duplicate, sender not in rule "
                f"({sender or '—'}) → manual review"
            )
            tally["review"] += 1
    elif has_b:
        patch["billing_customer"] = rule["entity_b"]
        patch["mapping_reason"] = (
            f"Billing entity = {rule['entity_b']} "
            f"(contract name contains {rule['token_b']})"
        )
        tally["entity_b"] += 1
    elif has_a:
        patch["billing_customer"] = rule["entity_a"]
        patch["mapping_reason"] = (
            f"Billing entity = {rule['entity_a']} "
            f"(contract name contains {rule['token_a']})"
        )
        tally["entity_a"] += 1
    else:
        # No token at all -> default entity, and the mapping flag stays off
        # so the UI doesn't claim a mapping was applied.
        patch["billing_customer"] = rule["default_entity"]
        patch["customer_mapping_applied"] = 0
        patch["mapping_reason"] = ""
        tally["entity_a"] += 1

    return patch


def stats(out: list[dict], tally: dict) -> dict:
    by: dict[str, int] = {}
    for r in out:
        by[r["flag"]] = by.get(r["flag"], 0) + 1
    return {
        "events": len(out),
        "flags": by,
        "billing_customers": len({r["billing_customer"] for r in out}),
        "with_contract_name": sum(1 for r in out if r.get("contract_name")),
        "entity_split": tally,
    }
