import { Card } from "@/components/ui/card";
import { fmt, usd, Loading } from "./Pill.jsx";

// Tone drives only the figure's colour. The card itself stays neutral so a
// row of four doesn't read as four different kinds of thing.
const TONE = {
  accent: "text-primary",
  ok: "text-ok",
  review: "text-review",
  "": "text-foreground",
};

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
    <div className="mb-4 grid grid-cols-2 gap-3.5 md:grid-cols-4">
      {cards.map((c) => (
        <Card key={c.label} className="gap-0 px-[18px] py-4">
          <div className="text-[11px] font-[650] tracking-[.09em] text-muted-foreground uppercase">
            {c.label}
          </div>
          <div
            className={`mt-2.5 text-3xl leading-none font-bold tracking-[-.02em] tabular-nums ${TONE[c.tone]}`}
          >
            {c.value}
          </div>
          <div className="mt-2 text-[12.5px] text-muted-foreground">{c.foot}</div>
        </Card>
      ))}
    </div>
  );
}
