import { fmt, usd, Loading } from "./Pill.jsx";

export default function KpiRow({ kpis, loading }) {
  if (loading || !kpis) return <Loading rows={2} />;

  const cards = [
    {
      tone: "accent",
      value: fmt(kpis.billable_events),
      label: "Billable paperwork events",
      foot: `sent in period, deduped · ${fmt(kpis.excluded_events)} excluded`,
    },
    {
      tone: "ok",
      value: usd(kpis.expected_total),
      label: "Confidently billable",
      foot: `${fmt(kpis.priced_events)} priced (${fmt(kpis.confirmed_events)} CSM-confirmed)`,
    },
    {
      tone: "review",
      value: fmt(kpis.awaiting_csm_events),
      label: "Awaiting CSM price",
      foot: `${fmt(kpis.awaiting_csm_accounts)} accounts in review`,
    },
    {
      tone: "",
      value: fmt(kpis.billing_customers),
      label: "Billing customers",
      foot: `${fmt(kpis.workers)} workers`,
    },
  ];

  return (
    <div className="kpis">
      {cards.map((c) => (
        <div key={c.label} className={`kpi ${c.tone}`}>
          <div className="label">{c.label}</div>
          <div className="v num">{c.value}</div>
          <div className="foot">{c.foot}</div>
        </div>
      ))}
    </div>
  );
}
