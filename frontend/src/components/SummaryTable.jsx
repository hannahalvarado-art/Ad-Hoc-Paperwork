import { useMemo, useState } from "react";
import { api } from "../api.js";
import { fmt, usd, Pill, Notice, Loading } from "./Pill.jsx";

const COLUMNS = [
  { key: "billing_customer", label: "Billing Customer", sortable: true },
  { key: "salesforce_account", label: "Salesforce Account" },
  { key: "csm", label: "CSM / Owner" },
  { key: "billable_packets", label: "Packets", sortable: true, right: true },
  { key: "unit_price", label: "Unit price", right: true },
  { key: "pricing_source", label: "Pricing source" },
  { key: "review_status", label: "Status" },
  { key: "expected_amount", label: "Expected", sortable: true, right: true },
  { key: "good_to_bill", label: "Good to Bill" },
];

const REVIEW_LABEL = {
  CSM_REVIEW_REQUIRED: "CSM review required",
  BLOCKED: "Blocked",
  GOOD_TO_BILL: "Good to Bill",
  READY_TO_BILL: "Ready to bill",
  CUSTOMER_EXCLUDED: "Customer excluded",
};

const FILTERS = [
  { key: "", label: "All customers" },
  { key: "good", label: "Good to Bill" },
  { key: "notapproved", label: "Not approved" },
  { key: "review", label: "CSM review required" },
  { key: "blocked", label: "Blocked" },
];

export default function SummaryTable({ data, loading, error, period, canAct, onChange }) {
  const [sort, setSort] = useState({ key: "expected_amount", dir: -1 });
  const [filter, setFilter] = useState("");
  const [csm, setCsm] = useState("");
  const [notice, setNotice] = useState(null);
  const [pending, setPending] = useState("");

  const rows = useMemo(() => {
    let r = [...(data?.rows ?? [])];
    if (filter === "good") r = r.filter((x) => x.good_to_bill);
    if (filter === "notapproved") r = r.filter((x) => !x.good_to_bill && x.good_to_bill_eligible);
    if (filter === "review") r = r.filter((x) => x.review_status === "CSM_REVIEW_REQUIRED");
    if (filter === "blocked") r = r.filter((x) => x.blocking_exceptions?.length);
    if (csm) r = r.filter((x) => x.csm === csm);
    r.sort((a, b) => {
      const A = a[sort.key] ?? 0;
      const B = b[sort.key] ?? 0;
      const cmp = typeof A === "string" ? A.localeCompare(String(B)) : A - B;
      return cmp * sort.dir;
    });
    return r;
  }, [data, sort, filter, csm]);

  if (loading) return <Loading rows={4} />;
  if (error) return <Notice kind="error">{error}</Notice>;
  if (!data?.rows?.length) {
    return <div className="empty">No billable activity in this period.</div>;
  }

  const click = (key) =>
    setSort((s) => ({
      key,
      dir: s.key === key ? -s.dir : key === "billing_customer" ? 1 : -1,
    }));

  const toggle = async (row, next) => {
    const id = `${row.billing_customer}|${row.salesforce_account_id}`;
    setPending(id);
    setNotice(null);
    try {
      await api.setApproval({
        period: period.label,
        billing_customer: row.billing_customer,
        salesforce_account_id: row.salesforce_account_id,
        good_to_bill: next,
      });
      setNotice({
        kind: "ok",
        text: next
          ? `${row.billing_customer} marked Good to Bill for ${period.name}.`
          : `Good to Bill removed for ${row.billing_customer}.`,
      });
      onChange();
    } catch (e) {
      setNotice({ kind: "error", text: e.message });
    } finally {
      setPending("");
    }
  };

  const t = data.totals;

  return (
    <>
      <div className="filters">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`chip ${filter === f.key ? "on" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        {(data.csms?.length ?? 0) > 0 && (
          <select value={csm} onChange={(e) => setCsm(e.target.value)} aria-label="Filter by CSM">
            <option value="">All CSMs</option>
            {data.csms.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        )}
        <span className="count">
          {rows.length} of {data.rows.length} customers
        </span>
      </div>

      {notice && <Notice kind={notice.kind}>{notice.text}</Notice>}

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
                  {c.sortable && (
                    <span className="ar">
                      {sort.key === c.key ? (sort.dir > 0 ? "▲" : "▼") : ""}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const id = `${r.billing_customer}|${r.salesforce_account_id}`;
              const busy = pending === id;
              // Disabled for a reason the user can read, rather than silently
              // inert: the title carries the specific blocker.
              const why = r.good_to_bill_eligible
                ? period.read_only
                  ? "This period is closed"
                  : canAct
                    ? ""
                    : "Sign in to approve"
                : r.good_to_bill_blocked_reason;

              return (
                <tr key={id} className={r.review_status === "CUSTOMER_EXCLUDED" ? "excluded" : ""}>
                  <td className="cust">
                    {r.billing_customer}
                    {r.source_customers?.length > 1 && (
                      <div className="sub">via {r.source_customers.join(", ")}</div>
                    )}
                  </td>
                  <td className="muted">
                    {r.salesforce_account || "—"}
                    {r.salesforce_account_id && (
                      <div className="sub mono">{r.salesforce_account_id}</div>
                    )}
                  </td>
                  <td className="muted">{r.csm}</td>
                  <td className="r num">
                    {fmt(r.billable_packets)}
                    {r.excluded_packets > 0 && (
                      <div className="sub">{fmt(r.excluded_packets)} excluded</div>
                    )}
                  </td>
                  <td className="r num muted">
                    {r.unit_prices?.length ? r.unit_prices.map(usd).join(" / ") : "—"}
                  </td>
                  <td className="muted small">
                    {r.pricing_source}
                    {r.pricing_status === "CSM_CONFIRMED_PRICE" && (
                      <div className="sub">Salesforce: {r.sf_pricing_status}</div>
                    )}
                  </td>
                  <td>
                    <Pill flag={pillFor(r)}>{REVIEW_LABEL[r.review_status] ?? r.review_status}</Pill>
                    {r.blocking_exceptions?.length > 0 && (
                      <div className="sub">{r.blocking_exceptions.join(", ")}</div>
                    )}
                  </td>
                  <td className="r money">
                    {r.expected_amount > 0 ? (
                      usd(r.expected_amount)
                    ) : r.review_status === "CSM_REVIEW_REQUIRED" ? (
                      <span className="faint">pending</span>
                    ) : (
                      usd(0)
                    )}
                  </td>
                  <td>
                    <label className="gtb" title={why}>
                      <input
                        type="checkbox"
                        checked={!!r.good_to_bill}
                        disabled={busy || !!why}
                        onChange={(e) => toggle(r, e.target.checked)}
                      />
                      <span>
                        {r.good_to_bill ? (
                          <>
                            <b>Approved</b>
                            <div className="sub">
                              {r.approved_by} · {(r.approved_at || "").slice(0, 16)}
                            </div>
                          </>
                        ) : why ? (
                          <span className="faint">{why}</span>
                        ) : (
                          "Good to Bill"
                        )}
                      </span>
                    </label>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td className="cust">Total</td>
              <td />
              <td />
              <td className="r num">{fmt(t.total_billable_packets)}</td>
              <td />
              <td />
              <td className="muted" style={{ fontSize: 12 }}>
                {fmt(t.customers_good_to_bill)} approved · {fmt(t.customers_not_yet_approved)} not yet
              </td>
              <td className="r money">{usd(t.expected_amount)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}

function pillFor(r) {
  if (r.review_status === "CUSTOMER_EXCLUDED") return "CUSTOMER_EXCLUDED";
  if (r.review_status === "BLOCKED") return "MISSING_SALESFORCE_ACCOUNT";
  if (r.review_status === "CSM_REVIEW_REQUIRED") return "CSM_CONFIRM_PRICE";
  if (r.review_status === "GOOD_TO_BILL") return "OK";
  return "CSM_CONFIRMED_PRICE";
}
