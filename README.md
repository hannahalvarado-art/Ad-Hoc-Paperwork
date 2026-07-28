# Ad Hoc Paperwork — Monthly Billing

Expected usage-based charges for completed Ad Hoc Paperwork signature packets,
processed one calendar month at a time. No invoices are issued and Salesforce is
never modified.

Started as a Node script chain plus a single-file HTML dashboard for one month's
validation; now a recurring monthly workflow on React + FastAPI + Neon, with
usage pulled from the Keboola warehouse.

**The production cron is not enabled.** See
[Enabling the monthly schedule](#enabling-the-monthly-schedule).

---

## How a month works

On or around the 2nd, the prior calendar month is processed. Everything is keyed
on **sent date** — the signed date is displayed but never decides the period.

```
locate or create the period ─→ PROCESSING
  pull prior-month usage by sent date
  consolidate contract-only duplicates
  map customers · split legal entities · apply exclusions
  merge into usage_events (idempotent)
  Salesforce pricing → persistent CSM overrides → CSM_CONFIRM_PRICE
  recompute customer summaries
                            ─→ IN_REVIEW ─→ Slack @csms
                               or FAILED, and no notification
```

CSMs then confirm any missing prices and tick **Good to Bill** per customer.
Accounting marks the period `READY_TO_BILL` and eventually `CLOSED`.

`monthly.run_period()` is the whole workflow. The scheduled job and the
"Run / re-run" button both call it — there is no separate manual path, so
testing July exercises exactly what will run on the 2nd.

### Two things that look alike and are not

| | Persistent CSM pricing | Good to Bill |
| --- | --- | --- |
| Scope | per Salesforce account | per customer **per month** |
| Lifetime | carries into future periods | must be reconfirmed every month |
| Means | "this is the agreed price" | "I reviewed this customer's month" |

A CSM confirming $0 settles the price for August too. It does not approve
August. Confirming a price and approving a month are separate actions, and the
Good to Bill checkbox stays unticked until someone ticks it.

### Idempotency

Rerunning an open month cannot duplicate its usage. `usage_events` is keyed on
`(period, packet, worker, paperwork)` — packet alone is not unique, because one
packet can cover several workers and several paperwork types, which is also why
different paperwork types stay separate billable events. A rerun updates rows in
place, inserts genuinely new ones, and marks anything that no longer qualifies
`NO_LONGER_QUALIFIES` rather than deleting it. A row someone already reviewed
that quietly vanished would be indistinguishable from one that was never there.

Price confirmations and Good to Bill approvals survive reruns.

### Closed periods

A closed period is the billed record and does not change:

- automated refreshes skip it
- a later price confirmation recalculates open periods only
- usage reruns are refused before any work is done
- its customer summaries are frozen at the values it closed with

Reopening is admin-only, requires a reason, and is written to the audit log.
Closing asks you to type the period label — a yes/no could be satisfied by a
stray click.

---

## Running it

Two terminals. Python 3.11+, Node 18+, and a Postgres database.

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp ../.env.example ../.env      # then put your connection string in DATABASE_URL
python seed.py --reset          # recreates the schema, loads config, runs the pipeline, verifies
uvicorn app.main:app --reload   # http://127.0.0.1:8000  (docs at /docs)
```

`.env` at the repo root (or in `backend/`) is loaded automatically and is
gitignored. `export DATABASE_URL=...` works too and takes precedence — which is
also why a `.env` can never shadow the value set in Vercel.

`seed.py` reads `backend/data/june_adhoc_v5.json`, which is **not in the
repository** — it carries worker names and Seso worker IDs. Get it from whoever
owns the billing extract and drop it in `backend/data/` before seeding. Without
it the API starts fine but every endpoint returns an empty period.

`--reset` drops and recreates the `public` schema. That takes the approved
price overrides and the audit history with it, so don't point it at anything
shared.

**Frontend**

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

Vite proxies `/api` to port 8000, so nothing needs configuring for local dev.

**Tests**

```bash
cd backend && python -m unittest discover -s tests -v
```

74 tests. The 38 covering pricing, month arithmetic, the duplicate rule, Good to
Bill eligibility and the ready-to-bill rule run with no setup. The other 36 need
a scratch database and skip without one:

```bash
TEST_DATABASE_URL=postgresql://localhost/adhoc_test python -m unittest discover -s tests -v
```

Each of those **drops and recreates the `public` schema**, so give it its own
database — not just its own schema, and never the one holding real periods.

---

## Running a month by hand

The development path for validating a month before the schedule exists.

With the warehouse configured, this is the whole thing:

```bash
curl -X POST localhost:8000/api/billing-periods/run \
     -H 'Content-Type: application/json' \
     -d '{"year": 2026, "month": 7, "notify": false}'
```

Without `SNOWFLAKE_*`, stage the month from a CSV in the shape
`app/sources/adhoc_usage.sql` returns and run the same workflow over it:

```bash
python tools/sync_accounts.py  --csv data/july_2026_accounts.csv   # account/price config
python tools/load_extract.py   --period 2026-07 --csv data/july_2026_usage.csv
```

Then walk the whole flow end to end and check it:

```bash
ADHOC_DEV_AUTH=1 ADHOC_DEV_USER=you@sesolabor.com \
  python tools/validate_month.py --period 2026-07
```

`validate_month.py` goes through the HTTP API rather than calling the services
directly, so authentication, the closed-period guard and the Good to Bill
eligibility rules are exercised the way a browser exercises them. It confirms a
price and approves a customer, then rolls both back. It never closes anything.

The `tools/collect_*.py` and `tools/build_extract.py` scripts are scaffolding
for pulling a month through the Keboola MCP in pages while the Snowflake
credentials are missing. Once `SNOWFLAKE_*` is set the source adapter does all
of it in one query and they can be deleted.

---

## Deploying

Configured for [Vercel Services](https://vercel.com/docs/services) via
`vercel.json`: the Vite frontend and the FastAPI backend build as two services
in one project, sharing a domain. `/api/*` routes to the backend, everything
else to the frontend — which is why the frontend calls same-origin `/api` with
no base URL to configure and no CORS involved.

In the Vercel project settings, **Framework Preset must be `Services`** and
**Root Directory must be `./`**. Build/Output/Install stay empty at the top
level; those settings live per-service in `vercel.json`.

Set `DATABASE_URL` as an environment variable. Use the **pooled** connection
string — each serverless invocation opens its own connection, and a direct
endpoint will exhaust its limit under any real concurrency.

The schema is applied on cold start (guarded by an advisory lock, so concurrent
cold starts don't race). Once it's applied, set `ADHOC_SKIP_MIGRATE=1` to drop
that round trip.

`GET /api/health` reports whether the database is actually reachable, which is
the first thing to check if the dashboard loads but comes up empty.

⚠️ The dashboard renders real worker names and Seso IDs. Sign-in gates the
*actions*, not the *page* — turn on Vercel Deployment Protection as well before
sharing the URL.

---

## Authentication

Google OIDC restricted to one hosted domain, with the session in an HttpOnly
cookie this backend signs. Chosen because price confirmations and Good to Bill
approvals are signoff: they need an identity the user cannot type. The `hd`
claim **and** the email domain are both checked, and a missing `hd` fails closed
— consumer Google accounts have no hosted domain, and treating "no claim" as
"claim satisfied" would let any gmail.com address through.

Without `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `ADHOC_SESSION_SECRET` the
app still serves every number; only the two attributable actions are disabled,
and the banner says why.

**What to set up in Google Cloud**

1. APIs & Services → Credentials → Create OAuth client ID → Web application.
2. Authorised redirect URI: `https://<your-domain>/api/auth/callback`
   (add `http://localhost:8000/api/auth/callback` for local work).
3. Put the client id and secret in the environment, generate a session secret
   with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
4. List anyone who may reopen a closed period in `ADHOC_ADMIN_EMAILS`.

Roles are deliberately minimal: any authenticated `@sesolabor.com` user can
confirm pricing and approve customers; an env-listed admin can additionally
reopen closed periods. The `audit_log` records the real identity on every
action, so splitting CSM from ACCOUNTING later is a policy change rather than a
migration.

For local development, `ADHOC_DEV_AUTH=1` plus `ADHOC_DEV_USER=you@sesolabor.com`
skips Google entirely. It needs **both** variables so one set by accident cannot
open the app, and the UI shows a `dev auth` badge so it cannot be mistaken for a
real session.

---

## Slack

**A Slack app with a bot token, not an incoming webhook.** Both can render a
real user-group mention — `<!subteam^S0123|@csms>` pings the group either way —
but a webhook is fixed to one channel, returns nothing useful, and cannot look
anything up. The bot token lets the app post to a different channel in
development and keep the returned `ts` as evidence the message landed.

**What to obtain**

1. Create a Slack app at api.slack.com/apps → From scratch, in the Seso workspace.
2. OAuth & Permissions → Bot Token Scopes: **`chat:write`**. Add
   **`chat:write.public`** if you would rather not invite the bot to channels.
   (`usergroups:read` and `users:read.email` are only needed if you want the app
   to *look up* the ids rather than being given them; the ids below are enough.)
3. Install to workspace, copy the **Bot User OAuth Token** (`xoxb-…`) into
   `SLACK_BOT_TOKEN`.
4. Invite the bot to both channels: `/invite @YourApp` in the real review
   channel and in a dev channel.
5. Channel ids: channel → View channel details → id at the bottom.
   → `SLACK_CHANNEL_ID`, `SLACK_DEV_CHANNEL_ID`
6. The `@csms` group id: Slack → People → User groups → open `@csms`; the URL
   ends in `/S0123ABCD`. → `SLACK_CSM_GROUP_ID`
7. Your member id: profile → More → Copy member ID. → `SLACK_NOTIFY_USER_ID`

Nothing is hardcoded — no token, webhook, group id, user id or channel id
appears anywhere in the source.

**Testing safely**

`SLACK_MODE` has three values and defaults to the safe one:

| Mode | Behaviour |
| --- | --- |
| `dry_run` *(default)* | Renders the message, stores it, **calls Slack not at all.** The exact text is returned to the UI by "Preview Slack message". Nothing can be notified because nothing is sent. |
| `dev` | Posts for real, but to `SLACK_DEV_CHANNEL_ID`, with every mention turned into plain text — `@csms` instead of `<!subteam^…>`. Looks right, pings nobody. |
| `live` | The real thing. Additionally requires `SLACK_ALLOW_LIVE=1`, so promoting an environment takes two deliberate changes rather than one typo. Without it, `live` silently degrades to `dev` and reports that it did. |

The scheduled review message is recorded under `kind='review'` with a partial
unique index on successful sends, so a retrying cron job is told it already
sent. The manual **Resend review notification** action is `kind='review_resend'`
and is deliberately not suppressed.

A failed run never notifies.

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

## Where usage comes from

Keboola project 10112 ("Seso Prod"), Snowflake, database `SAPI_10112`. The query
is `backend/app/sources/adhoc_usage.sql`, kept as a file so it can be pasted
into a worksheet unchanged when someone needs to check a number by hand.

The lineage, verified by reproducing the known-good June 2026 extract:

| App field | Source |
| --- | --- |
| `packet_id` | `prod_ad_hoc_worker_packet.id` |
| `sent_date` | `.created_at`, converted to `America/Los_Angeles` |
| `signed_date` | `.signed_by_worker_at` |
| `paperwork_name` | `prod_ad_hoc_document_template_configuration.internal_name` |
| `seso_worker_id` | `prod_enterprise_worker.id` |
| `source_customer` | `prod_enterprise.legal_name` |
| `contract_name` | `prod_h2a_contract.name`, joined on `contract_id` |
| Salesforce account | `sf_account_enterprises` by `enterprise_id` |
| Salesforce price | `salesforce_opportunity_product_cw`, `sf_product_code = 'AdHoc'` |

"Completed Signature Packets only" resolves structurally rather than by display
name: `status = 'COMPLETE'` and `configuration_type IN ('ANVIL_USER_CONFIGURED',
'ANVIL_SESO_CONFIGURED')`. The two required content exclusions are their own
`configuration_type` values and are therefore already excluded by that filter —
`HAMMER_FEDERAL_W4` and `SESO_INTERNAL_DISCIPLINARY_NOTICE`. Matching those on
`paperwork_name` would be fragile; the configuration type is structural.

Three traps worth knowing, all of which cost time to discover:

- `internal_name`, **not** `worker_facing_name` — config 609 is internally
  "Plan 401(k)" and worker-facing "401(k) Plan", and the app uses the former.
- Join `prod_h2a_contract` on **`contract_id`**, not `uuid`. It has both and
  only one matches.
- Do **not** use `out.c-data_model.out_ad_hoc_paperwork`. It has no packet id,
  its `hfid` is a contract-level grouping rather than a packet key, its coverage
  is incomplete, its `active_contract` column is the contract *name* rather than
  a boolean, and its `is_invoicing` flag encodes a broader billability rule than
  this app's.

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

## Enabling the monthly schedule

**Not enabled.** Three separate things are missing on purpose, so no single edit
can start messaging `@csms` from a half-finished environment:

1. `vercel.json` has no `crons` entry.
2. `CRON_SECRET` is unset, and `/api/cron/monthly` refuses every request without it.
3. `ADHOC_CRON_ENABLED` is not `1`, and the route refuses again without it.

`GET /api/cron/status` reports all three without running anything.

When July has been validated, add to `vercel.json`:

```json
"crons": [
  { "path": "/api/cron/monthly", "schedule": "0 14 2 * *" }
]
```

14:00 UTC on the 2nd is 06:00 America/Los_Angeles — after the warehouse's
nightly sync, before anyone starts reviewing. Then set `CRON_SECRET` (Vercel
sends it as `Authorization: Bearer …`), `ADHOC_CRON_ENABLED=1`, and redeploy.

The job is safe to retry: it resolves to the same period, merges usage rather
than appending it, skips a closed month with `status: skipped` rather than
erroring, and the once-only notification index means a retry after a partial
failure cannot notify twice.

### Before turning it on

- [x] July 2026 processes end to end from real warehouse data
- [x] June 2026 still reproduces its baseline exactly (645 / $1,844.00 / 86 excluded / 12 customers)
- [x] a rerun is idempotent — 0 added, everything updated
- [x] `CSM_CONFIRM_PRICE` customers surface, and Good to Bill is blocked until resolved
- [x] a price confirmation persists, records the authenticated user, and recalculates open periods only
- [x] Good to Bill persists, records the authenticated user, and does not leak into another month
- [x] audit log records the actions
- [x] Slack renders in `dry_run` with mentions defused
- [x] `READY_TO_BILL` is refused while pricing is unresolved; closing needs explicit confirmation
- [x] closed periods refuse writes and do not recalculate
- [ ] `SNOWFLAKE_*` configured so the job can pull usage unattended
- [ ] Google OAuth client created, `ADHOC_SESSION_SECRET` set, admins listed
- [ ] Slack app installed, bot invited, ids set; one `dev`-mode send confirmed in the dev channel
- [ ] a full dry run of `/api/cron/monthly` with `CRON_SECRET` against August

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

3. ~~**No authentication.**~~ Resolved: Google OIDC, domain-restricted, and
   `confirmed_by` / `approved_by` now come from the session rather than a text
   box. See [Authentication](#authentication).

4. ~~**`num_src` is carried but not recomputed.**~~ Resolved: the collapse used
   to happen upstream of `v2.json`, but pulling from the warehouse means the raw
   rows are no longer pre-deduplicated, so the rule lives in
   `pipeline/stage0_dedupe.py` now.

5. **Peri & Sons dominates July by volume.** 1,048 of July's 1,749 packets are
   Peri & Sons, all excluded, so July's billable total is 701 across ten
   customers. Worth confirming the exclusion is still intended at that scale.

6. **Six of July's ten customers have no Salesforce Ad Hoc price**, including
   Chapa Global (172 packets) and Golden Eagle (149). They are correctly held at
   `CSM_CONFIRM_PRICE` rather than billed at $0, but that is a lot of the month
   waiting on CSM confirmation.

---

## Layout

```
backend/
  app/
    main.py               FastAPI app, CORS, schema bootstrap, health
    db.py                 connection helpers, period resolution
    schema.sql            the validated reconciliation schema
    schema_monthly.sql    what recurring monthly operation added
    periods.py            month arithmetic, status lifecycle, CLOSED guard
    auth.py               Google OIDC, signed session cookie, admin flag
    audit.py              the append-only audit trail
    slack.py              review notification, dry_run/dev/live modes
    pricing.py            the pricing hierarchy (was eff())
    reporting.py          KPIs, excluded, review queue, event list
    models.py             Pydantic request models
    services/
      monthly.py          THE monthly workflow — cron and UI both call this
      summaries.py        per-customer results, Good to Bill, accounting counts
    sources/
      adhoc_usage.sql     the warehouse query, with its reasoning
      keboola.py          Snowflake adapter
      upload.py           use what is already staged in raw_events
    pipeline/
      stage0_dedupe.py    contract-only duplicate consolidation
      stage1_mapping.py   customer mapping and price classification
      stage2_contracts.py contract names and entity split
      stage3_exclusions.py customer exclusions
      hex_comparison.py   reconciliation against the legacy Hex report
      runner.py           ingest helpers, event columns
    routers/              dashboard, overrides, config, pipeline, comparison,
                          auth, billing, cron
  tools/
    load_extract.py       stage a CSV and run the workflow over it
    sync_accounts.py      upsert Salesforce account/price config
    validate_month.py     18-check end-to-end walk through the HTTP API
    collect_*.py          scaffolding for paging a month through the Keboola MCP
    build_extract.py       — all four are disposable once SNOWFLAKE_* is set
  seed.py                 seed + verify against the Node output
  tests/
    test_pipeline.py      the reconciliation rules
    test_monthly.py       periods, duplicates, approvals, Slack, immutability

frontend/
  src/
    App.jsx               page shell, shared refresh on any save
    api.js                fetch client + useApi hook
    styles.css            the original design system, tokens unchanged
    components/           PeriodBar, AccountingPanel, KpiRow, ReviewQueue,
                          SummaryTable, ExcludedTable, EventTable,
                          MethodNotes, Pill
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

Monthly operation:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/auth/me` | who am I; is sign-in even configured |
| GET | `/api/auth/login` · `/callback` | Google OIDC |
| GET | `/api/billing-periods` | month switcher |
| GET | `/api/billing-periods/{label}` | one period, its totals, its runs |
| POST | `/api/billing-periods/run` | run or re-run a month — the cron's entry point too |
| POST | `/api/billing-periods/{label}/refresh-usage` | re-pull and merge |
| POST | `/api/billing-periods/{label}/refresh-pricing` | re-price without re-pulling |
| POST | `/api/billing-periods/{label}/ready-to-bill` | gated on unresolved pricing and exceptions |
| POST | `/api/billing-periods/{label}/close` | needs the label typed back |
| POST | `/api/billing-periods/{label}/reopen` | admin only, needs a reason |
| GET | `/api/billing-periods/{label}/notification-preview` | exactly what would be sent |
| POST | `/api/billing-periods/{label}/notify` | deliberate resend |
| GET | `/api/customer-summary` | Summary by Billing Customer, filterable |
| GET | `/api/accounting` | the accounting counts and controls |
| PUT | `/api/approvals` | Good to Bill for one customer in one month |
| GET | `/api/usage-events` | the persisted usage layer, including disqualified rows |
| GET | `/api/audit` | who did what, when |
| POST | `/api/cron/monthly` | scheduled job — bearer secret, disabled by default |
| GET | `/api/cron/status` | whether the schedule would run |

Interactive docs at `http://127.0.0.1:8000/docs`.
