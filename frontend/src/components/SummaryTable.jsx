import { useMemo, useState } from "react";
import { fmt, usd, Pill, Loading } from "./Pill.jsx";

const COLUMNS = [
  { key: "billing_customer", label: "Billing Customer", sortable: true },
  { key: "csm", label: "CSM / Owner" },
  { key: "events", label: "Events", sortable: true, right: true },
  { key: "_bar", label: "Billable / review" },
  { key: "workers", label: "Workers", right: true },
  { key: "_price", label: "Unit price", right: true },
  { key: "status", label: "Status" },
  { key: "expected", label: "Expected total", sortable: true, right: true },
];

export default function SummaryTable({ summary, loading }) {
  const [sort, setSort] = useState({ key: "expected", dir: -1 });

  const rows = useMemo(() => {
    const r = [...(summary?.rows ?? [])];
    r.sort((a, b) => {
      const A = a[sort.key];
      const B = b[sort.key];
      const cmp = typeof A === "string" ? A.localeCompare(B) : A - B;
      return cmp * sort.dir;
    });
    return r;
  }, [summary, sort]);

  if (loading) return <Loading rows={4} />;
  if (!summary?.rows?.length) {
    return <div className="empty">No billable activity in this period.</div>;
  }

  const max = Math.max(1, ...rows.map((r) => r.events));
  const t = summary.totals;

  const click = (key) =>
    setSort((s) => ({
      key,
      dir: s.key === key ? -s.dir : key === "billing_customer" ? 1 : -1,
    }));

  return (
    <div className="card scroll">
      <table>
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                className={[c.right ? "r" : "", c.sortable ? "sortable" : ""].join(" ").trim()}
                onClick={c.sortable ? () => click(c.key) : undefined}
                aria-sort={
                  sort.key === c.key ? (sort.dir > 0 ? "ascending" : "descending") : undefined
                }
              >
                {c.label}{" "}
                {c.sortable && <span className="ar">{sort.key === c.key ? (sort.dir > 0 ? "▲" : "▼") : ""}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.billing_customer}>
              <td className="cust">{r.billing_customer}</td>
              <td className="muted">{r.csm}</td>
              <td className="r num">{fmt(r.events)}</td>
              <td className="bar-cell">
                <div style={{ display: "flex", gap: 2, alignItems: "center" }}>
                  {r.ok > 0 && (
                    <div className="bar" style={{ width: `${Math.round((r.ok / max) * 140)}px` }} />
                  )}
                  {r.review > 0 && (
                    <div
                      className="bar rev"
                      style={{ width: `${Math.round((r.review / max) * 140)}px` }}
                    />
                  )}
                </div>
              </td>
              <td className="r num">{fmt(r.workers)}</td>
              <td className="r num muted">
                {r.unit_prices.length ? r.unit_prices.map(usd).join(" / ") : "—"}
              </td>
              <td>
                {r.status === "MIXED" ? (
                  <>
                    <Pill flag="OK">{r.ok} ok</Pill>{" "}
                    <Pill flag="CSM_CONFIRM_PRICE">{r.review} rev</Pill>
                  </>
                ) : (
                  <Pill flag={r.status} />
                )}
              </td>
              <td className="r money">
                {r.expected > 0 ? (
                  usd(r.expected)
                ) : r.review ? (
                  <span className="faint">pending</span>
                ) : (
                  usd(0)
                )}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td className="cust">Total</td>
            <td />
            <td className="r num">{fmt(t.events)}</td>
            <td />
            <td className="r num">{fmt(t.workers)}</td>
            <td />
            <td className="muted" style={{ fontSize: 12 }}>
              {fmt(t.review)} to review
            </td>
            <td className="r money">{usd(t.expected)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
