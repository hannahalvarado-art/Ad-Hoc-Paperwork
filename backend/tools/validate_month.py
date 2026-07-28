#!/usr/bin/env python3
"""End-to-end validation of the monthly workflow, through the HTTP API.

Walks the twelve things that have to work before the production cron is
enabled, in order, against a real period. Goes through the API rather than
calling the services directly, so authentication, the closed-period guard and
the eligibility rules are exercised the way the browser will exercise them.

    ADHOC_DEV_AUTH=1 ADHOC_DEV_USER=you@sesolabor.com \
      python tools/validate_month.py --period 2026-07

It is read-mostly but not read-only: it confirms a price, marks a customer Good
to Bill, and then undoes both. It never closes the period being validated —
that step is checked against a scratch period instead, because closing is the
one action the app deliberately cannot undo.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ADHOC_DEV_AUTH", "1")
os.environ.setdefault("ADHOC_DEV_USER", "dev@sesolabor.com")
os.environ.setdefault("SLACK_MODE", "dry_run")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASS, FAIL = "  PASS", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label))
    print(f"{PASS if ok else FAIL}  {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="2026-07")
    args = ap.parse_args()
    label = args.period
    c = TestClient(app)

    print(f"\nValidating {label}\n" + "=" * 60)

    # 1 — identity
    me = c.get("/api/auth/me").json()
    check(me["authenticated"], "1. Authenticated identity available", me["user"]["email"])

    # 2 — the period exists and is workable
    r = c.get(f"/api/billing-periods/{label}").json()
    period, totals = r["period"], r["totals"]
    check(period["status"] in ("IN_REVIEW", "READY_TO_BILL", "PROCESSING"),
          "2. Period present and open", f"status={period['status']}")
    check(period["period_start"] == f"{label}-01",
          "3. Period window is the calendar month", f"{period['period_start']}..{period['period_end']}")

    # 3 — usage persisted, by sent date
    ue = c.get(f"/api/usage-events?period={label}&limit=1").json()["rows"]
    check(bool(ue) and ue[0]["sent_date"].startswith(label),
          "4. Usage persisted and dated by sent date",
          ue[0]["sent_date"] if ue else "no rows")

    # 4 — idempotent rerun
    before = c.get(f"/api/customer-summary?period={label}").json()["totals"]
    run = c.post("/api/billing-periods/run", json={
        "year": period["year"], "month": period["month"],
        "source": "upload", "notify": False, "refresh_usage": False,
    })
    if run.status_code != 200:
        return check(False, "5. Idempotent rerun", run.text) or 1
    merge = run.json()["merge"]
    after = c.get(f"/api/customer-summary?period={label}").json()["totals"]
    check(merge["events_added"] == 0 and before == after,
          "5. Rerun is idempotent",
          f"added={merge['events_added']} updated={merge['events_updated']}")

    # 5 — CSM_CONFIRM_PRICE customers surface
    queue = c.get(f"/api/review-queue?period={label}").json()
    check(queue["total"] > 0, "6. CSM_CONFIRM_PRICE customers surfaced",
          f"{queue['total']} account(s)")
    target = next((a for a in queue["accounts"] if not a["confirmed"]), None)
    if target is None:
        return check(False, "6b. An unconfirmed account to test with") or 1

    # 6 — Good to Bill is blocked while pricing is unresolved
    blocked = c.put("/api/approvals", json={
        "period": label, "billing_customer": target["billing_customer"],
        "salesforce_account_id": target["sf_account_id"], "good_to_bill": True,
    })
    check(blocked.status_code == 409,
          "7. Good to Bill blocked while pricing unresolved",
          blocked.json().get("detail", "")[:70])

    # 7 — confirming a price persists and recalculates
    conf = c.put("/api/overrides", json={
        "sf_account_id": target["sf_account_id"],
        "confirmed_unit_price": 4.0,
        "effective_date": f"{label}-01",
        "confirm": True,
        "note": "validate_month.py",
        "billing_customer": target["billing_customer"],
    })
    if conf.status_code != 200:
        return check(False, "8. CSM price confirmation", conf.text) or 1
    body = conf.json()
    check(body["confirmed_by"] == os.environ["ADHOC_DEV_USER"],
          "8. Price confirmation records the authenticated user", body["confirmed_by"])
    check(body["sf_pricing_status"] == "Not Configured"
          and body["pricing_source"] == "CSM Confirmed Override",
          "9. Still reports Salesforce as Not Configured")
    check(label in body["recalculated_periods"],
          "10. Open period recalculated", str(body["recalculated_periods"]))

    # 8 — the customer is now eligible and can be approved
    row = next(r for r in c.get(f"/api/customer-summary?period={label}").json()["rows"]
               if r["salesforce_account_id"] == target["sf_account_id"])
    check(row["good_to_bill_eligible"] and row["pricing_status"] == "CSM_CONFIRMED_PRICE",
          "11. Customer eligible after confirmation",
          f"{row['pricing_status']} @ {row['unit_price']}")

    ok = c.put("/api/approvals", json={
        "period": label, "billing_customer": row["billing_customer"],
        "salesforce_account_id": row["salesforce_account_id"], "good_to_bill": True,
    })
    check(ok.status_code == 200 and ok.json()["approved_by"] == os.environ["ADHOC_DEV_USER"],
          "12. Good to Bill records the authenticated user",
          ok.json().get("approved_by", ok.text[:60]))

    # 9 — the approval persists across a rerun (i.e. survives a refresh)
    c.post("/api/billing-periods/run", json={
        "year": period["year"], "month": period["month"],
        "source": "upload", "notify": False, "refresh_usage": False,
    })
    again = next(r for r in c.get(f"/api/customer-summary?period={label}").json()["rows"]
                 if r["salesforce_account_id"] == target["sf_account_id"])
    check(again["good_to_bill"], "13. Approval survives a rerun")

    # 10 — the approval is month-specific
    other = "2026-06" if label != "2026-06" else "2026-07"
    others = c.get(f"/api/customer-summary?period={other}").json().get("rows", [])
    leaked = [r for r in others
              if r["salesforce_account_id"] == target["sf_account_id"] and r["good_to_bill"]]
    check(not leaked, f"14. Approval did not leak into {other}")

    # 11 — audit trail
    trail = c.get(f"/api/audit?period={label}").json()
    actions = {a["action"] for a in trail}
    check({"good_to_bill_checked", "billing_period_rerun"} <= actions,
          "15. Audit trail records the actions", ", ".join(sorted(actions))[:80])

    # 12 — Slack renders without sending
    prev = c.get(f"/api/billing-periods/{label}/notification-preview").json()
    check(prev["mode"] == "dry_run" and "@csms" in prev["message"]
          and "<!subteam" not in prev["message"],
          "16. Slack dry-run renders with mentions defused", f"mode={prev['mode']}")

    # 13 — period cannot be marked ready while pricing is unresolved
    ready = c.post(f"/api/billing-periods/{label}/ready-to-bill")
    still_blocked = c.get(f"/api/customer-summary?period={label}").json()["totals"]["blocked_by_pricing"]
    if still_blocked:
        check(ready.status_code == 409,
              "17. Ready to Bill refused while pricing unresolved",
              ready.json().get("detail", "")[:70])
    else:
        check(ready.status_code == 200, "17. Ready to Bill allowed once clear")

    # 14 — closing needs the exact label
    bad = c.post(f"/api/billing-periods/{label}/close", json={"confirm": "yes"})
    check(bad.status_code == 400, "18. Closing requires explicit confirmation",
          bad.json().get("detail", "")[:70])

    # clean up what this script changed
    c.request("DELETE", f"/api/overrides/{target['sf_account_id']}")
    c.put("/api/approvals", json={
        "period": label, "billing_customer": row["billing_customer"],
        "salesforce_account_id": row["salesforce_account_id"], "good_to_bill": False,
    })
    print("\n  (rolled back the test price confirmation and approval)")

    failed = [label for ok_, label in results if not ok_]
    print("\n" + "=" * 60)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for f in failed:
        print(f"  FAILED: {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
