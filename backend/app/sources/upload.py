"""Usage from an extract already uploaded into raw_events.

This is the path that existed before the warehouse connection: someone posts a
month's JSON to /api/ingest/raw-file and then runs the pipeline. It is kept for
two reasons — validating a month against a hand-checked extract, and having a
way to run a period at all when the warehouse is unreachable.

It does not fetch anything. `fetch` returning an empty list tells the service
"use whatever is already in raw_events for this period", which is exactly the
old behaviour.
"""

from __future__ import annotations


class UploadSource:
    name = "upload"

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "Uploaded extract (raw_events as already loaded)"

    def fetch(self, start: str, end: str) -> list[dict]:
        return []
