export const fmt = (n) => Number(n ?? 0).toLocaleString("en-US");

export const usd = (n) =>
  `$${Number(n ?? 0).toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;

export const FLAGS = {
  OK: { cls: "p-ok", label: "Billable" },
  CSM_CONFIRMED_PRICE: { cls: "p-conf", label: "CSM Confirmed" },
  CSM_CONFIRM_PRICE: { cls: "p-csm", label: "CSM Confirm Price" },
  PRICE_OUTLIER_REVIEW: { cls: "p-out", label: "Price outlier" },
  ENTITY_BILLING_REVIEW: { cls: "p-ov", label: "Entity billing review" },
  MISSING_SALESFORCE_ACCOUNT: { cls: "p-mis", label: "Missing account" },
  CUSTOMER_EXCLUDED: { cls: "p-exc", label: "Customer excluded" },
};

export function Pill({ flag, children }) {
  const f = FLAGS[flag];
  return <span className={`pill ${f ? f.cls : ""}`}>{children ?? f?.label ?? flag}</span>;
}

export const Dash = ({ value }) =>
  value === "" || value == null ? <span className="faint">—</span> : value;

export function Notice({ kind = "", children }) {
  if (!children) return null;
  return <div className={`notice ${kind}`}>{children}</div>;
}

export function Loading({ rows = 3 }) {
  return (
    <div style={{ display: "grid", gap: 8, padding: "16px 0" }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${100 - i * 12}%` }} />
      ))}
    </div>
  );
}
