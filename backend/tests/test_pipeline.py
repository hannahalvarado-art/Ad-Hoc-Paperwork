#!/usr/bin/env python3
"""Regression tests for the billing rules.

    python -m unittest discover -s tests -v      (from backend/)

These target the rules where a silent change costs money: the pricing
hierarchy, the $0-vs-unconfirmed distinction, the entity split, the outlier
guard, and exclusion name matching.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pricing  # noqa: E402
from app.db import SCHEMA_PATH  # noqa: E402
from app.pipeline import stage1_mapping, stage2_contracts, stage3_exclusions  # noqa: E402

ACCOUNT_PRICED = "0018b0000224tcLAAQ"
ACCOUNT_UNPRICED = "0018b0000224qbbAAA"


def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    conn.executemany(
        "INSERT INTO sf_accounts (account_id, name, csm, adhoc_price) VALUES (?,?,?,?)",
        [
            (ACCOUNT_PRICED, "Bengard Ranch", "Madison Kois", 4.0),
            (ACCOUNT_UNPRICED, "Bonnie Plants, Inc.", "Rachel Stoltzmann", None),
            ("ACCT_EXPENSIVE", "Costly Farms", "Someone", 500.0),
            ("ACCT_OVERLOOK", "Overlook Harvesting Company, LLC", "Rachel Stoltzmann", 4.0),
        ],
    )
    conn.execute("INSERT INTO settings (key, value) VALUES ('price_outlier_threshold','16')")
    conn.execute(
        "INSERT INTO customer_map (source_customer, billing_customer, sf_account_id, reason) "
        "VALUES ('Hartnell','Bengard Ranch',?, 'billed under Bengard')",
        (ACCOUNT_PRICED,),
    )
    conn.commit()
    return conn


def raw(**kw) -> dict:
    base = {
        "id": 1, "enterprise_name": "Somebody Farms", "account_id": ACCOUNT_PRICED,
        "csm": "Madison Kois", "sf_price": 4.0, "worker_name": "A Worker",
        "seso_worker_id": "1", "paperwork_name": "I-9", "packet_id": "100",
        "num_src": 1, "sent_date": "2026-06-02", "signed_date": "2026-06-02",
        "sender_name": "Someone", "contract_ids": "abc", "has_active": 1,
    }
    return {**base, **kw}


class PricingHierarchy(unittest.TestCase):
    def test_salesforce_price_wins(self):
        e = pricing.effective({"flag": "OK", "sf_price": 4.0, "salesforce_account_id": "X"}, {})
        self.assertEqual(e["charge"], 4.0)
        self.assertEqual(e["pricing_source"], "Salesforce contracted")

    def test_unconfirmed_is_held_not_zero(self):
        """The rule the whole review queue exists to protect."""
        e = pricing.effective(
            {"flag": "CSM_CONFIRM_PRICE", "sf_price": None, "salesforce_account_id": "X"}, {}
        )
        self.assertIsNone(e["charge"])
        self.assertNotEqual(e["charge"], 0)
        self.assertEqual(e["flag"], "CSM_CONFIRM_PRICE")

    def test_override_releases_event(self):
        ovr = {"X": {"confirmed_unit_price": 7.5, "confirmed_by": "CSM"}}
        e = pricing.effective(
            {"flag": "CSM_CONFIRM_PRICE", "sf_price": None, "salesforce_account_id": "X"}, ovr
        )
        self.assertEqual(e["flag"], "CSM_CONFIRMED_PRICE")
        self.assertEqual(e["charge"], 7.5)
        self.assertEqual(e["sf_pricing_status"], "Not Configured")

    def test_confirmed_zero_is_a_real_price(self):
        ovr = {"X": {"confirmed_unit_price": 0, "confirmed_by": "CSM"}}
        e = pricing.effective(
            {"flag": "CSM_CONFIRM_PRICE", "sf_price": None, "salesforce_account_id": "X"}, ovr
        )
        self.assertEqual(e["charge"], 0.0)
        self.assertIn(e["flag"], pricing.PRICED_FLAGS)

    def test_excluded_and_held_never_charge(self):
        for flag in ("CUSTOMER_EXCLUDED", "PRICE_OUTLIER_REVIEW",
                     "MISSING_SALESFORCE_ACCOUNT", "ENTITY_BILLING_REVIEW"):
            e = pricing.effective({"flag": flag, "sf_price": 500, "salesforce_account_id": "X"}, {})
            self.assertIsNone(e["charge"], flag)

    def test_totals_use_decimal(self):
        """0.1 + 0.2 must be 0.30, not 0.30000000000000004."""
        t = pricing.total([pricing.money("0.1"), pricing.money("0.2")])
        self.assertEqual(str(t), "0.30")


class Stage1Classification(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()

    def tearDown(self):
        self.conn.close()

    def test_mapping_redirects_identity_and_price(self):
        out = stage1_mapping.run(self.conn, [raw(enterprise_name="Hartnell", account_id="", sf_price=None)])
        r = out[0]
        self.assertEqual(r["billing_customer"], "Bengard Ranch")
        self.assertEqual(r["salesforce_account"], "Bengard Ranch")
        self.assertEqual(r["sf_price"], 4.0)
        self.assertEqual(r["flag"], "OK")
        self.assertTrue(r["customer_mapping_applied"])

    def test_missing_account(self):
        out = stage1_mapping.run(self.conn, [raw(account_id="", sf_price=None)])
        self.assertEqual(out[0]["flag"], "MISSING_SALESFORCE_ACCOUNT")

    def test_blank_price_goes_to_review(self):
        out = stage1_mapping.run(self.conn, [raw(account_id=ACCOUNT_UNPRICED, sf_price="")])
        self.assertEqual(out[0]["flag"], "CSM_CONFIRM_PRICE")

    def test_outlier_is_held(self):
        out = stage1_mapping.run(self.conn, [raw(account_id="ACCT_EXPENSIVE", sf_price=500)])
        self.assertEqual(out[0]["flag"], "PRICE_OUTLIER_REVIEW")

    def test_threshold_is_configurable(self):
        self.conn.execute("UPDATE settings SET value='1000' WHERE key='price_outlier_threshold'")
        out = stage1_mapping.run(self.conn, [raw(account_id="ACCT_EXPENSIVE", sf_price=500)])
        self.assertEqual(out[0]["flag"], "OK")


class EntitySplit(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        self.conn.execute(
            """INSERT INTO entity_split_rules
               (source_customer, token_a, entity_a, token_b, entity_b,
                default_entity, review_label)
               VALUES ('Overlook Harvesting Company, LLC','OHC','Overlook Harvesting Company, LLC',
                       'OHM','Overlook Harvesting Michigan, LLC',
                       'Overlook Harvesting Company, LLC','Overlook — BILLING REVIEW')"""
        )
        self.conn.execute(
            "INSERT INTO entity_split_senders (rule_id, sender_name, resolves_to) "
            "VALUES (1,'Ana Sloan Caldera','entity_b')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _run(self, contract_name, sender="Someone Else"):
        self.conn.execute(
            "INSERT OR REPLACE INTO contract_lookup (packet_id, contract_names) VALUES ('100',?)",
            (contract_name,),
        )
        events = stage1_mapping.run(
            self.conn,
            [raw(enterprise_name="Overlook Harvesting Company, LLC",
                 account_id="ACCT_OVERLOOK", sender_name=sender)],
        )
        out, tally = stage2_contracts.run(self.conn, events)
        return out[0], tally

    def test_ohm_routes_to_michigan(self):
        r, _ = self._run("OHM 2026 Northland Farms FW")
        self.assertEqual(r["billing_customer"], "Overlook Harvesting Michigan, LLC")

    def test_ohc_routes_to_company(self):
        r, _ = self._run("OHC 2026 Some Farm FW")
        self.assertEqual(r["billing_customer"], "Overlook Harvesting Company, LLC")

    def test_token_match_is_case_insensitive(self):
        r, _ = self._run("ohm 2026 lowercase FW")
        self.assertEqual(r["billing_customer"], "Overlook Harvesting Michigan, LLC")

    def test_both_tokens_unknown_sender_is_held(self):
        r, tally = self._run("OHC and OHM combined FW", sender="Nobody Known")
        self.assertEqual(r["flag"], "ENTITY_BILLING_REVIEW")
        self.assertEqual(tally["review"], 1)
        self.assertIn("manual review", r["mapping_reason"])

    def test_both_tokens_known_sender_resolves(self):
        r, tally = self._run("OHC and OHM combined FW", sender="Ana Sloan Caldera")
        self.assertEqual(r["billing_customer"], "Overlook Harvesting Michigan, LLC")
        self.assertEqual(r["flag"], "OK")
        self.assertEqual(tally["resolved_by_sender"], 1)

    def test_no_token_falls_back_without_claiming_a_mapping(self):
        r, _ = self._run("2026 Untokened Contract FW")
        self.assertEqual(r["billing_customer"], "Overlook Harvesting Company, LLC")
        self.assertFalse(r["customer_mapping_applied"])


class Exclusions(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        self.conn.execute(
            "INSERT INTO excluded_customers (source_customer, reason) "
            "VALUES ('Peri & Sons Farms, Inc.','excluded from Ad Hoc billing')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _flag(self, name):
        events = stage1_mapping.run(self.conn, [raw(enterprise_name=name)])
        return stage3_exclusions.run(self.conn, events)[0]

    def test_exact_name_excluded(self):
        r = self._flag("Peri & Sons Farms, Inc.")
        self.assertEqual(r["flag"], "CUSTOMER_EXCLUDED")
        self.assertTrue(r["excluded"])

    def test_punctuation_variants_still_excluded(self):
        """The original required an exact string match, so any of these would
        have silently billed an excluded customer."""
        for variant in ("Peri & Sons Farms Inc",
                        "Peri & Sons Farms,  Inc.",
                        "peri & sons farms, inc."):
            with self.subTest(variant=variant):
                self.assertEqual(self._flag(variant)["flag"], "CUSTOMER_EXCLUDED")

    def test_other_customers_untouched(self):
        r = self._flag("Peri & Sons Trucking, Inc.")
        self.assertNotEqual(r["flag"], "CUSTOMER_EXCLUDED")
        self.assertFalse(r["excluded"])

    def test_excluded_events_are_retained(self):
        events = stage1_mapping.run(
            self.conn,
            [raw(enterprise_name="Peri & Sons Farms, Inc."), raw(packet_id="101")],
        )
        out = stage3_exclusions.run(self.conn, events)
        self.assertEqual(len(out), 2, "excluded rows must be kept for audit")
        s = stage3_exclusions.stats(out)
        self.assertEqual(s["billable_events"], 1)
        self.assertEqual(s["excluded_events"], 1)


class HexHelpers(unittest.TestCase):
    def test_timezone_conversion_crosses_the_date_line(self):
        from app.pipeline.hex_comparison import to_la_date

        # 03:00 UTC on the 2nd is still the 1st in Los Angeles.
        self.assertEqual(to_la_date("2026-06-02 03:00:00+00"), "2026-06-01")
        self.assertEqual(to_la_date("2026-06-02 18:00:00+00"), "2026-06-02")
        self.assertEqual(to_la_date(""), "")
        self.assertEqual(to_la_date("not a date"), "")

    def test_contract_fanout_collapses(self):
        from app.pipeline.hex_comparison import collapse_hex

        rows = [
            {"customer": "A", "worker": "W", "paperwork": "I-9", "sent": "2026-06-01",
             "signed": "2026-06-01", "sender": "S", "contract": f"c{i}"}
            for i in range(3)
        ]
        events, fanout = collapse_hex(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(fanout, 2, "Hex billed 3 rows for 1 packet")
        self.assertEqual(events[0]["raw_rows"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
