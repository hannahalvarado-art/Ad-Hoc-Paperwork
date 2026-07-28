"""The monthly review notification.

METHOD: a Slack app with a **bot token**, calling chat.postMessage. Not an
incoming webhook. Both can render a real user-group mention — the syntax
`<!subteam^S0123|@csms>` pings the group either way — but a webhook is nailed to
one channel, returns nothing useful, and cannot look anything up. The bot token
lets the app resolve the @csms group and Hannah's user id from configuration,
post to a different channel in development, and keep the returned `ts` as
evidence the message actually landed.

DEVELOPMENT SAFETY. `SLACK_MODE` has three values and defaults to the safe one:

    dry_run  (default) render the message, store it, call Slack not at all.
             The exact text comes back in the API response so it can be read in
             the UI. Nothing can be notified because nothing is sent.
    dev      post for real, but to SLACK_DEV_CHANNEL_ID, and with every mention
             defused into plain text — `@csms` instead of `<!subteam^...>`. The
             message looks right and pings nobody.
    live     the real thing. Refuses to run unless SLACK_ALLOW_LIVE=1 is also
             set, so promoting an environment takes two deliberate changes
             rather than one typo.

DUPLICATE SUPPRESSION. The scheduled review message is written to
`notifications` under kind='review', which carries a partial unique index on
(period_id, kind) for sent rows. A retrying cron job hits that index and is
told it already sent. A manual resend is kind='review_resend' and is
deliberately unconstrained.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .db import Conn, one, rows
from .periods import public as period_public
from .services import summaries

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
SLACK_DEV_CHANNEL_ID = os.environ.get("SLACK_DEV_CHANNEL_ID", "")
SLACK_CSM_GROUP_ID = os.environ.get("SLACK_CSM_GROUP_ID", "")
SLACK_NOTIFY_USER_ID = os.environ.get("SLACK_NOTIFY_USER_ID", "")
SLACK_MODE = os.environ.get("SLACK_MODE", "dry_run").lower()
SLACK_ALLOW_LIVE = os.environ.get("SLACK_ALLOW_LIVE") == "1"
APP_URL = os.environ.get("ADHOC_APP_URL", "http://localhost:5173")

POST_MESSAGE = "https://slack.com/api/chat.postMessage"


class SlackError(RuntimeError):
    pass


def effective_mode() -> str:
    """`live` degrades to `dev` without the second flag, and is reported as such."""
    if SLACK_MODE == "live" and not SLACK_ALLOW_LIVE:
        return "dev"
    return SLACK_MODE if SLACK_MODE in ("dry_run", "dev", "live") else "dry_run"


def _channel(mode: str) -> str:
    if mode == "live":
        return SLACK_CHANNEL_ID
    return SLACK_DEV_CHANNEL_ID or SLACK_CHANNEL_ID


def _mentions(mode: str) -> tuple[str, str]:
    """Real mentions only in live mode; inert text everywhere else."""
    if mode != "live":
        return "@Hannah", "@csms"
    user = f"<@{SLACK_NOTIFY_USER_ID}>" if SLACK_NOTIFY_USER_ID else "@Hannah"
    group = f"<!subteam^{SLACK_CSM_GROUP_ID}>" if SLACK_CSM_GROUP_ID else "@csms"
    return user, group


def _money(v: float | None) -> str:
    return f"${(v or 0):,.2f}"


def compose(conn: Conn, period_id: int, mode: str | None = None) -> str:
    """Build the review message. Pure text, so it can be shown before sending."""
    mode = mode or effective_mode()
    period = one(conn, "SELECT * FROM periods WHERE id = ?", (period_id,))
    if not period:
        raise SlackError(f"No period {period_id}")

    customers = summaries.for_period(conn, period)
    t = summaries.totals(customers)
    user, group = _mentions(mode)
    link = f"{APP_URL.rstrip('/')}/?period={period['label']}"

    lines = [
        f"*Ad Hoc Paperwork — {period['name']} billing is ready for review*",
        "",
        f"{t['total_billable_packets']:,} unique billable packets",
        f"{_money(t['expected_amount'])} expected billing currently known",
        f"{t['blocked_by_pricing']} customer(s) need pricing confirmation",
        f"{t['customers_not_yet_approved']} customer(s) still need Good to Bill approval",
        f"{t['blocked_by_other_exceptions']} other billing exception(s)",
        "",
        f"{user} {group} — please review the billing period here:",
        link,
    ]
    if mode != "live":
        lines.insert(0, f"_[{mode.upper()} — mentions disabled, not a production notification]_")
    return "\n".join(lines)


def send_review_notification(
    conn: Conn,
    period_id: int,
    *,
    run_id: int | None = None,
    actor: str = "system:cron",
    kind: str = "review",
) -> dict:
    """Send (or simulate) the review message and record the attempt.

    Never raises: the caller is a billing run that has already succeeded, and a
    Slack outage must not turn a good month into a failed one. Problems come
    back in the returned dict and are stored in `notifications`.
    """
    mode = effective_mode()

    if kind == "review" and already_sent(conn, period_id):
        return _log(
            conn, period_id, kind, mode, "skipped", run_id, actor,
            message=None, error="A review notification was already sent for this period.",
        )

    try:
        text = compose(conn, period_id, mode)
    except Exception as exc:
        return _log(conn, period_id, kind, mode, "failed", run_id, actor, error=str(exc))

    if mode == "dry_run":
        return _log(
            conn, period_id, kind, mode, "sent", run_id, actor, message=text,
            note="Rendered only — Slack was not called.",
        )

    channel = _channel(mode)
    if not SLACK_BOT_TOKEN or not channel:
        return _log(
            conn, period_id, kind, mode, "failed", run_id, actor, message=text,
            error=(
                "Slack is not configured. Set SLACK_BOT_TOKEN and "
                + ("SLACK_CHANNEL_ID." if mode == "live" else "SLACK_DEV_CHANNEL_ID.")
            ),
        )

    try:
        ts = _post(channel, text)
    except Exception as exc:
        return _log(conn, period_id, kind, mode, "failed", run_id, actor, message=text, error=str(exc))

    return _log(conn, period_id, kind, mode, "sent", run_id, actor, message=text, slack_ts=ts)


def _post(channel: str, text: str) -> str:
    payload = json.dumps(
        {
            "channel": channel,
            "text": text,
            # Slack only expands <!subteam^...> and <@...> when the message is
            # not sent as a raw literal; mrkdwn is on by default but stated
            # explicitly so a future edit does not quietly disable mentions.
            "mrkdwn": True,
            "unfurl_links": False,
        }
    ).encode()
    req = urllib.request.Request(
        POST_MESSAGE, data=payload,
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed host
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise SlackError(f"Could not reach Slack: {exc}") from exc

    if not body.get("ok"):
        # Slack answers 200 with ok:false, so the HTTP status is not the check.
        raise SlackError(f"Slack rejected the message: {body.get('error', 'unknown error')}")
    return str(body.get("ts", ""))


def _log(
    conn: Conn,
    period_id: int,
    kind: str,
    mode: str,
    status: str,
    run_id: int | None,
    actor: str,
    message: str | None = None,
    error: str | None = None,
    slack_ts: str | None = None,
    note: str | None = None,
) -> dict:
    try:
        conn.execute(
            "INSERT INTO notifications "
            "(period_id, kind, channel, mode, status, slack_ts, message, error, actor, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (period_id, kind, _channel(mode), mode, status, slack_ts, message, error, actor, run_id),
        )
    except Exception as exc:
        # The unique index rejecting a second 'review' send is the mechanism
        # working, not a failure to report.
        return {
            "status": "skipped", "mode": mode, "kind": kind,
            "error": "A review notification was already recorded for this period.",
            "detail": str(exc), "message": message,
        }
    if run_id is not None:
        conn.execute(
            "UPDATE pipeline_runs SET notification_status = ? WHERE id = ?", (status, run_id)
        )
    return {
        "status": status, "mode": mode, "kind": kind, "channel": _channel(mode),
        "message": message, "error": error, "slack_ts": slack_ts, "note": note,
    }


def already_sent(conn: Conn, period_id: int) -> bool:
    return bool(
        one(
            conn,
            "SELECT 1 FROM notifications WHERE period_id = ? AND kind = 'review' "
            "AND status = 'sent' LIMIT 1",
            (period_id,),
        )
    )


def history(conn: Conn, period_id: int) -> list[dict]:
    return rows(
        conn,
        "SELECT id, kind, channel, mode, status, slack_ts, error, actor, created_at "
        "FROM notifications WHERE period_id = ? ORDER BY id DESC",
        (period_id,),
    )


def config_status() -> dict:
    """What is configured, for /api/health and the accounting panel."""
    mode = effective_mode()
    return {
        "mode": mode,
        "requested_mode": SLACK_MODE,
        "downgraded": SLACK_MODE == "live" and not SLACK_ALLOW_LIVE,
        "bot_token": bool(SLACK_BOT_TOKEN),
        "channel": bool(SLACK_CHANNEL_ID),
        "dev_channel": bool(SLACK_DEV_CHANNEL_ID),
        "csm_group": bool(SLACK_CSM_GROUP_ID),
        "notify_user": bool(SLACK_NOTIFY_USER_ID),
        "will_mention_real_users": mode == "live",
    }
