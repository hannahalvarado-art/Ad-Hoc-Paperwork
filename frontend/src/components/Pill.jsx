import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

export const fmt = (n) => Number(n ?? 0).toLocaleString("en-US");

export const usd = (n) =>
  `$${Number(n ?? 0).toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;

// Badge variant per billing flag. The colour is load-bearing — amber means a
// CSM still has to price the account, red means no Salesforce account at all —
// so these map onto the semantic variants in ui/badge.jsx rather than shadcn's
// neutral default/secondary.
export const FLAGS = {
  OK: { variant: "ok", label: "Billable" },
  CSM_CONFIRMED_PRICE: { variant: "conf", label: "CSM Confirmed" },
  CSM_CONFIRM_PRICE: { variant: "review", label: "CSM Confirm Price" },
  PRICE_OUTLIER_REVIEW: { variant: "outlier", label: "Price outlier" },
  ENTITY_BILLING_REVIEW: { variant: "map", label: "Entity billing review" },
  MISSING_SALESFORCE_ACCOUNT: { variant: "missing", label: "Missing account" },
  CUSTOMER_EXCLUDED: { variant: "excluded", label: "Customer excluded" },
};

export function Pill({ flag, children }) {
  const f = FLAGS[flag];
  return (
    <Badge variant={f?.variant ?? "excluded"} dot className="gap-1.5 text-[11.5px] font-semibold">
      {children ?? f?.label ?? flag}
    </Badge>
  );
}

// Period and run status. Uppercase, bordered, no dot — visually distinct from
// the flag pills so a period status never reads as a per-row billing flag.
const STATUS_VARIANTS = {
  in_review: "statusReview",
  ready_to_bill: "statusOk",
  closed: "statusNeutral",
  processing: "statusAccent",
  failed: "statusMissing",
  dry_run: "statusConf",
  dev: "statusReview",
  live: "statusMissing",
};

export function StatusPill({ status, children }) {
  const key = (status || "").toLowerCase();
  return (
    <Badge
      variant={STATUS_VARIANTS[key] ?? "statusNeutral"}
      className="text-[11px] font-semibold"
    >
      {children ?? status?.replace(/_/g, " ")}
    </Badge>
  );
}

export const Dash = ({ value }) =>
  value === "" || value == null ? <span className="text-app-faint">—</span> : value;

export function Notice({ kind = "", children }) {
  if (!children) return null;
  return (
    // block, not Alert's default grid: these messages are flowing prose with
    // inline <b>/<span> in them, and grid would break each onto its own row.
    <Alert
      variant={kind === "error" ? "error" : kind === "ok" ? "ok" : "surface"}
      className="mb-3.5 block px-3.5 py-2.5 text-[13px]"
    >
      {children}
    </Alert>
  );
}

export function Loading({ rows = 3 }) {
  return (
    <div className="grid gap-2 py-4">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-3.5" style={{ width: `${100 - i * 12}%` }} />
      ))}
    </div>
  );
}
