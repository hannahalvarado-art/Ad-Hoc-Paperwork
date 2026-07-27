# Ad Hoc Paperwork — Billing Reconciliation

Expected usage-based charges for completed Ad Hoc Paperwork signature packets.
Read-only validation: no invoices are issued and Salesforce is never modified.

Converted from a Node script chain plus a single-file HTML dashboard into
React + Python + SQLite.

---

## Running it

Two terminals. Python 3.11+ and Node 18+.

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python seed.py --reset          # builds the DB, loads config, runs the pipeline, verifies
uvicorn app.main:app --reload   # http://127.0.0.1:8000  (docs at /docs)
```

**Frontend**

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

Vite proxies `/api` to port 8000, so nothing needs configuring for local dev.

**Tests**

```bash
cd backend && python -m unittest discover -s tests -v     # 23 tests, no extra deps
```

---

## What replaced what

| Was | Now |
| --- | --- |
| `01_customer_mapping_and_pricing.js` | `app/pipeline/stage1_mapping.py` |
| `02_contract_names_and_overlook_split.js` | `app/pipeline/stage2_contracts.py` |
| `03_customer_exclusion.js` | `app/pipeline/stage3_exclusions.py` |
| `hex_comparison_analysis.js` | `app/pipeline/hex_comparison.py` |
| `june_adhoc_dashboard.html` | `frontend/src/` (React) |
| `v2 → v3 → v4 → v5.json` handoffs | one atomic run, `events` table |
| `CUSTOMER_MAP` dict | `customer_map` table |
| `EXCLUDED_CUSTOMERS` dict | `excluded_customers` table |
| `ACCT` + `TARGET_PRICE` dicts | `sf_accounts` table |
| `OVERLOOK_*` constants | `entity_split_rules` + `entity_split_senders` |
| `price > 16` literal | `settings.price_outlier_threshold` |
| `contract_lookup.csv` + `parseCSV()` | `contract_lookup` table, `csv` module |
| `localStorage` overrides | `price_overrides` + `override_audit` tables |
| `eff()` in the browser | `app/pricing.py`, server-side |

Editing a rule no longer means editing a script. Each config write returns
`rerun_required: true` — the change applies on the next
`POST /api/pipeline/run`.

---

## Verification

The three input files (`june_adhoc_v2.json`, `contract_lookup.csv`,
`hex_june2026.csv`) were not part of the handoff, but the dashboard had the
final 645-event result inlined as `const DATA=[...]`. Because the stages are
deterministic, `seed.py` reconstructs the pipeline's input from that output,
re-runs the Python stages, and diffs the result against the known-good Node
run:

```
✓ Verified: all 645 events match the Node output on every billing field
  (customer, account, price, flag, dates, contract, exclusion)
```

Totals reproduce as well: **$1,844.00** expected, 461 priced events, 98
awaiting a CSM price across 5 accounts, 86 excluded, 12 billing customers.

The only differences are four reworded `mapping_reason` strings, a consequence
of generalising Overlook's hardcoded reason into a template. `seed.py` reports
those separately rather than folding them into a clean pass.

### Loading the real inputs

```bash
curl -F file=@june_adhoc_v2.json "localhost:8000/api/ingest/raw-file?period=2026-06"
curl -F file=@contract_lookup.csv  localhost:8000/api/ingest/contract-lookup
curl -X POST "localhost:8000/api/pipeline/run?period=2026-06"
curl -F file=@hex_june2026.csv "localhost:8000/api/comparison/run?period=2026-06"
```

---

## Pricing hierarchy

The rule the review queue exists to protect:

1. **Salesforce contracted price** from a Closed-Won opportunity → use it.
2. **Approved CSM override** → `CSM_CONFIRMED_PRICE`, bill at the confirmed
   price, which may legitimately be **$0**.
3. **Neither** → `CSM_CONFIRM_PRICE`. Held. Never auto-$0, never a borrowed
   price from another account.

A confirmed $0 and an unconfirmed price are different states and are counted
differently: the first is priced, the second is pending. Salesforce is never
written to — overrides live only in the approved-override layer.

---

## Deliberate changes

**Pricing moved to the server.** `eff()` ran in the browser and was called
independently by `renderKpis`, `renderSummary` and `renderDetail`. Three call
sites deriving money from the same data is three chances to disagree. One pass
now feeds every endpoint.

**Overrides left `localStorage`.** They were scoped to one browser profile and
vanished on a cache clear, so two people reviewing the same period saw
different totals. `price_overrides` is shared; `override_audit` is append-only,
so a revoked price stays explainable after the fact. The export envelope is
unchanged, so previously exported files still import.

**The 1,200-row cap is gone.** Filtering, sorting and paging happen in SQL.
The status filter matches the *effective* flag, so "CSM Confirmed" finds events
whose stored flag is still `CSM_CONFIRM_PRICE` but which an override released.

**Exclusion matching normalises names.** The dict key had to read
`'Peri & Sons Farms, Inc.'` while the Salesforce account reads
`'Peri & Sons Farm, Inc.'`; exact-equality matching meant one stray character
would silently bill an excluded customer. Matching now uses the same
normalisation `hex_comparison_analysis.js` already had in `nCust()`. Covered by
`test_punctuation_variants_still_excluded`.

**Money is `Decimal`.** Float arithmetic is survivable at $4 a packet and not
worth carrying into a billing system.

**The Overlook rule is generalised.** Tokens, entity names, the default and the
resolving senders are rows, so the next customer billing across two legal
entities is configuration rather than a patch. The flag is
`ENTITY_BILLING_REVIEW`; one customer's name no longer appears in the enum.

**Runs are atomic.** A pipeline run either replaces a period's events entirely
or leaves the previous set untouched. No more `v4.json` on disk disagreeing
with the `v5.json` beside it.

### Bug found while porting

`hex_comparison_analysis.js` read `june_adhoc_v2.json` and then referenced
`r.flag` and `r.expected_charge` — fields stage 1 adds and which therefore did
not exist yet. Every Claude-side flag and charge in `comparison2.json` was
`undefined`. The port reads the final `events` table instead.

---

## Open questions

1. **Overlook's Michigan entity bills against the Company account.** Stage 2
   rewrites `billing_customer` but leaves `salesforce_account_id` pointing at
   `0018b0000224qbBAAQ` (Overlook Harvesting Company), so Michigan's packets
   are priced from Company's $4 and any CSM override would attach to Company.
   Intentional, or should Michigan have its own Salesforce account?

2. **Peri & Sons carries a $500 Ad Hoc price.** With the exclusion lifted those
   86 events would land in `PRICE_OUTLIER_REVIEW`, not `OK` — worth knowing
   before anyone removes the exclusion.

3. **No authentication.** Anyone who can reach the app can confirm a price.
   `confirmed_by` is a free-text field, and `override_audit` records what was
   typed rather than who was logged in. Fine for validation, not for signoff.

4. **`num_src` is carried but not recomputed.** Dedupe happened upstream of
   `v2.json`, so that collapse is not part of this pipeline. Two events arrive
   with `num_src = 2`.

---

## Layout

```
backend/
  app/
    main.py               FastAPI app, CORS, schema bootstrap
    db.py                 connection helpers, period resolution
    schema.sql            tables, indexes, comments tying each back to its dict
    pricing.py            the pricing hierarchy (was eff())
    reporting.py          KPIs, summary, excluded, review queue, event list
    models.py             Pydantic request models
    pipeline/
      stage1_mapping.py   customer mapping and price classification
      stage2_contracts.py contract names and entity split
      stage3_exclusions.py customer exclusions
      hex_comparison.py   reconciliation against the legacy Hex report
      runner.py           chains the stages in one transaction
    routers/              dashboard, overrides, config, pipeline, comparison
  seed.py                 seed + verify against the Node output
  tests/test_pipeline.py  23 regression tests
  data/june_adhoc_v5.json the 645 events recovered from the dashboard

frontend/
  src/
    App.jsx               page shell, shared refresh on override change
    api.js                fetch client + useApi hook
    styles.css            the original design system, tokens unchanged
    components/           KpiRow, ReviewQueue, SummaryTable,
                          ExcludedTable, EventTable, MethodNotes, Pill
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/kpis` | headline figures |
| GET | `/api/summary` | totals by billing customer |
| GET | `/api/excluded` | excluded rollup, retained for audit |
| GET | `/api/events` | filter, sort, page |
| GET | `/api/review-queue` | accounts awaiting a CSM price |
| PUT | `/api/overrides` | confirm a price |
| DELETE | `/api/overrides/{id}` | revoke, keeping the audit row |
| GET | `/api/overrides/export` | same envelope the old button produced |
| POST | `/api/overrides/import` | load an exported file |
| GET | `/api/overrides/audit` | who confirmed what, when |
| GET | `/api/config` | accounts, mappings, exclusions, split rules, settings |
| PUT | `/api/config/...` | edit a rule |
| POST | `/api/pipeline/run` | re-run stages 1–3 |
| POST | `/api/comparison/run` | upload the Hex CSV and reconcile |

Interactive docs at `http://127.0.0.1:8000/docs`.
