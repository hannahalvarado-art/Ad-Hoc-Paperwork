import { useEffect, useMemo, useState } from "react";
import { api, useApi } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmt, usd, Pill, Dash, Notice, Loading } from "./Pill.jsx";

const PAGE = 100;

// Base UI treats "" as "nothing selected", which would render the placeholder
// instead of "All …". A sentinel keeps the empty choice selectable, and it is
// translated back to "" before it reaches the query string.
const ALL = "__all__";

const FLAG_OPTIONS = [
  [ALL, "All statuses"],
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

  const customerItems = [
    { value: ALL, label: "All billing customers" },
    ...(customers.data ?? []).map((c) => ({ value: c, label: c })),
  ];
  const flagItems = FLAG_OPTIONS.map(([value, label]) => ({ value, label }));

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2.5">
        <Input
          type="search"
          className="min-w-[230px] flex-none"
          placeholder="Search worker, document, contract, source…"
          aria-label="Search events"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Select
          items={customerItems}
          value={customer || ALL}
          onValueChange={(v) => setCustomer(v === ALL ? "" : v)}
        >
          <SelectTrigger size="sm" aria-label="Billing customer">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {customerItems.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          items={flagItems}
          value={flag || ALL}
          onValueChange={(v) => setFlag(v === ALL ? "" : v)}
        >
          <SelectTrigger size="sm" aria-label="Status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {flagItems.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="ml-auto text-xs text-muted-foreground">
          {pageInfo &&
            `${fmt(pageInfo.from)}–${fmt(pageInfo.to)} of ${fmt(pageInfo.matched)} matched · ${fmt(
              pageInfo.total,
            )} events total`}
        </span>
      </div>

      {error && <Notice kind="error">{error}</Notice>}

      <Card className="gap-0 py-0">
        {loading && !data ? (
          <div className="p-4">
            <Loading rows={6} />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                {COLUMNS.map((c) => (
                  <TableHead
                    key={c.label}
                    className={[
                      c.right ? "text-right" : "",
                      c.sortable ? "cursor-pointer select-none" : "",
                    ]
                      .join(" ")
                      .trim()}
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
                      <span className="text-[10px] opacity-50">
                        {sort.key === c.key ? (sort.dir === "asc" ? "▲" : "▼") : ""}
                      </span>
                    )}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data?.rows ?? []).map((r) => {
                const differs = r.billing_customer !== r.source_customer;
                return (
                  <TableRow key={r.id}>
                    <TableCell className="font-[550] text-foreground">{r.source_customer}</TableCell>
                    <TableCell>
                      {differs ? (
                        <b>{r.billing_customer}</b>
                      ) : (
                        <span className="text-muted-foreground/70">same</span>
                      )}
                      {r.customer_mapping_applied && (
                        <span
                          className="ml-1.5 inline-block rounded-[5px] bg-map-soft px-1.5 py-px align-middle text-[10.5px] font-[650] text-map"
                          title={r.mapping_reason}
                        >
                          MAPPED
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-[12.5px] text-muted-foreground">
                      <Dash value={r.salesforce_account} />
                    </TableCell>
                    <TableCell>{r.worker_name}</TableCell>
                    <TableCell>{r.paperwork_name}</TableCell>
                    <TableCell
                      className="text-[12.5px] text-muted-foreground"
                      title={r.contract_ids || ""}
                    >
                      <Dash value={r.contract_name} />
                    </TableCell>
                    <TableCell className="tabular-nums text-muted-foreground">
                      <Dash value={r.sent_date} />
                    </TableCell>
                    <TableCell className="tabular-nums">
                      <Dash value={r.signed_date} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {r.unit_price != null ? (
                        usd(r.unit_price)
                      ) : (
                        <span className="text-muted-foreground/70">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {r.pricing_source || <span className="text-muted-foreground/70">—</span>}
                      {r.flag === "CSM_CONFIRMED_PRICE" && (
                        <>
                          <br />
                          <span className="text-muted-foreground/70">SF: Not Configured</span>
                        </>
                      )}
                    </TableCell>
                    <TableCell>
                      <Pill flag={r.flag}>{r.flag_label}</Pill>
                    </TableCell>
                    <TableCell className="text-right font-semibold tabular-nums">
                      {r.charge != null ? (
                        usd(r.charge)
                      ) : r.flag === "CSM_CONFIRM_PRICE" ? (
                        <span className="text-muted-foreground/70">pending</span>
                      ) : (
                        <span className="text-muted-foreground/70">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
              {data?.rows?.length === 0 && (
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={COLUMNS.length}>
                    <div className="px-4 py-7 text-center text-[13.5px] text-muted-foreground">
                      No events match these filters. Clear the search or pick another status.
                    </div>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </Card>

      <div className="mt-3 flex items-center gap-2.5 text-xs text-muted-foreground">
        <Button
          variant="surface"
          size="appSm"
          disabled={offset === 0 || loading}
          onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
        >
          Previous
        </Button>
        <Button
          variant="surface"
          size="appSm"
          disabled={!data || offset + PAGE >= data.matched || loading}
          onClick={() => setOffset((o) => o + PAGE)}
        >
          Next
        </Button>
        <span className="ml-auto">
          Pricing source shows <b>Salesforce contracted</b>, <b>CSM Confirmed Override</b> (with
          “Salesforce Pricing: Not Configured” retained for audit), or blank while awaiting CSM
          confirmation.
        </span>
      </div>
    </>
  );
}
