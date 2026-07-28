import { Pill } from "./Pill.jsx";

/** Month switcher plus who you are.
 *
 * The status pill is not decoration: a CLOSED period is read-only and every
 * control below is disabled, so the reason has to be visible at the point where
 * someone would otherwise wonder why nothing responds.
 */
export default function PeriodBar({ periods, value, onChange, period, auth, onSignOut }) {
  const list = periods?.periods ?? [];
  const readOnly = period?.read_only;

  return (
    <div className="periodbar">
      <div className="pb-left">
        <label className="pb-label" htmlFor="period-select">
          Billing period
        </label>
        <select
          id="period-select"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label="Billing period"
        >
          {!list.length && <option value="">—</option>}
          {list.map((p) => (
            <option key={p.label} value={p.label}>
              {p.name}
              {p.status === "CLOSED" ? " · closed" : ""}
            </option>
          ))}
        </select>

        {period && (
          <>
            <span className={`status-pill s-${(period.status || "").toLowerCase()}`}>
              {period.status?.replace(/_/g, " ")}
            </span>
            <span className="pb-range">
              {period.period_start} → {period.period_end} · by sent date
            </span>
          </>
        )}
      </div>

      <div className="pb-right">
        {readOnly && <span className="pb-ro">Read-only — this period is closed</span>}
        {auth?.authenticated ? (
          <>
            <span className="pb-user" title={auth.user.email}>
              {auth.user.email}
              {auth.user.is_admin && <span className="pb-admin">admin</span>}
            </span>
            {auth.dev_mode ? (
              <span className="pb-dev" title="ADHOC_DEV_AUTH is set — not a real session">
                dev auth
              </span>
            ) : (
              <button className="btn sm ghost" onClick={onSignOut}>
                Sign out
              </button>
            )}
          </>
        ) : (
          <a className="btn sm primary" href="/api/auth/login">
            Sign in
          </a>
        )}
      </div>
    </div>
  );
}

/** Shown in place of the action bar when nobody is signed in. */
export function SignInNotice({ auth }) {
  if (!auth || auth.authenticated) return null;
  return (
    <div className="notice">
      {auth.configured ? (
        <>
          <b>You are not signed in.</b> The numbers below are visible, but confirming a price or
          marking a customer Good to Bill records who did it — so those actions need a{" "}
          <b>@{auth.allowed_domain}</b> sign-in.
        </>
      ) : (
        <>
          <b>Authentication is not configured.</b> Price confirmations and Good to Bill approvals
          are disabled, because there is no way to record who performed them. Set{" "}
          <span className="mono">GOOGLE_CLIENT_ID</span>,{" "}
          <span className="mono">GOOGLE_CLIENT_SECRET</span> and{" "}
          <span className="mono">ADHOC_SESSION_SECRET</span>.
        </>
      )}
    </div>
  );
}
