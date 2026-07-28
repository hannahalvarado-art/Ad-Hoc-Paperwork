"""The monthly billing workflow.

One function, `run_period`, is the whole thing. The scheduled job and the
"Run / Re-run" button in the UI both call it with different arguments; neither
holds any business logic of its own. That is deliberate — the point of testing
July by hand is that the thing being tested is the thing that will run on the
2nd, not a rehearsal of it.

The sequence:

     1. locate or create the period for the target month
     2. status -> PROCESSING
     3. pull prior-month usage by SENT date
     4. apply the validated rules (dedupe -> mapping -> contracts -> exclusions)
     5. merge into usage_events (idempotent)
     6. Salesforce pricing, then persistent CSM overrides
     7. recompute customer summaries
     8. count unresolved pricing and exceptions
     9. status -> IN_REVIEW, or FAILED
    10. record run metrics
    11. notify Slack, but only on success

A CLOSED period is refused before step 3, so a rerun aimed at a closed month
costs nothing and changes nothing.
"""

from __future__ import annotations

import json
import traceback
from typing import Any

from .. import audit, periods, slack
from ..db import Conn, one, rows, scalar, tx
from ..periods import PeriodClosed
from ..pipeline import runner, stage0_dedupe, stage1_mapping, stage2_contracts, stage3_exclusions
from ..sources import SourceUnavailable, get_source
from . import summaries

# Fields copied from a pipeline event onto its usage_events row.
_CARRY = (
    "worker_name", "sent_date", "signed_date", "sender_name", "source_customer",
    "billing_customer", "salesforce_account", "salesforce_account_id",
    "contract_name", "contract_ids", "source_record_ids", "duplicate_group_key",
    "duplicate_contracts", "csm", "customer_mapping_applied", "mapping_reason",
    "sf_price", "flag", "has_active", "excluded", "exclusion_reason",
)


class RunFailed(RuntimeError):
    pass


def run_period(
    conn: Conn,
    year: int,
    month: int,
    *,
    actor: str = "system:cron",
    run_type: str = "scheduled",
    source_name: str | None = None,
    billing_type: str = periods.BILLING_TYPE_ADHOC,
    notify: bool = True,
    refresh_usage: bool = True,
) -> dict[str, Any]:
    """Process one calendar month end to end. Returns a run summary."""
    period, created = periods.get_or_create_period(
        conn, year, month, billing_type, created_by=actor, run_source=run_type
    )
    # Refuse before any expensive work: a run that will be rejected should not
    # first spend a minute pulling from the warehouse.
    periods.assert_open(period, "rerun the billing period")

    period_id = period["id"]
    if created:
        audit.record(
            conn, audit.PERIOD_CREATED, actor, period_id=period_id,
            billing_type=billing_type, new_value=period["label"],
            source=run_type, note=f"Created for {period['name']}",
        )

    run_id = _open_run(conn, period_id, run_type, actor)
    periods.set_status(conn, period_id, periods.PROCESSING, actor)

    try:
        pulled = 0
        if refresh_usage:
            pulled = _pull_usage(conn, period, source_name)

        raw = rows(conn, "SELECT * FROM raw_events WHERE period_id = ?", (period_id,))
        if not raw:
            raise RunFailed(
                f"No usage rows for {period['name']}. The source returned nothing and "
                f"none were previously loaded, so there is nothing to bill."
            )

        # --- the validated rules, unchanged ------------------------------
        s0, dedupe_tally = stage0_dedupe.run(conn, raw)
        s1 = stage1_mapping.run(conn, s0)
        # stage 1 builds fresh dicts; carry the dedupe provenance across.
        _restore_provenance(s0, s1)
        s2, entity_tally = stage2_contracts.run(conn, s1)
        s3 = stage3_exclusions.run(conn, s2)

        stats = {
            "stage0_dedupe": stage0_dedupe.stats(s0, dedupe_tally),
            "stage1_mapping": stage1_mapping.stats(s1),
            "stage2_contracts": stage2_contracts.stats(s2, entity_tally),
            "stage3_exclusions": stage3_exclusions.stats(s3),
        }

        with tx(conn):
            # `events` stays the current-run snapshot the existing dashboard
            # reads; usage_events is the durable, merged layer beside it.
            _replace_events(conn, period_id, run_id, s3)
            merge = _merge_usage(conn, period_id, run_id, s3, billing_type)

        totals = summaries.rebuild(conn, period_id, actor=actor)

        _close_run(
            conn, run_id, "ok",
            stats=stats,
            rows_pulled=pulled or len(raw),
            rows_excluded=stats["stage3_exclusions"]["excluded_events"],
            duplicates_removed=dedupe_tally["rows_removed"],
            final_event_count=len(s3),
            customer_count=totals["customers"],
            unresolved_pricing=totals["blocked_by_pricing"],
            unresolved_exceptions=totals["unresolved_exceptions"],
            expected_amount=totals["expected_amount"],
            **merge,
        )
        periods.set_status(conn, period_id, periods.IN_REVIEW, actor)
        audit.record(
            conn, audit.PERIOD_RERUN, actor, period_id=period_id,
            billing_type=billing_type, entity="pipeline_runs", entity_id=run_id,
            new_value={"events": len(s3), "expected": totals["expected_amount"]},
            source=run_type,
        )

        result = {
            "period": periods.public(periods.get_period(conn, year, month, billing_type)),
            "run_id": run_id,
            "status": "ok",
            "events": len(s3),
            "stats": stats,
            "merge": merge,
            "totals": totals,
        }

        if notify:
            # Notification failure must not fail the run: the billing period is
            # correct and in review whether or not Slack accepted the message,
            # and marking it FAILED would hide a good month behind a chat outage.
            result["notification"] = slack.send_review_notification(
                conn, period_id, run_id=run_id, actor=actor, kind="review"
            )
        return result

    except (RunFailed, SourceUnavailable, Exception) as exc:  # noqa: B014 - explicit for readers
        detail = f"{type(exc).__name__}: {exc}"
        _close_run(conn, run_id, "failed", error=detail, notification_status="not_sent")
        periods.set_status(conn, period_id, periods.FAILED, actor)
        audit.record(
            conn, "billing_period_run_failed", actor, period_id=period_id,
            billing_type=billing_type, entity="pipeline_runs", entity_id=run_id,
            new_value=detail, source=run_type,
            note="No review notification sent.",
        )
        print(f"[monthly] {period['label']} FAILED: {detail}\n{traceback.format_exc()}")
        raise RunFailed(detail) from exc


# ------------------------------------------------------------------ usage pull
def _pull_usage(conn: Conn, period: dict, source_name: str | None) -> int:
    """Fetch the month from the source and load it into raw_events.

    The window is on SENT date. `replace=True` is safe and correct here: it
    replaces the *staging* rows for this period, while usage_events — the
    durable layer with the review state on it — is merged rather than replaced.
    """
    source = get_source(source_name)
    records = source.fetch(period["period_start"], period["period_end"])
    if not records:
        # An upload source always returns nothing; that means "use what is
        # already staged", not "the month was empty".
        return 0
    runner.ingest_raw(conn, period["id"], records, replace=True)

    # Contract names arrive on the row from the warehouse. stage 2 reads them
    # from contract_lookup, so seed it here; stage 0 then overwrites the entries
    # for any consolidated packet with the joined name.
    pairs = sorted(
        {
            (str(r["packet_id"]), r["contract_name"])
            for r in records
            if r.get("packet_id") and r.get("contract_name")
        }
    )
    if pairs:
        runner.ingest_contract_lookup(conn, pairs)
    return len(records)


def _restore_provenance(source_rows: list[dict], mapped: list[dict]) -> None:
    """Carry stage 0's duplicate evidence through stage 1.

    stage1_mapping constructs new dicts with an explicit field list rather than
    copying, so anything stage 0 added is dropped unless it is put back. Zipped
    by position because stage 1 preserves order one-for-one.
    """
    for src, dst in zip(source_rows, mapped):
        for field in (
            "source_record_ids", "duplicate_group_key",
            "duplicate_contracts", "duplicate_source_count",
        ):
            if field in src:
                dst[field] = src[field]
        if src.get("contract_name") and not dst.get("contract_name"):
            dst["contract_name"] = src["contract_name"]


def _replace_events(conn: Conn, period_id: int, run_id: int, events: list[dict]) -> None:
    conn.execute("DELETE FROM events WHERE period_id = ?", (period_id,))
    cols = runner.EVENT_COLUMNS
    conn.executemany(
        f"INSERT INTO events ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})",
        [
            tuple(
                period_id if c == "period_id"
                else run_id if c == "run_id"
                else e.get(c)
                for c in cols
            )
            for e in events
        ],
    )


# ------------------------------------------------------------- idempotent merge
def _merge_usage(
    conn: Conn, period_id: int, run_id: int, events: list[dict], billing_type: str
) -> dict[str, int]:
    """Merge this run's events into usage_events without duplicating them.

    Keyed on (period, packet, worker, paperwork) — the unique constraint on the
    table — so a second run of the same month updates in place. Three outcomes:

      added         first time this event has been seen in this period
      updated       seen before; its classification is refreshed
      disqualified  previously seen, absent now

    Disqualified rows are marked, never deleted. A row someone already reviewed
    that quietly vanished would be indistinguishable from one that was never
    there, and the reviewer would have no way to find out what happened to it.
    """
    before = scalar(
        conn,
        "SELECT COUNT(*) FROM usage_events WHERE period_id = ? AND qualification_status = 'QUALIFIED'",
        (period_id,),
    ) or 0

    cols = [
        "period_id", "billing_type", "packet_id", "seso_worker_id", "paperwork_name",
        *_CARRY, "duplicate_source_count", "first_seen_run_id", "last_seen_run_id",
        "qualification_status", "updated_at",
    ]
    # Everything except the natural key and the first-seen marker is refreshed.
    updatable = [
        c for c in cols
        if c not in ("period_id", "packet_id", "seso_worker_id", "paperwork_name", "first_seen_run_id")
    ]

    now = periods.now_utc()

    def row(e: dict) -> tuple:
        values = {
            "period_id": period_id,
            "billing_type": billing_type,
            "packet_id": str(e.get("packet_id") or ""),
            "seso_worker_id": str(e.get("seso_worker_id") or ""),
            "paperwork_name": e.get("paperwork_name") or "",
            "duplicate_source_count": int(e.get("duplicate_source_count") or e.get("num_src") or 1),
            "first_seen_run_id": run_id,
            "last_seen_run_id": run_id,
            "qualification_status": "QUALIFIED",
            "updated_at": now,
            **{c: e.get(c) for c in _CARRY},
        }
        return tuple(values[c] for c in cols)

    conn.executemany(
        f"INSERT INTO usage_events ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT (period_id, packet_id, seso_worker_id, paperwork_name) DO UPDATE SET "
        + ", ".join(f"{c}=EXCLUDED.{c}" for c in updatable)
        # Re-qualify a row that had dropped out and has come back.
        + ", disqualified_at = NULL, disqualified_reason = NULL",
        [row(e) for e in events],
    )

    # Anything this run did not touch no longer qualifies.
    disqualified = conn.execute(
        "UPDATE usage_events SET qualification_status = 'NO_LONGER_QUALIFIES', "
        "disqualified_at = ?, disqualified_reason = ?, updated_at = ? "
        "WHERE period_id = ? AND last_seen_run_id <> ? "
        "AND qualification_status = 'QUALIFIED' RETURNING id",
        (
            now,
            f"Not present in run {run_id}: no longer matches the billing rules "
            f"for this period.",
            now, period_id, run_id,
        ),
    ).fetchall()

    after = scalar(
        conn,
        "SELECT COUNT(*) FROM usage_events WHERE period_id = ? AND qualification_status = 'QUALIFIED'",
        (period_id,),
    ) or 0
    added = max(0, after - (before - len(disqualified)))
    return {
        "events_added": added,
        "events_updated": len(events) - added,
        "events_disqualified": len(disqualified),
    }


# ----------------------------------------------------------------- run records
def _open_run(conn: Conn, period_id: int, run_type: str, actor: str) -> int:
    cur = conn.execute(
        "INSERT INTO pipeline_runs (period_id, source, status, run_type, actor) "
        "VALUES (?, ?, 'running', ?, ?) RETURNING id",
        (period_id, run_type, run_type, actor),
    )
    return cur.fetchone()["id"]


def _close_run(conn: Conn, run_id: int, status: str, stats: dict | None = None, **fields) -> None:
    sets = ["status = ?", "finished_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"]
    params: list[Any] = [status]
    if stats is not None:
        sets.append("stats = ?")
        params.append(json.dumps(stats))
    for key, value in fields.items():
        sets.append(f"{key} = ?")
        params.append(value)
    params.append(run_id)
    conn.execute(f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id = ?", params)


def latest_run(conn: Conn, period_id: int) -> dict | None:
    return one(
        conn,
        "SELECT * FROM pipeline_runs WHERE period_id = ? ORDER BY id DESC LIMIT 1",
        (period_id,),
    )


def run_log(conn: Conn, period_id: int, limit: int = 20) -> list[dict]:
    return rows(
        conn,
        "SELECT * FROM pipeline_runs WHERE period_id = ? ORDER BY id DESC LIMIT ?",
        (period_id, limit),
    )
