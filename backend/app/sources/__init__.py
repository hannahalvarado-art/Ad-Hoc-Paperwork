"""Where a period's usage comes from.

The billing rules do not care whether the month's packets arrived from the
warehouse or from an uploaded extract, so the source is a small interface and
everything downstream — mapping, entity split, exclusions, pricing — is
unchanged either way.

Two implementations:

    keboola   the Seso Prod Snowflake warehouse. The real source.
    upload    rows already loaded into raw_events by a file upload. The path
              that existed before this, kept because it is how a month gets
              re-validated against a hand-checked extract.

`ADHOC_USAGE_SOURCE` picks the default.
"""

from __future__ import annotations

import os

from .base import SourceUnavailable, UsageSource
from .keboola import KeboolaSource
from .upload import UploadSource

DEFAULT_SOURCE = os.environ.get("ADHOC_USAGE_SOURCE", "keboola")

_SOURCES: dict[str, type[UsageSource]] = {
    "keboola": KeboolaSource,
    "upload": UploadSource,
}


def get_source(name: str | None = None) -> UsageSource:
    key = (name or DEFAULT_SOURCE).lower()
    try:
        return _SOURCES[key]()
    except KeyError as exc:
        raise SourceUnavailable(
            f"Unknown usage source {key!r}. Available: {', '.join(sorted(_SOURCES))}."
        ) from exc


__all__ = ["UsageSource", "SourceUnavailable", "get_source", "DEFAULT_SOURCE"]
