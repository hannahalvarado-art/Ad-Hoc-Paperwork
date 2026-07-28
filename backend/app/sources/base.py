"""The usage source interface."""

from __future__ import annotations

from typing import Protocol


class SourceUnavailable(RuntimeError):
    """The source is not configured or cannot be reached.

    Separate from "the source returned nothing", which is a legitimate answer
    for a quiet month. Conflating them is how a broken warehouse connection
    turns into a billing period that confidently reports zero usage — so a
    failure raises and a genuinely empty month returns an empty list.
    """


class UsageSource(Protocol):
    """Pull the raw usage rows for one calendar month.

    Returns records in the shape `raw_events` expects — the same shape
    `POST /api/ingest/raw` has always accepted — so the pipeline stages need no
    knowledge of where they came from:

        enterprise_name, account_id, csm, sf_price, worker_name,
        seso_worker_id, paperwork_name, packet_id, num_src, sent_date,
        signed_date, sender_name, contract_ids, contract_name, has_active

    `start` and `end` are inclusive ISO dates, and the window is applied to the
    **sent date**, which is what determines the billing month.
    """

    name: str

    def available(self) -> bool:
        """True if this source could run right now."""
        ...

    def describe(self) -> str:
        """One line for the run log and the UI."""
        ...

    def fetch(self, start: str, end: str) -> list[dict]:
        ...
