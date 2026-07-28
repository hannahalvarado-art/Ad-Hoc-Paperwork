import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Notice, StatusPill } from "./Pill.jsx";

/** Month switcher plus who you are.
 *
 * The status pill is not decoration: a CLOSED period is read-only and every
 * control below is disabled, so the reason has to be visible at the point where
 * someone would otherwise wonder why nothing responds.
 */
export default function PeriodBar({ periods, value, onChange, period, auth, onSignOut }) {
  const list = periods?.periods ?? [];
  const readOnly = period?.read_only;

  // Base UI renders the selected item's label from `items`, so the closed
  // suffix has to be part of the label rather than appended in the option.
  const items = list.map((p) => ({
    value: p.label,
    label: `${p.name}${p.status === "CLOSED" ? " · closed" : ""}`,
  }));

  return (
    <Card
      className="mb-3.5 flex-row flex-wrap items-center gap-3 rounded-xl px-3.5 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <Label htmlFor="period-select" className="text-xs font-normal text-muted-foreground">
          Billing period
        </Label>
        <Select items={items} value={value} onValueChange={onChange}>
          <SelectTrigger id="period-select" size="sm" aria-label="Billing period">
            <SelectValue placeholder="—" />
          </SelectTrigger>
          <SelectContent>
            {items.map((i) => (
              <SelectItem key={i.value} value={i.value}>
                {i.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {period && (
          <>
            <StatusPill status={period.status} />
            <span className="text-xs text-muted-foreground/70">
              {period.period_start} → {period.period_end} · by sent date
            </span>
          </>
        )}
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-2.5">
        {readOnly && (
          <span className="rounded-full border border-review bg-review-soft px-2.5 py-0.5 text-xs text-review">
            Read-only — this period is closed
          </span>
        )}
        {auth?.authenticated ? (
          <>
            <span
              className="inline-flex items-center gap-1.5 text-[12.5px] text-foreground/80"
              title={auth.user.email}
            >
              {auth.user.email}
              {auth.user.is_admin && (
                <span className="rounded-full border border-input px-1.5 py-px text-[10.5px] tracking-[.04em] text-muted-foreground uppercase">
                  admin
                </span>
              )}
            </span>
            {auth.dev_mode ? (
              <span
                className="rounded-full border border-review bg-review-soft px-1.5 py-px text-[10.5px] tracking-[.04em] text-review uppercase"
                title="ADHOC_DEV_AUTH is set — not a real session"
              >
                dev auth
              </span>
            ) : (
              <Button variant="appGhost" size="appSm" onClick={onSignOut}>
                Sign out
              </Button>
            )}
          </>
        ) : (
          // nativeButton={false} because this is a real navigation to the
          // OAuth endpoint, so it has to stay an <a>. Base UI otherwise warns
          // that it lost native button semantics.
          <Button
            variant="primary"
            size="appSm"
            nativeButton={false}
            render={<a href="/api/auth/login">Sign in</a>}
          />
        )}
      </div>
    </Card>
  );
}

/** Shown in place of the action bar when nobody is signed in. */
export function SignInNotice({ auth }) {
  if (!auth || auth.authenticated) return null;
  return (
    <Notice>
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
          <span className="font-mono">GOOGLE_CLIENT_ID</span>,{" "}
          <span className="font-mono">GOOGLE_CLIENT_SECRET</span> and{" "}
          <span className="font-mono">ADHOC_SESSION_SECRET</span>.
        </>
      )}
    </Notice>
  );
}
