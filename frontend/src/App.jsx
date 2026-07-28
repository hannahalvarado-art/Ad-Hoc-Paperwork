import { useCallback, useEffect, useState } from "react";
import { api, useApi } from "./api.js";
import KpiRow from "./components/KpiRow.jsx";
import ReviewQueue from "./components/ReviewQueue.jsx";
import SummaryTable from "./components/SummaryTable.jsx";
import ExcludedTable from "./components/ExcludedTable.jsx";
import EventTable from "./components/EventTable.jsx";
import MethodNotes from "./components/MethodNotes.jsx";
import AccountingPanel from "./components/AccountingPanel.jsx";
import PeriodBar, { SignInNotice } from "./components/PeriodBar.jsx";
import { Notice } from "./components/Pill.jsx";

export default function App() {
  // The month being viewed. Empty means "whatever the server considers
  // current", which is the newest period that is not closed.
  const [period, setPeriod] = useState(
    () => new URLSearchParams(window.location.search).get("period") || "",
  );
  // Bumped whenever anything is saved, so every dependent section refetches.
  // Pricing and approvals live on the server; there is nothing to recompute
  // here, the numbers just come back correct.
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const auth = useApi(() => api.me(), []);
  const periods = useApi(() => api.billingPeriods(), [refreshKey]);
  const kpis = useApi(() => api.kpis(period), [period, refreshKey]);
  const summary = useApi(() => api.customerSummary({ period }), [period, refreshKey]);
  const accounting = useApi(() => api.accounting(period), [period, refreshKey]);
  const excluded = useApi(() => api.excluded(period), [period, refreshKey], []);
  const queue = useApi(() => api.reviewQueue(period), [period, refreshKey]);
  const config = useApi(() => api.config(), []);

  const activePeriod = summary.data?.period ?? kpis.data?.period;

  // Keep the URL in step so a link to a month opens on that month.
  useEffect(() => {
    if (!activePeriod?.label) return;
    const url = new URL(window.location.href);
    url.searchParams.set("period", activePeriod.label);
    window.history.replaceState({}, "", url);
  }, [activePeriod?.label]);

  const canAct = !!auth.data?.authenticated;
  const readOnly = !!activePeriod?.read_only;
  const fatal = periods.error || kpis.error;

  const signOut = async () => {
    await api.logout();
    window.location.reload();
  };

  return (
    <div className="wrap">
      <header>
        <div className="eyebrow">
          Ad Hoc Paperwork · monthly billing · {activePeriod?.billing_type_label ?? ""}
        </div>
        <h1>{activePeriod?.name ?? "Billing"} — Ad Hoc Paperwork Reconciliation</h1>
        <p className="sub">
          Expected usage-based charges for completed Ad Hoc Paperwork signature packets, by{" "}
          <b>sent date</b>. Where Salesforce has no Ad Hoc price, the account routes to a{" "}
          <b>CSM price review</b>; confirmed prices persist and apply to future periods
          automatically. <b>Good to Bill is per month</b> and must be reconfirmed each period.
        </p>
      </header>

      <div className="banner">
        <span className="dot" />
        <div>
          <b>No invoices are issued and Salesforce is never modified.</b> CSM confirmations live in
          a separate approved-override layer. Pricing order: Salesforce contracted price → approved
          CSM override → otherwise CSM Confirm Price. An unconfirmed price is never treated as $0.
          Closed periods are immutable.
        </div>
      </div>

      {fatal && (
        <Notice kind="error">
          {fatal}
          {/^\d+\s/.test(fatal) && <> — the API responded but could not serve this request.</>}
        </Notice>
      )}

      <PeriodBar
        periods={periods.data}
        value={activePeriod?.label ?? period}
        onChange={setPeriod}
        period={activePeriod}
        auth={auth.data}
        onSignOut={signOut}
      />
      <SignInNotice auth={auth.data} />

      <KpiRow kpis={kpis.data} loading={kpis.loading} />

      <AccountingPanel
        data={accounting.data}
        loading={accounting.loading}
        error={accounting.error}
        period={activePeriod}
        canAct={canAct}
        onChange={refresh}
      />

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
          canAct={canAct}
          readOnly={readOnly}
          auth={auth.data}
        />
      </section>

      <section>
        <div className="sec-h">
          <h2>Summary by billing customer</h2>
          <span className="hint">
            unique billable packets · unit price · pricing source · Good to Bill
          </span>
        </div>
        <SummaryTable
          data={summary.data}
          loading={summary.loading}
          error={summary.error}
          period={activePeriod}
          canAct={canAct}
          onChange={refresh}
        />
      </section>

      <ExcludedTable rows={excluded.data} />

      <section>
        <div className="sec-h">
          <h2>Paperwork events</h2>
          <span className="hint">source and contract preserved; pricing hierarchy applied</span>
        </div>
        <EventTable period={activePeriod?.label ?? period} refreshKey={refreshKey} />
      </section>

      {/* config.error is deliberately not part of `fatal`: the rules panel is
          not needed to read the numbers, so a failed /api/config degrades that
          one section instead of replacing the whole dashboard with a banner. It
          does have to be reported *somewhere*, though — it used to be dropped
          entirely and the panel just claimed nothing was configured. */}
      <MethodNotes config={config.data} loading={config.loading} error={config.error} />

      <div className="foot-note">
        <span>
          <b>Pricing:</b> Salesforce → approved CSM override → CSM Confirm
        </span>
        <span>
          <b>Approval:</b> Good to Bill, per customer per month
        </span>
        <span>
          <b>Status:</b> {activePeriod?.status?.replace(/_/g, " ") ?? "—"}
        </span>
      </div>
    </div>
  );
}
