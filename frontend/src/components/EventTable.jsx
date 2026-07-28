import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { api, useApi } from "../api.js";
import { agTheme } from "@/lib/agGrid.js";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fmt, usd, Pill, Dash, Notice } from "./Pill.jsx";

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

// Only these reach SORTABLE in reporting.py; anything else silently falls back
// to source_customer, so the remaining columns stay unsortable in the header
// rather than offering an order the server will not honour.
const SORTABLE = new Set([
  "source_customer",
  "billing_customer",
  "worker_name",
  "paperwork_name",
  "sent_date",
  "signed_date",
  "flag",
]);

const muted = "text-muted-foreground/70";

/** Debounce a value so typing doesn't fire a request per keystroke. */
function useDebounced(value, ms = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

// --- cell renderers ---------------------------------------------------------
// Every renderer has to tolerate `data` being undefined: with the infinite row
// model AG Grid mounts the cells of a block before its rows have arrived.

function BillingCustomerCell({ data }) {
  if (!data) return null;
  const differs = data.billing_customer !== data.source_customer;
  return (
    <>
      {differs ? <b>{data.billing_customer}</b> : <span className={muted}>same</span>}
      {data.customer_mapping_applied && (
        <span
          className="ml-1.5 inline-block rounded-[5px] bg-map-soft px-1.5 py-px align-middle text-[10.5px] font-[650] text-map"
          title={data.mapping_reason}
        >
          MAPPED
        </span>
      )}
    </>
  );
}

function PricingSourceCell({ data }) {
  if (!data) return null;
  return (
    <div className="whitespace-normal text-xs leading-tight text-muted-foreground">
      {data.pricing_source || <span className={muted}>—</span>}
      {data.flag === "CSM_CONFIRMED_PRICE" && (
        <div className={muted}>SF: Not Configured</div>
      )}
    </div>
  );
}

function ChargeCell({ data }) {
  if (!data) return null;
  if (data.charge != null) return usd(data.charge);
  return <span className={muted}>{data.flag === "CSM_CONFIRM_PRICE" ? "pending" : "—"}</span>;
}

const DashCell = ({ value }) => <Dash value={value} />;

const LoadingCell = () => <Skeleton className="h-3.5 w-2/3" />;

const NoRowsOverlay = () => (
  <div className="px-4 py-7 text-center text-[13.5px] text-muted-foreground">
    No events match these filters. Clear the search or pick another status.
  </div>
);

// --- columns ----------------------------------------------------------------
// `field` doubles as the sort key sent to /api/events, so colId and the
// server's SORTABLE keys are the same strings by construction.
const COLUMN_DEFS = [
  {
    field: "source_customer",
    headerName: "Source Customer",
    sort: "asc",
    minWidth: 110,
    flex: 1.3,
    cellClass: "font-[550] text-foreground",
  },
  {
    field: "billing_customer",
    headerName: "Billing Customer",
    minWidth: 110,
    flex: 1.3,
    cellRenderer: BillingCustomerCell,
  },
  {
    field: "salesforce_account",
    headerName: "Salesforce Account",
    minWidth: 100,
    flex: 1,
    cellClass: "text-[12.5px] text-muted-foreground",
    cellRenderer: DashCell,
  },
  { field: "worker_name", headerName: "Worker", minWidth: 100, flex: 1 },
  { field: "paperwork_name", headerName: "Document", minWidth: 110, flex: 1.3 },
  {
    field: "contract_name",
    headerName: "Contract Name",
    minWidth: 100,
    flex: 1,
    cellClass: "text-[12.5px] text-muted-foreground",
    cellRenderer: DashCell,
    tooltipValueGetter: (p) => p.data?.contract_ids || null,
  },
  {
    field: "sent_date",
    headerName: "Sent",
    width: 95,
    cellClass: "tabular-nums text-muted-foreground",
    cellRenderer: DashCell,
  },
  {
    field: "signed_date",
    headerName: "Signed",
    width: 95,
    cellClass: "tabular-nums",
    cellRenderer: DashCell,
  },
  {
    field: "unit_price",
    headerName: "Unit $",
    width: 90,
    type: "rightAligned",
    // ag-right-aligned-cell is repeated because a colDef's cellClass replaces
    // the one the column type contributes rather than merging with it.
    cellClass: "ag-right-aligned-cell tabular-nums text-muted-foreground",
    // A null unit price is "not priced yet", not $0 — usd() would render it as
    // $0 and that reads as a real, free packet.
    valueFormatter: (p) => (p.value == null ? "—" : usd(p.value)),
  },
  {
    field: "pricing_source",
    headerName: "Pricing source",
    minWidth: 100,
    flex: 1,
    cellRenderer: PricingSourceCell,
  },
  {
    field: "flag",
    headerName: "Status",
    width: 155,
    cellRenderer: (p) => (p.data ? <Pill flag={p.value}>{p.data.flag_label}</Pill> : null),
  },
  {
    field: "charge",
    headerName: "Charge",
    width: 100,
    type: "rightAligned",
    cellClass: "ag-right-aligned-cell tabular-nums font-semibold",
    cellRenderer: ChargeCell,
  },
];

export default function EventTable({ period, refreshKey }) {
  const gridRef = useRef(null);
  const [gridReady, setGridReady] = useState(false);
  const [search, setSearch] = useState("");
  const [customer, setCustomer] = useState("");
  const [flag, setFlag] = useState("");
  const [counts, setCounts] = useState(null);
  const [error, setError] = useState(null);

  const debounced = useDebounced(search);

  const customers = useApi(() => api.billingCustomers(period), [period, refreshKey], []);

  // Sorting, filtering and paging all stay on the server — the grid only ever
  // holds one block, so letting AG Grid sort would reorder the visible 100 rows
  // and quietly claim to have sorted all of them.
  const datasource = useMemo(
    () => ({
      getRows: async (params) => {
        const [sort] = params.sortModel ?? [];
        try {
          const data = await api.events({
            period,
            search: debounced,
            billing_customer: customer,
            flag,
            sort: sort?.colId ?? "source_customer",
            direction: sort?.sort ?? "asc",
            limit: params.endRow - params.startRow,
            offset: params.startRow,
          });
          setError(null);
          setCounts({ matched: data.matched, total: data.total });
          params.successCallback(data.rows, data.matched);
          const grid = gridRef.current?.api;
          if (data.matched === 0) grid?.showNoRowsOverlay();
          else grid?.hideOverlay();
        } catch (e) {
          setError(e.message || "Request failed");
          params.failCallback();
        }
      },
    }),
    [period, debounced, customer, flag],
  );

  // Swapping the datasource purges the cache and returns to the first page,
  // which is what a filter change should do. refreshKey is a dependency so a
  // saved override or approval re-reads the current page.
  useEffect(() => {
    if (!gridReady) return;
    gridRef.current?.api.setGridOption("datasource", datasource);
  }, [gridReady, datasource, refreshKey]);

  const customerItems = [
    { value: ALL, label: "All billing customers" },
    ...(customers.data ?? []).map((c) => ({ value: c, label: c })),
  ];
  const flagItems = FLAG_OPTIONS.map(([value, label]) => ({ value, label }));

  const defaultColDef = useMemo(
    () => ({ resizable: true, suppressHeaderMenuButton: true }),
    [],
  );

  // The header's sort arrow is the only thing telling the user a column can be
  // ordered, so it has to agree with what reporting.py will actually accept.
  const columnDefs = useMemo(
    () => COLUMN_DEFS.map((c) => ({ ...c, sortable: SORTABLE.has(c.field) })),
    [],
  );

  const onGridReady = useCallback(() => setGridReady(true), []);

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
          {counts &&
            `${fmt(counts.matched)} matched · ${fmt(counts.total)} events total`}
        </span>
      </div>

      {error && <Notice kind="error">{error}</Notice>}

      <Card className="gap-0 overflow-hidden py-0">
        {/* The grid draws its own scrollbars, so it needs a bounded height;
            color-scheme is set here rather than on :root so only the grid's
            scrollbars follow dark mode, not every control on the page. */}
        <div className="h-[640px] w-full [color-scheme:light_dark]">
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            rowModelType="infinite"
            cacheBlockSize={PAGE}
            maxBlocksInCache={4}
            pagination
            paginationPageSize={PAGE}
            paginationPageSizeSelector={false}
            rowHeight={44}
            headerHeight={40}
            getRowId={(p) => String(p.data.id)}
            tooltipShowDelay={300}
            loadingCellRenderer={LoadingCell}
            noRowsOverlayComponent={NoRowsOverlay}
            onGridReady={onGridReady}
          />
        </div>
      </Card>

      <div className="mt-3 text-xs text-muted-foreground">
        Pricing source shows <b>Salesforce contracted</b>, <b>CSM Confirmed Override</b> (with
        “Salesforce Pricing: Not Configured” retained for audit), or blank while awaiting CSM
        confirmation.
      </div>
    </>
  );
}
