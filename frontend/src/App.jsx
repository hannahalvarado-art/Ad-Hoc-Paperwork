import { useCallback, useState } from "react";
import { api, useApi } from "./api.js";
import KpiRow from "./components/KpiRow.jsx";
import ReviewQueue from "./components/ReviewQueue.jsx";
import SummaryTable from "./components/SummaryTable.jsx";
import ExcludedTable from "./components/ExcludedTable.jsx";
import EventTable from "./components/EventTable.jsx";
import MethodNotes from "./components/MethodNotes.jsx";
import { Notice } from "./components/Pill.jsx";

export default function App() {
  const [period, setPeriod] = useState("");
  // Bumped whenever an override is saved or revoked, so every dependent
  // section refetches. Pricing lives on the server, so there is nothing to
  // recompute here — the numbers just come back correct.
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const periods = useApi(() => api.periods(), [], []);
  const kpis = useApi(() => api.kpis(period), [period, refreshKey]);
  const summary = useApi(() => api.summary(period), [period, refreshKey]);
  const excluded = useApi(() => api.excluded(period), [period, refreshKey], []);
  const queue = useApi(() => api.reviewQueue(period), [period, refreshKey]);
  const config = useApi(() => api.config(), []);

  const activePeriod = kpis.data?.period;
  const fatal = periods.error || kpis.error;

  return (
    <div className="wrap">
      <header>
        <div className="eyebrow">Finance validation exercise · revised logic</div>
        <h1>Ad Hoc Paperwork — {activePeriod?.name ?? "Billing"} Reconciliation</h1>
        <p className="sub">
          Expected usage-based charges for completed Ad Hoc Paperwork signature packets. Where
          Salesforce has no Ad Hoc price, the account routes to a <b>CSM price review</b>; confirmed
          prices are saved to an approved-override layer and applied automatically to future periods.
        </p>
      </header>

      <div className="banner">
        <span className="dot" />
        <div>
          <b>Read-only validation — no invoices, and Salesforce is never modified.</b> CSM
          confirmations are stored in a separate approved-override layer, shared across everyone
          using this app and exportable as a config file for the billing pipeline. Pricing order:
          Salesforce contracted price → approved CSM override → otherwise CSM Confirm Price. An
          unconfirmed price is never treated as $0.
        </div>
      </div>

      {/* The server's own message where there is one — a 503 from an
          unconfigured or unreachable database explains itself, and the old
          hardcoded "port 8000 / run seed.py" advice was misleading anywhere
          other than a local dev machine. */}
      {fatal && (
        <Notice kind="error">
          {fatal}
          {/^\d+\s/.test(fatal) && (
            <> — the API responded but could not serve this request.</>
          )}
        </Notice>
      )}

      {(periods.data?.length ?? 0) > 1 && (
        <div className="filters">
          <select
            aria-label="Billing period"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
          >
            <option value="">Latest period</option>
            {periods.data.map((p) => (
              <option key={p.label} value={p.label}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <KpiRow kpis={kpis.data} loading={kpis.loading} />

      <section>
        <div className="sec-h">
          <h2>CSM price review</h2>
          <span className="hint">
            accounts with no Salesforce Ad Hoc price — confirm to release for billing
          </span>
        </div>
        <ReviewQueue
          queue={queue.data}
          loading={queue.loading}
          error={queue.error}
          onChange={refresh}
        />
      </section>

      <section>
        <div className="sec-h">
          <h2>Summary by billing customer</h2>
          <span className="hint">unique billable paperwork · unit price · expected total</span>
        </div>
        <SummaryTable summary={summary.data} loading={summary.loading} />
      </section>

      <ExcludedTable rows={excluded.data} />

      <section>
        <div className="sec-h">
          <h2>Paperwork events</h2>
          <span className="hint">source and contract preserved; pricing hierarchy applied</span>
        </div>
        <EventTable period={period} refreshKey={refreshKey} />
      </section>

      <MethodNotes config={config.data} />

      <div className="foot-note">
        <span>
          <b>Pricing:</b> Salesforce → approved CSM override → CSM Confirm
        </span>
        <span>
          <b>Overrides:</b> separate config layer; Salesforce untouched
        </span>
        <span>
          <b>Status:</b> validation only — not invoiced
        </span>
      </div>
    </div>
  );
}
