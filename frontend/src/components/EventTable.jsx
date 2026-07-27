import { useEffect, useMemo, useState } from "react";
import { api, useApi } from "../api.js";
import { fmt, usd, Pill, Dash, Notice, Loading } from "./Pill.jsx";

const PAGE = 100;

const FLAG_OPTIONS = [
  ["", "All statuses"],
  ["OK", "Billable (OK)"],
  ["CSM_CONFIRMED_PRICE", "CSM Confirmed"],
  ["CSM_CONFIRM_PRICE", "CSM Confirm Price"],
  ["PRICE_OUTLIER_REVIEW", "Price outlier"],
  ["ENTITY_BILLING_REVIEW", "Entity billing review"],
  ["MISSING_SALESFORCE_ACCOUNT", "Missing account"],
  ["CUSTOMER_EXCLUDED", "Customer excluded"],
];

const COLUMNS = [
  { key: "source_customer", label: "Source Customer", sortable: true },
  { key: "billing_customer", label: "Billing Customer", sortable: true },
  { key: null, label: "Salesforce Account" },
  { key: "worker_name", label: "Worker", sortable: true },
  { key: "paperwork_name", label: "Document", sortable: true },
  { key: null, label: "Contract Name" },
  { key: "sent_date", label: "Sent", sortable: true },
  { key: "signed_date", label: "Signed", sortable: true },
  { key: null, label: "Unit $", right: true },
  { key: null, label: "Pricing source" },
  { key: "flag", label: "Status", sortable: true },
  { key: null, label: "Charge", right: true },
];

/** Debounce a value so typing doesn't fire a request per keystroke. */
function useDebounced(value, ms = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function EventTable({ period, refreshKey }) {
  const [search, setSearch] = useState("");
  const [customer, setCustomer] = useState("");
  const [flag, setFlag] = useState("");
  const [sort, setSort] = useState({ key: "source_customer", dir: "asc" });
  const [offset, setOffset] = useState(0);

  const debounced = useDebounced(search);

  // Any filter change returns to the first page.
  useEffect(() => setOffset(0), [debounced, customer, flag, sort]);

  const customers = useApi(() => api.billingCustomers(period), [period, refreshKey], []);

  const { data, loading, error } = useApi(
    () =>
      api.events({
        period,
        search: debounced,
        billing_customer: customer,
        flag,
        sort: sort.key,
        direction: sort.dir,
        limit: PAGE,
        offset,
      }),
    [period, debounced, customer, flag, sort.key, sort.dir, offset, refreshKey],
  );

  const pageInfo = useMemo(() => {
    if (!data) return null;
    const from = data.matched === 0 ? 0 : data.offset + 1;
    const to = Math.min(data.offset + data.limit, data.matched);
    return { from, to, matched: data.matched, total: data.total };
  }, [data]);

  const click = (key) =>
    key &&
    setSort((s) => ({ key, dir: s.key === key && s.dir === "asc" ? "desc" : "asc" }));

  return (
    <>
      <div className="filters">
        <input
          type="search"
          placeholder="Search worker, document, contract, source…"
          aria-label="Search events"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          aria-label="Billing customer"
          value={customer}
          onChange={(e) => setCustomer(e.target.value)}
        >
          <option value="">All billing customers</option>
          {(customers.data ?? []).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select aria-label="Status" value={flag} onChange={(e) => setFlag(e.target.value)}>
          {FLAG_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <span className="count">
          {pageInfo &&
            `${fmt(pageInfo.from)}–${fmt(pageInfo.to)} of ${fmt(pageInfo.matched)} matched · ${fmt(
              pageInfo.total,
            )} events total`}
        </span>
      </div>

      {error && <Notice kind="error">{error}</Notice>}

      <div className="card scroll">
        {loading && !data ? (
          <div style={{ padding: 16 }}>
            <Loading rows={6} />
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th
                    key={c.label}
                    className={[c.right ? "r" : "", c.sortable ? "sortable" : ""].join(" ").trim()}
                    onClick={c.sortable ? () => click(c.key) : undefined}
                    aria-sort={
                      sort.key === c.key
                        ? sort.dir === "asc"
                          ? "ascending"
                          : "descending"
                        : undefined
                    }
                  >
                    {c.label}{" "}
                    {c.sortable && (
                      <span className="ar">
                        {sort.key === c.key ? (sort.dir === "asc" ? "▲" : "▼") : ""}
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.rows ?? []).map((r) => {
                const differs = r.billing_customer !== r.source_customer;
                return (
                  <tr key={r.id}>
                    <td className="cust" style={{ fontWeight: 550 }}>
                      {r.source_customer}
                    </td>
                    <td>
                      {differs ? <b>{r.billing_customer}</b> : <span className="faint">same</span>}
                      {r.customer_mapping_applied && (
                        <span className="tag" title={r.mapping_reason}>
                          MAPPED
                        </span>
                      )}
                    </td>
                    <td className="muted" style={{ fontSize: 12.5 }}>
                      <Dash value={r.salesforce_account} />
                    </td>
                    <td>{r.worker_name}</td>
                    <td>{r.paperwork_name}</td>
                    <td className="muted" style={{ fontSize: 12.5 }} title={r.contract_ids || ""}>
                      <Dash value={r.contract_name} />
                    </td>
                    <td className="num muted">
                      <Dash value={r.sent_date} />
                    </td>
                    <td className="num">
                      <Dash value={r.signed_date} />
                    </td>
                    <td className="r num muted">
                      {r.unit_price != null ? usd(r.unit_price) : <span className="faint">—</span>}
                    </td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {r.pricing_source || <span className="faint">—</span>}
                      {r.flag === "CSM_CONFIRMED_PRICE" && (
                        <>
                          <br />
                          <span className="faint">SF: Not Configured</span>
                        </>
                      )}
                    </td>
                    <td>
                      <Pill flag={r.flag}>{r.flag_label}</Pill>
                    </td>
                    <td className="r money">
                      {r.charge != null ? (
                        usd(r.charge)
                      ) : r.flag === "CSM_CONFIRM_PRICE" ? (
                        <span className="faint">pending</span>
                      ) : (
                        <span className="faint">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {data?.rows?.length === 0 && (
                <tr>
                  <td colSpan={COLUMNS.length}>
                    <div className="empty">
                      No events match these filters. Clear the search or pick another status.
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      <div className="pager">
        <button
          className="btn sm"
          disabled={offset === 0 || loading}
          onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
        >
          Previous
        </button>
        <button
          className="btn sm"
          disabled={!data || offset + PAGE >= data.matched || loading}
          onClick={() => setOffset((o) => o + PAGE)}
        >
          Next
        </button>
        <span className="spacer">
          Pricing source shows <b>Salesforce contracted</b>, <b>CSM Confirmed Override</b> (with
          “Salesforce Pricing: Not Configured” retained for audit), or blank while awaiting CSM
          confirmation.
        </span>
      </div>
    </>
  );
}
