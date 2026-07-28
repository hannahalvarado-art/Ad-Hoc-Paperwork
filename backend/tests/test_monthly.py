#!/usr/bin/env python3
"""Regression tests for the monthly billing framework.

Companion to test_pipeline.py, which covers the reconciliation rules. These
cover the things recurring operation added, and target the same class of
problem: a silent change that costs money or loses an approval.

    python -m unittest discover -s tests -v      (from backend/)

The pure-logic tests (month arithmetic, the duplicate rule, eligibility, the
Slack message) run anywhere. The rest need a scratch Postgres database, the
same TEST_DATABASE_URL the stage tests use, and skip without one.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from app import periods  # noqa: E402
from app.db import MONTHLY_SCHEMA_PATH, SCHEMA_PATH, Conn  # noqa: E402
from app.periods import PeriodClosed  # noqa: E402
from app.pipeline import stage0_dedupe  # noqa: E402
from app.services import summaries  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_db = unittest.skipUnless(
    TEST_DATABASE_URL,
    "Set TEST_DATABASE_URL to a scratch Postgres database to run these.",
)

ACCOUNT = "0018b0000224tcLAAQ"
UNPRICED = "0018b0000224qbbAAA"


def scratch_db() -> Conn:
    conn = Conn(psycopg.connect(TEST_DATABASE_URL, autocommit=True))
    conn.executescript("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.executescript(MONTHLY_SCHEMA_PATH.read_text())
    conn.executemany(
        "INSERT INTO sf_accounts (account_id, name, csm, adhoc_price) VALUES (?,?,?,?)",
        [
            (ACCOUNT, "Bengard Ranch", "Madison Kois", 4.0),
            (UNPRICED, "Bonnie Plants, Inc.", "Rachel Stoltzmann", None),
        ],
    )
    return conn


def usage_row(conn, period_id, packet, worker, paperwork, **kw):
    cols = {
        "period_id": period_id,
        "packet_id": packet,
        "seso_worker_id": worker,
        "paperwork_name": paperwork,
        "source_customer": kw.get("source_customer", "Bengard Ranch"),
        "billing_customer": kw.get("billing_customer", "Bengard Ranch"),
        "salesforce_account_id": kw.get("account_id", ACCOUNT),
        "salesforce_account": "Bengard Ranch",
        "sf_price": kw.get("sf_price", 4.0),
        "flag": kw.get("flag", "OK"),
        "excluded": kw.get("excluded", 0),
        "csm": "Madison Kois",
        "sent_date": kw.get("sent_date", "2026-07-05"),
        "seso": None,
    }
    cols.pop("seso")
    names = ", ".join(cols)
    conn.execute(
        f"INSERT INTO usage_events ({names}) VALUES ({', '.join('?' for _ in cols)})",
        tuple(cols.values()),
    )


# ------------------------------------------------------------- month arithmetic
class MonthArithmetic(unittest.TestCase):
    def test_prior_month_from_the_second(self):
        """The job runs on the 2nd and must target the whole previous month."""
        self.assertEqual(periods.prior_month(date(2026, 8, 2)), (2026, 7))

    def test_prior_month_crosses_the_year(self):
        self.assertEqual(periods.prior_month(date(2027, 1, 2)), (2026, 12))

    def test_prior_month_is_stable_across_retry_days(self):
        """A retry on the 3rd must not target a different month than the 2nd."""
        self.assertEqual(
            periods.prior_month(date(2026, 8, 2)), periods.prior_month(date(2026, 8, 3))
        )

    def test_month_bounds_handle_short_and_leap_months(self):
        self.assertEqual(periods.month_bounds(2026, 2), ("2026-02-01", "2026-02-28"))
        self.assertEqual(periods.month_bounds(2028, 2), ("2028-02-01", "2028-02-29"))
        self.assertEqual(periods.month_bounds(2026, 7), ("2026-07-01", "2026-07-31"))

    def test_parse_label_rejects_nonsense(self):
        self.assertEqual(periods.parse_label("2026-07"), (2026, 7))
        with self.assertRaises(ValueError):
            periods.parse_label("2026-13")
        with self.assertRaises(ValueError):
            periods.parse_label("July")


# --------------------------------------------------------------- duplicate rule
class DuplicateRule(unittest.TestCase):
    """The rule: a duplicate is the same paperwork event differing only by
    contract. Everything else stays separate."""

    class FakeConn:
        def __init__(self):
            self.written = []

        def executemany(self, sql, seq):
            self.written.extend(seq)

    def run_stage(self, rows):
        conn = self.FakeConn()
        out, tally = stage0_dedupe.run(conn, rows)
        return out, tally, conn.written

    def row(self, packet, worker, paperwork, contract_id="", contract_name="", sent="2026-07-05"):
        return {
            "packet_id": packet, "seso_worker_id": worker, "paperwork_name": paperwork,
            "contract_ids": contract_id, "contract_name": contract_name, "sent_date": sent,
        }

    def test_same_worker_different_paperwork_is_not_a_duplicate(self):
        out, tally, _ = self.run_stage([
            self.row("1", "W1", "CA W-4", "c1", "Contract A"),
            self.row("2", "W1", "Plan 401(k)", "c1", "Contract A"),
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual(tally["rows_removed"], 0)

    def test_same_paperwork_same_contract_is_not_a_duplicate(self):
        """Two packets for the same worker/paperwork/day under ONE contract are
        two events. Collapsing them would undercount. June 2026 has such a pair
        and it must stay two rows."""
        out, tally, _ = self.run_stage([
            self.row("1", "W1", "CA W-4", "c1", "Contract A"),
            self.row("2", "W1", "CA W-4", "c1", "Contract A"),
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual(tally["rows_removed"], 0)
        self.assertEqual(tally["same_contract_kept"], 2)

    def test_contract_only_difference_collapses_once(self):
        out, tally, _ = self.run_stage([
            self.row("2", "W1", "CA W-4", "cB", "OHM Contract"),
            self.row("1", "W1", "CA W-4", "cA", "OHC Contract"),
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(tally["rows_removed"], 1)
        self.assertEqual(out[0]["duplicate_source_count"], 2)

    def test_collapse_preserves_every_contract(self):
        out, _, written = self.run_stage([
            self.row("1", "W1", "CA W-4", "cA", "OHC Contract"),
            self.row("2", "W1", "CA W-4", "cB", "OHM Contract"),
        ])
        row = out[0]
        self.assertEqual(json.loads(row["duplicate_contracts"]), ["OHC Contract", "OHM Contract"])
        self.assertEqual(row["contract_ids"], "cA,cB")
        self.assertEqual(sorted(json.loads(row["source_record_ids"])), ["1", "2"])
        # Written back to contract_lookup so stage 2's OHC/OHM branch sees both.
        self.assertIn(("1", "OHC Contract | OHM Contract"), written)

    def test_winner_is_deterministic_and_numeric(self):
        """A rerun must pick the same surviving packet id, or the merge churns.
        String ordering would pick '1000' over '999'."""
        rows = [
            self.row("1000", "W1", "CA W-4", "cA", "A"),
            self.row("999", "W1", "CA W-4", "cB", "B"),
        ]
        first, _, _ = self.run_stage(rows)
        second, _, _ = self.run_stage(list(reversed(rows)))
        self.assertEqual(first[0]["packet_id"], "999")
        self.assertEqual(first[0]["packet_id"], second[0]["packet_id"])

    def test_different_days_are_separate_events(self):
        out, tally, _ = self.run_stage([
            self.row("1", "W1", "CA W-4", "cA", "A", sent="2026-07-05"),
            self.row("2", "W1", "CA W-4", "cB", "B", sent="2026-07-06"),
        ])
        self.assertEqual(len(out), 2)


# ------------------------------------------------------- Good to Bill eligibility
class Eligibility(unittest.TestCase):
    def test_pricing_blocks_approval(self):
        ok, reason = summaries._eligibility(True, [], summaries.CSM_REVIEW_REQUIRED)
        self.assertFalse(ok)
        self.assertIn("pricing confirmation required", reason)

    def test_exception_blocks_approval(self):
        ok, reason = summaries._eligibility(False, ["ENTITY_BILLING_REVIEW"], summaries.BLOCKED)
        self.assertFalse(ok)
        self.assertIn("unresolved billing exception", reason)

    def test_excluded_customer_cannot_be_approved(self):
        ok, reason = summaries._eligibility(False, [], summaries.CUSTOMER_EXCLUDED)
        self.assertFalse(ok)
        self.assertIn("excluded", reason)

    def test_clear_customer_is_eligible(self):
        ok, reason = summaries._eligibility(False, [], summaries.READY_TO_BILL)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_confirmed_zero_still_requires_separate_approval(self):
        """A CSM confirming $0 settles the price. It does not approve the month."""
        rows = [{
            "review_status": summaries.READY_TO_BILL, "pricing_status": "CSM_CONFIRMED_PRICE",
            "blocking_exceptions": [], "good_to_bill": False, "good_to_bill_eligible": True,
            "billable_packets": 10, "excluded_packets": 0, "expected_amount": 0.0, "workers": 3,
        }]
        t = summaries.totals(rows)
        self.assertEqual(t["customers_good_to_bill"], 0)
        self.assertEqual(t["customers_not_yet_approved"], 1)


# ---------------------------------------------------------- ready-to-bill rule
class ReadyToBillRule(unittest.TestCase):
    """The agreed rule: blocking exceptions and unresolved pricing gate the
    period; Good to Bill approvals are reported but do not."""

    def customer(self, **kw):
        base = {
            "review_status": summaries.READY_TO_BILL, "pricing_status": "OK",
            "blocking_exceptions": [], "good_to_bill": False, "good_to_bill_eligible": True,
            "billable_packets": 1, "excluded_packets": 0, "expected_amount": 4.0, "workers": 1,
        }
        base.update(kw)
        return base

    def test_unapproved_customers_do_not_block(self):
        ok, reason = summaries.can_mark_ready([self.customer(), self.customer()])
        self.assertTrue(ok, reason)

    def test_unresolved_pricing_blocks(self):
        ok, reason = summaries.can_mark_ready([
            self.customer(pricing_status="CSM_CONFIRM_PRICE",
                          review_status=summaries.CSM_REVIEW_REQUIRED),
        ])
        self.assertFalse(ok)
        self.assertIn("pricing confirmation", reason)

    def test_blocking_exception_blocks(self):
        ok, reason = summaries.can_mark_ready([
            self.customer(blocking_exceptions=["MISSING_SALESFORCE_ACCOUNT"],
                          review_status=summaries.BLOCKED),
        ])
        self.assertFalse(ok)
        self.assertIn("billing exception", reason)

    def test_both_blockers_are_reported_together(self):
        ok, reason = summaries.can_mark_ready([
            self.customer(pricing_status="CSM_CONFIRM_PRICE",
                          review_status=summaries.CSM_REVIEW_REQUIRED),
            self.customer(blocking_exceptions=["PRICE_OUTLIER_REVIEW"],
                          review_status=summaries.BLOCKED),
        ])
        self.assertFalse(ok)
        self.assertIn("pricing confirmation", reason)
        self.assertIn("billing exception", reason)

    def test_excluded_customers_are_not_counted(self):
        ok, _ = summaries.can_mark_ready([
            self.customer(),
            self.customer(review_status=summaries.CUSTOMER_EXCLUDED,
                          billable_packets=0, excluded_packets=86, expected_amount=0.0),
        ])
        self.assertTrue(ok)


# ------------------------------------------------------------- closed periods
@requires_db
class ClosedPeriods(unittest.TestCase):
    def setUp(self):
        self.conn = scratch_db()
        self.period, _ = periods.get_or_create_period(self.conn, 2026, 7)

    def tearDown(self):
        self.conn.close()

    def test_open_period_accepts_writes(self):
        periods.assert_open(self.period, "test")  # does not raise

    def test_closed_period_refuses_writes(self):
        periods.set_status(self.conn, self.period["id"], periods.CLOSED, "a@b.com")
        closed = periods.get_period(self.conn, 2026, 7)
        with self.assertRaises(PeriodClosed) as ctx:
            periods.assert_open(closed, "rerun the billing period")
        # The message has to name a way forward, not just refuse.
        self.assertIn("reopen", str(ctx.exception))

    def test_legacy_closed_column_stays_in_step(self):
        """Older code reads periods.closed; it must not disagree with status."""
        periods.set_status(self.conn, self.period["id"], periods.CLOSED, "a@b.com")
        row = periods.get_period(self.conn, 2026, 7)
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["closed"], 1)
        self.assertTrue(periods.is_closed(row))

    def test_reopening_restores_writability(self):
        periods.set_status(self.conn, self.period["id"], periods.CLOSED, "a@b.com")
        periods.set_status(self.conn, self.period["id"], periods.IN_REVIEW, "a@b.com")
        row = periods.get_period(self.conn, 2026, 7)
        self.assertEqual(row["closed"], 0)
        periods.assert_open(row, "test")

    def test_rebuild_leaves_a_closed_period_alone(self):
        """A later price change must not rewrite a month that was billed."""
        usage_row(self.conn, self.period["id"], "P1", "W1", "CA W-4")
        summaries.rebuild(self.conn, self.period["id"])
        before = summaries.stored(self.conn, self.period["id"])[0]["expected_amount"]

        periods.set_status(self.conn, self.period["id"], periods.CLOSED, "a@b.com")
        self.conn.execute(
            "UPDATE usage_events SET sf_price = 999 WHERE period_id = ?", (self.period["id"],)
        )
        summaries.rebuild(self.conn, self.period["id"])
        after = summaries.stored(self.conn, self.period["id"])[0]["expected_amount"]
        self.assertEqual(before, after)


# ---------------------------------------------------------------- period keys
@requires_db
class PeriodIdentity(unittest.TestCase):
    def setUp(self):
        self.conn = scratch_db()

    def tearDown(self):
        self.conn.close()

    def test_creating_the_same_month_twice_returns_one_period(self):
        """The scheduled job and a manual run can race; both must land on the
        same row rather than making two."""
        first, created_first = periods.get_or_create_period(self.conn, 2026, 7)
        second, created_second = periods.get_or_create_period(self.conn, 2026, 7)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(created_first)
        self.assertFalse(created_second)

    def test_period_carries_its_month_window(self):
        p, _ = periods.get_or_create_period(self.conn, 2026, 7)
        self.assertEqual(p["period_start"], "2026-07-01")
        self.assertEqual(p["period_end"], "2026-07-31")
        self.assertEqual(p["label"], "2026-07")
        self.assertEqual(p["billing_type"], periods.BILLING_TYPE_ADHOC)

    def test_another_billing_type_gets_its_own_period(self):
        """The framework has to carry a second product without a second table."""
        adhoc, _ = periods.get_or_create_period(self.conn, 2026, 7)
        other, _ = periods.get_or_create_period(
            self.conn, 2026, 7, billing_type="WORKER_ONBOARDING"
        )
        self.assertNotEqual(adhoc["id"], other["id"])

    def test_default_period_prefers_an_open_month(self):
        """June IN_REVIEW should outrank July CLOSED as the landing page."""
        from app.db import resolve_period

        june, _ = periods.get_or_create_period(self.conn, 2026, 6)
        july, _ = periods.get_or_create_period(self.conn, 2026, 7)
        periods.set_status(self.conn, june["id"], periods.IN_REVIEW, "a@b.com")
        periods.set_status(self.conn, july["id"], periods.CLOSED, "a@b.com")
        self.assertEqual(resolve_period(self.conn, None)["label"], "2026-06")


# ------------------------------------------------------------------- approvals
@requires_db
class Approvals(unittest.TestCase):
    def setUp(self):
        self.conn = scratch_db()
        self.july, _ = periods.get_or_create_period(self.conn, 2026, 7)
        self.august, _ = periods.get_or_create_period(self.conn, 2026, 8)

    def tearDown(self):
        self.conn.close()

    def approve(self, period_id, customer="Bengard Ranch", account=ACCOUNT):
        self.conn.execute(
            "INSERT INTO customer_period_approvals "
            "(period_id, billing_customer, salesforce_account_id, good_to_bill, approved_by, approved_at) "
            "VALUES (?, ?, ?, 1, 'csm@sesolabor.com', '2026-08-02 10:00:00')",
            (period_id, customer, account),
        )

    def test_approval_is_month_specific(self):
        """A July approval must not approve August."""
        usage_row(self.conn, self.july["id"], "P1", "W1", "CA W-4")
        usage_row(self.conn, self.august["id"], "P2", "W1", "CA W-4", sent_date="2026-08-05")
        self.approve(self.july["id"])

        july = summaries.compute(self.conn, self.july["id"])[0]
        august = summaries.compute(self.conn, self.august["id"])[0]
        self.assertTrue(july["good_to_bill"])
        self.assertFalse(august["good_to_bill"])

    def test_approval_records_who_and_when(self):
        usage_row(self.conn, self.july["id"], "P1", "W1", "CA W-4")
        self.approve(self.july["id"])
        row = summaries.compute(self.conn, self.july["id"])[0]
        self.assertEqual(row["approved_by"], "csm@sesolabor.com")
        self.assertEqual(row["review_status"], summaries.GOOD_TO_BILL)

    def test_duplicate_approval_is_rejected_by_the_database(self):
        self.approve(self.july["id"])
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.approve(self.july["id"])


@requires_db
class AuditAtomicity(unittest.TestCase):
    """A change and the audit row explaining it must commit together.

    They did not, once: an override was written, the audit call then failed, and
    the result was a price change nobody could account for. Anything that
    records who did what has to be inside the same transaction as the thing they
    did.
    """

    def setUp(self):
        self.conn = scratch_db()
        self.period, _ = periods.get_or_create_period(self.conn, 2026, 7)

    def tearDown(self):
        self.conn.close()

    def test_a_failure_after_the_change_rolls_the_change_back(self):
        from app import audit
        from app.db import tx

        def attempt():
            with tx(self.conn):
                self.conn.execute(
                    "INSERT INTO customer_period_approvals "
                    "(period_id, billing_customer, salesforce_account_id, good_to_bill, approved_by) "
                    "VALUES (?, 'Bengard Ranch', ?, 1, 'csm@sesolabor.com')",
                    (self.period["id"], ACCOUNT),
                )
                audit.record(
                    self.conn, audit.GOOD_TO_BILL_SET, "csm@sesolabor.com",
                    period_id=self.period["id"], customer="Bengard Ranch",
                )
                raise RuntimeError("something fails after both writes")

        with self.assertRaises(RuntimeError):
            attempt()

        approvals = self.conn.execute(
            "SELECT COUNT(*) AS n FROM customer_period_approvals"
        ).fetchone()["n"]
        entries = self.conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
        self.assertEqual(approvals, 0, "the approval should have rolled back")
        self.assertEqual(entries, 0, "the audit row should have rolled back with it")

    def test_both_land_when_the_transaction_commits(self):
        from app import audit
        from app.db import tx

        with tx(self.conn):
            self.conn.execute(
                "INSERT INTO customer_period_approvals "
                "(period_id, billing_customer, salesforce_account_id, good_to_bill, approved_by) "
                "VALUES (?, 'Bengard Ranch', ?, 1, 'csm@sesolabor.com')",
                (self.period["id"], ACCOUNT),
            )
            audit.record(
                self.conn, audit.GOOD_TO_BILL_SET, "csm@sesolabor.com",
                period_id=self.period["id"], customer="Bengard Ranch",
            )

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM customer_period_approvals").fetchone()["n"], 1
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"], 1)


# ------------------------------------------------------------ idempotent merge
@requires_db
class UsageMerge(unittest.TestCase):
    def setUp(self):
        self.conn = scratch_db()
        self.period, _ = periods.get_or_create_period(self.conn, 2026, 7)

    def tearDown(self):
        self.conn.close()

    def test_natural_key_rejects_a_duplicate_row(self):
        """This constraint is the idempotency guarantee: a rerun cannot double
        a month's usage even if the merge logic were wrong."""
        usage_row(self.conn, self.period["id"], "P1", "W1", "CA W-4")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            usage_row(self.conn, self.period["id"], "P1", "W1", "CA W-4")

    def test_same_packet_different_paperwork_coexists(self):
        """One packet can cover several paperwork types; each is billable."""
        usage_row(self.conn, self.period["id"], "P1", "W1", "CA W-4")
        usage_row(self.conn, self.period["id"], "P1", "W1", "Plan 401(k)")
        n = self.conn.execute(
            "SELECT COUNT(*) AS n FROM usage_events WHERE period_id = ?", (self.period["id"],)
        ).fetchone()["n"]
        self.assertEqual(n, 2)

    def test_same_packet_different_worker_coexists(self):
        usage_row(self.conn, self.period["id"], "P1", "W1", "CA W-4")
        usage_row(self.conn, self.period["id"], "P1", "W2", "CA W-4")
        n = self.conn.execute(
            "SELECT COUNT(*) AS n FROM usage_events WHERE period_id = ?", (self.period["id"],)
        ).fetchone()["n"]
        self.assertEqual(n, 2)

    def test_excluded_usage_is_retained_but_not_billed(self):
        usage_row(self.conn, self.period["id"], "P1", "W1", "CA W-4",
                  flag="CUSTOMER_EXCLUDED", excluded=1, source_customer="Peri & Sons Farms, Inc.",
                  billing_customer="Peri & Sons Farms, Inc.")
        rows = summaries.compute(self.conn, self.period["id"])
        self.assertEqual(rows[0]["billable_packets"], 0)
        self.assertEqual(rows[0]["excluded_packets"], 1)
        self.assertEqual(rows[0]["review_status"], summaries.CUSTOMER_EXCLUDED)


# ----------------------------------------------------------- Slack composition
@requires_db
class SlackMessage(unittest.TestCase):
    def setUp(self):
        self.conn = scratch_db()
        self.period, _ = periods.get_or_create_period(self.conn, 2026, 7)
        usage_row(self.conn, self.period["id"], "P1", "W1", "CA W-4")
        usage_row(self.conn, self.period["id"], "P2", "W2", "CA W-4")
        summaries.rebuild(self.conn, self.period["id"])

    def tearDown(self):
        self.conn.close()

    def test_dev_mode_defuses_mentions(self):
        """A development send must not be able to ping @csms."""
        from app import slack

        text = slack.compose(self.conn, self.period["id"], mode="dev")
        self.assertIn("@csms", text)
        self.assertNotIn("<!subteam", text)
        self.assertNotIn("<@", text)

    def test_dry_run_is_labelled(self):
        from app import slack

        text = slack.compose(self.conn, self.period["id"], mode="dry_run")
        self.assertIn("DRY_RUN", text.upper())

    def test_message_carries_the_required_figures(self):
        from app import slack

        text = slack.compose(self.conn, self.period["id"], mode="dev")
        self.assertIn("July 2026", text)
        self.assertIn("unique billable packets", text)
        self.assertIn("expected billing currently known", text)
        self.assertIn("need pricing confirmation", text)
        self.assertIn("Good to Bill approval", text)

    def test_notification_is_recorded_once(self):
        """A retrying cron job must not notify twice."""
        from app import slack

        first = slack.send_review_notification(self.conn, self.period["id"], kind="review")
        second = slack.send_review_notification(self.conn, self.period["id"], kind="review")
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "skipped")

    def test_a_resend_is_allowed(self):
        """The manual Resend action is deliberately not suppressed."""
        from app import slack

        slack.send_review_notification(self.conn, self.period["id"], kind="review")
        resend = slack.send_review_notification(self.conn, self.period["id"], kind="review_resend")
        self.assertEqual(resend["status"], "sent")


if __name__ == "__main__":
    unittest.main()
