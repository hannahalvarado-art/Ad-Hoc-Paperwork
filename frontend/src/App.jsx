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
import Section from "./components/Section.jsx";
import { Alert } from "@/components/ui/alert";

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
    <div className="mx-auto max-w-[1280px] px-6 pt-8 pb-20">
      <header>
        <div className="mb-1.5 font-serif text-[15px] text-muted-foreground italic">
          Ad Hoc Paperwork · monthly billing · {activePeriod?.billing_type_label ?? ""}
        </div>
        <h1 className="text-[30px] leading-[1.15] font-[680] tracking-[-0.02em]">
          {activePeriod?.name ?? "Billing"} — Ad Hoc Paperwork Reconciliation
        </h1>
        <p className="mt-2.5 max-w-[72ch] text-foreground/80">
          Expected usage-based charges for completed Ad Hoc Paperwork signature packets, by{" "}
          <b>sent date</b>. Where Salesforce has no Ad Hoc price, the account routes to a{" "}
          <b>CSM price review</b>; confirmed prices persist and apply to future periods
          automatically. <b>Good to Bill is per month</b> and must be reconfirmed each period.
        </p>
      </header>

      <Alert className="my-[22px] mb-[30px] flex items-start gap-3 rounded-xl border-input bg-primary/10 px-4 py-3 text-[13.5px] text-foreground/80">
        <span className="mt-[5px] size-2.5 flex-none rounded-full bg-ok shadow-[0_0_0_3px_var(--ok-soft)]" />
        <div>
          <b className="text-foreground">No invoices are issued and Salesforce is never modified.</b>{" "}
          CSM confirmations live in a separate approved-override layer. Pricing order: Salesforce
          contracted price → approved CSM override → otherwise CSM Confirm Price. An unconfirmed
          price is never treated as $0. Closed periods are immutable.
        </div>
      </Alert>

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

      <Section
        title="CSM price review"
        hint="accounts with no Salesforce Ad Hoc price — confirm to release for billing"
      >
        <ReviewQueue
          queue={queue.data}
          loading={queue.loading}
          error={queue.error}
          onChange={refresh}
          canAct={canAct}
          readOnly={readOnly}
          auth={auth.data}
        />
      </Section>

      <Section
        title="Summary by billing customer"
        hint="unique billable packets · unit price · pricing source · Good to Bill"
      >
        <SummaryTable
          data={summary.data}
          loading={summary.loading}
          error={summary.error}
          period={activePeriod}
          canAct={canAct}
          onChange={refresh}
        />
      </Section>

      <ExcludedTable rows={excluded.data} />

      <Section
        title="Paperwork events"
        hint="source and contract preserved; pricing hierarchy applied"
      >
        <EventTable period={activePeriod?.label ?? period} refreshKey={refreshKey} />
      </Section>

      {/* config.error is deliberately not part of `fatal`: the rules panel is
          not needed to read the numbers, so a failed /api/config degrades that
          one section instead of replacing the whole dashboard with a banner. It
          does have to be reported *somewhere*, though — it used to be dropped
          entirely and the panel just claimed nothing was configured. */}
      <MethodNotes config={config.data} loading={config.loading} error={config.error} />

      <div className="mt-10 flex flex-wrap gap-x-[22px] gap-y-1.5 border-t pt-[18px] text-[12.5px] text-muted-foreground">
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
