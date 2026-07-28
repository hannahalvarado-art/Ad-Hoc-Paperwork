import { useMemo, useState } from "react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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

const ALL_CSMS = "__all__";

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
    return <div className="px-4 py-7 text-center text-[13.5px] text-muted-foreground">No billable activity in this period.</div>;
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
  const csmItems = [
    { value: ALL_CSMS, label: "All CSMs" },
    ...(data.csms ?? []).map((n) => ({ value: n, label: n })),
  ];

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2.5">
        {FILTERS.map((f) => (
          <Button
            key={f.key}
            variant={filter === f.key ? "chipOn" : "chip"}
            size="chip"
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </Button>
        ))}
        {(data.csms?.length ?? 0) > 0 && (
          <Select
            items={csmItems}
            value={csm || ALL_CSMS}
            onValueChange={(v) => setCsm(v === ALL_CSMS ? "" : v)}
          >
            <SelectTrigger size="sm" aria-label="Filter by CSM">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {csmItems.map((i) => (
                <SelectItem key={i.value} value={i.value}>
                  {i.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {rows.length} of {data.rows.length} customers
        </span>
      </div>

      {notice && <Notice kind={notice.kind}>{notice.text}</Notice>}

      <Card className="gap-0 py-0">
        <Table>
          <TableHeader>
            <TableRow>
              {COLUMNS.map((c) => (
                <TableHead
                  key={c.key}
                  className={[
                    c.right ? "text-right" : "",
                    c.sortable ? "cursor-pointer select-none" : "",
                  ]
                    .join(" ")
                    .trim()}
                  onClick={c.sortable ? () => click(c.key) : undefined}
                  aria-sort={
                    sort.key === c.key ? (sort.dir > 0 ? "ascending" : "descending") : undefined
                  }
                >
                  {c.label}{" "}
                  {c.sortable && (
                    <span className="text-[10px] opacity-50">
                      {sort.key === c.key ? (sort.dir > 0 ? "▲" : "▼") : ""}
                    </span>
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
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
                <TableRow
                  key={id}
                  className={r.review_status === "CUSTOMER_EXCLUDED" ? "opacity-60" : ""}
                >
                  <TableCell className="font-semibold text-foreground">
                    {r.billing_customer}
                    {r.source_customers?.length > 1 && (
                      <div className="mt-px text-[11px] text-muted-foreground/70">
                        via {r.source_customers.join(", ")}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {r.salesforce_account || "—"}
                    {r.salesforce_account_id && (
                      <div className="mt-px font-mono text-[11px] text-muted-foreground/70">
                        {r.salesforce_account_id}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{r.csm}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fmt(r.billable_packets)}
                    {r.excluded_packets > 0 && (
                      <div className="mt-px text-[11px] text-muted-foreground/70">
                        {fmt(r.excluded_packets)} excluded
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {r.unit_prices?.length ? r.unit_prices.map(usd).join(" / ") : "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {r.pricing_source}
                    {r.pricing_status === "CSM_CONFIRMED_PRICE" && (
                      <div className="mt-px text-[11px] text-muted-foreground/70">
                        Salesforce: {r.sf_pricing_status}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <Pill flag={pillFor(r)}>{REVIEW_LABEL[r.review_status] ?? r.review_status}</Pill>
                    {r.blocking_exceptions?.length > 0 && (
                      <div className="mt-px text-[11px] text-muted-foreground/70">
                        {r.blocking_exceptions.join(", ")}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {r.expected_amount > 0 ? (
                      usd(r.expected_amount)
                    ) : r.review_status === "CSM_REVIEW_REQUIRED" ? (
                      <span className="text-muted-foreground/70">pending</span>
                    ) : (
                      usd(0)
                    )}
                  </TableCell>
                  <TableCell>
                    <label
                      className={`flex items-start gap-2 text-[12.5px] ${
                        why ? "cursor-not-allowed" : "cursor-pointer"
                      }`}
                      title={why}
                    >
                      <Checkbox
                        className="mt-px"
                        checked={!!r.good_to_bill}
                        disabled={busy || !!why}
                        onCheckedChange={(next) => toggle(r, next)}
                      />
                      <span>
                        {r.good_to_bill ? (
                          <>
                            <b>Approved</b>
                            <div className="mt-px text-[11px] text-muted-foreground/70">
                              {r.approved_by} · {(r.approved_at || "").slice(0, 16)}
                            </div>
                          </>
                        ) : why ? (
                          <span className="text-muted-foreground/70">{why}</span>
                        ) : (
                          "Good to Bill"
                        )}
                      </span>
                    </label>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell className="font-semibold text-foreground">Total</TableCell>
              <TableCell />
              <TableCell />
              <TableCell className="text-right tabular-nums">
                {fmt(t.total_billable_packets)}
              </TableCell>
              <TableCell />
              <TableCell />
              <TableCell className="text-xs text-muted-foreground">
                {fmt(t.customers_good_to_bill)} approved · {fmt(t.customers_not_yet_approved)} not
                yet
              </TableCell>
              <TableCell className="text-right font-semibold tabular-nums">
                {usd(t.expected_amount)}
              </TableCell>
              <TableCell />
            </TableRow>
          </TableFooter>
        </Table>
      </Card>
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
