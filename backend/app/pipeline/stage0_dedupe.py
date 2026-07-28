"""Stage 0 — consolidate contract-only duplicates.

This collapse used to happen upstream, before the extract the app was handed
(`num_src` arrived already set and the pipeline just carried it). Pulling
directly from the warehouse means the raw rows are no longer pre-deduplicated,
so the rule has to live here.

THE RULE, and what each clause is protecting against:

  * Group on (worker, paperwork type, sent date). The same worker appearing
    several times is **not** in itself a duplicate — they may genuinely have
    signed several things — so the worker alone is never the key.

  * Different paperwork types are separate billable events, which is why
    paperwork_name is part of the key rather than something we look past.

  * A group only collapses when it spans **more than one distinct contract**.
    That is the literal rule: a true duplicate is the same underlying paperwork
    event where the only meaningful difference is the contract. Two packets for
    the same worker, paperwork and day under the *same* contract are two
    events, and collapsing them would quietly undercount. June 2026 contains
    exactly one such pair, and it must stay two rows.

  * Every contract name and id in the group is preserved on the surviving row,
    not just the winner's. Contract Name drives customer-specific billing —
    Overlook's OHC/OHM split reads it — so discarding the others would break
    the rule that depends on them.

That last point is also why the joined contract names are written back to
`contract_lookup` against the surviving packet id: stage 2 reads contract names
from there and already knows what to do when one name contains both an OHC and
an OHM token. Feeding it the joined name means the validated entity-split logic
handles a cross-contract duplicate with no change to stage 2 at all.
"""

from __future__ import annotations

import json

CONTRACT_JOIN = " | "


def _key(r: dict) -> tuple:
    return (
        str(r.get("seso_worker_id") or ""),
        str(r.get("paperwork_name") or ""),
        str(r.get("sent_date") or ""),
    )


def _contract_id(r: dict) -> str:
    return str(r.get("contract_ids") or "").strip()


def _contract_name(r: dict) -> str:
    return str(r.get("contract_name") or "").strip()


def run(conn, raw: list[dict]) -> tuple[list[dict], dict]:
    """Returns (rows, tally). `conn` is used to record joined contract names."""
    groups: dict[tuple, list[dict]] = {}
    for r in raw:
        groups.setdefault(_key(r), []).append(r)

    out: list[dict] = []
    tally = {
        "input_rows": len(raw),
        "groups": len(groups),
        "collapsed_groups": 0,
        "rows_removed": 0,
        "multi_contract_groups": 0,
        "same_contract_kept": 0,
    }
    lookup_rows: list[tuple[str, str]] = []

    for members in groups.values():
        if len(members) == 1:
            out.append(_single(members[0]))
            continue

        distinct_contracts = {_contract_id(m) for m in members if _contract_id(m)}
        distinct_names = {_contract_name(m) for m in members if _contract_name(m)}

        # Same contract (or no contract information at all) -> genuinely
        # separate events. Left alone.
        if len(distinct_contracts) <= 1 and len(distinct_names) <= 1:
            tally["same_contract_kept"] += len(members)
            out.extend(_single(m) for m in members)
            continue

        tally["multi_contract_groups"] += 1
        tally["collapsed_groups"] += 1
        tally["rows_removed"] += len(members) - 1

        # Deterministic winner so a rerun picks the same surviving packet id and
        # the merge in usage_events updates rather than churns. Numeric-aware:
        # packet ids are numeric strings, and plain string ordering would make
        # '1000' sort before '999'.
        winner = min(members, key=lambda m: _sortable(m.get("packet_id")))
        names = sorted(distinct_names)
        ids = sorted(distinct_contracts)
        joined_names = CONTRACT_JOIN.join(names)

        row = _single(winner)
        row.update(
            {
                "num_src": len(members),
                "contract_ids": ",".join(ids),
                "contract_name": joined_names,
                "duplicate_group_key": "|".join(_key(winner)),
                "duplicate_source_count": len(members),
                "duplicate_contracts": json.dumps(names),
                "source_record_ids": json.dumps(
                    sorted((str(m.get("packet_id") or "") for m in members), key=_sortable)
                ),
            }
        )
        out.append(row)

        if joined_names and winner.get("packet_id"):
            lookup_rows.append((str(winner["packet_id"]), joined_names))

    # Stage 2 reads contract names from contract_lookup. Writing the joined name
    # there is what lets the existing OHC/OHM both-token branch see a
    # cross-contract duplicate as one.
    if lookup_rows:
        conn.executemany(
            "INSERT INTO contract_lookup (packet_id, contract_names) VALUES (?, ?) "
            "ON CONFLICT (packet_id) DO UPDATE SET contract_names = EXCLUDED.contract_names",
            lookup_rows,
        )

    tally["output_rows"] = len(out)
    return out, tally


def _sortable(packet_id) -> tuple[int, object]:
    """Numeric packet ids sort numerically; anything else sorts after, by text."""
    s = str(packet_id or "")
    return (0, int(s)) if s.isdigit() else (1, s)


def _single(r: dict) -> dict:
    """A row that survived on its own still records its own provenance."""
    row = dict(r)
    row.setdefault("num_src", 1)
    row["duplicate_source_count"] = int(row.get("num_src") or 1)
    row.setdefault("duplicate_group_key", "|".join(_key(r)))
    name = _contract_name(r)
    row.setdefault("duplicate_contracts", json.dumps([name] if name else []))
    row.setdefault("source_record_ids", json.dumps([str(r.get("packet_id") or "")]))
    return row


def stats(out: list[dict], tally: dict) -> dict:
    return {
        **tally,
        "events": len(out),
        "consolidated_events": sum(1 for r in out if int(r.get("duplicate_source_count") or 1) > 1),
    }
