import { useState } from "react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { fmt, usd, Notice, Loading, StatusPill } from "./Pill.jsx";
import Section from "./Section.jsx";

const TONE = {
  ok: "text-ok",
  review: "text-review",
  accent: "text-primary",
  "": "text-foreground",
};

/** Accounting's counts and the manual controls.
 *
 * Every control here is disabled on a closed period rather than hidden: hiding
 * them would make a closed month look like a broken one.
 */
export default function AccountingPanel({ data, loading, error, period, canAct, onChange }) {
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState(null);
  const [preview, setPreview] = useState(null);
  const [closing, setClosing] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  if (loading) return <Loading rows={3} />;
  if (error) return <Notice kind="error">{error}</Notice>;
  if (!data) return null;

  const t = data.totals;
  const readOnly = period?.read_only;
  const label = period?.label;

  const act = async (name, fn) => {
    setBusy(name);
    setNotice(null);
    try {
      const result = await fn();
      setNotice({ kind: "ok", text: typeof result === "string" ? result : "Done." });
      onChange();
    } catch (e) {
      setNotice({ kind: "error", text: e.message });
    } finally {
      setBusy("");
    }
  };

  const cards = [
    { label: "Ready for CSM review", value: t.customers_ready_for_review, tone: "review" },
    { label: "Good to Bill", value: t.customers_good_to_bill, tone: "ok" },
    { label: "Not yet approved", value: t.customers_not_yet_approved, tone: "" },
    { label: "Blocked by pricing", value: t.blocked_by_pricing, tone: "review" },
    { label: "Blocked by exceptions", value: t.blocked_by_other_exceptions, tone: "review" },
    { label: "Unique billable packets", value: fmt(t.total_billable_packets), tone: "accent" },
    { label: "Expected billing known", value: usd(t.expected_amount), tone: "ok" },
    { label: "Unresolved exceptions", value: t.unresolved_exceptions, tone: "" },
  ];

  return (
    <Section
      title="Accounting"
      hint={
        <>
          {data.usage_source.describes}
          {!data.usage_source.available && " — not configured"}
        </>
      }
    >
      <div className="mb-3.5 grid grid-cols-2 gap-2.5 md:grid-cols-4">
        {cards.map((c) => (
          <Card key={c.label} className="gap-0 rounded-[10px] px-3.5 py-3">
            <div className={`text-[22px] font-[650] tabular-nums ${TONE[c.tone]}`}>{c.value}</div>
            <div className="mt-0.5 text-[11.5px] text-muted-foreground">{c.label}</div>
          </Card>
        ))}
      </div>

      {notice && <Notice kind={notice.kind}>{notice.text}</Notice>}

      {!data.can_mark_ready && data.ready_blocked_reason && (
        <Notice>Cannot mark ready to bill yet — {data.ready_blocked_reason}.</Notice>
      )}

      <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
        <Button
          variant="surface"
          size="app"
          disabled={!canAct || readOnly || !!busy}
          onClick={() =>
            act("run", async () => {
              const r = await api.runPeriod({
                year: period.year,
                month: period.month,
                notify: false,
                refresh_usage: true,
              });
              return `Run ${r.run_id}: ${r.events} events (${r.merge.events_added} new, ${r.merge.events_updated} updated, ${r.merge.events_disqualified} no longer qualifying).`;
            })
          }
        >
          {busy === "run" ? "Running…" : "Run / re-run period"}
        </Button>

        <Button
          variant="surface"
          size="app"
          disabled={!canAct || readOnly || !!busy}
          onClick={() =>
            act("usage", async () => {
              const r = await api.refreshUsage(label);
              return `Usage refreshed: ${r.merge.events_added} new, ${r.merge.events_updated} updated, ${r.merge.events_disqualified} no longer qualifying.`;
            })
          }
        >
          Refresh usage
        </Button>

        <Button
          variant="surface"
          size="app"
          disabled={!canAct || readOnly || !!busy}
          onClick={() =>
            act("pricing", async () => {
              const r = await api.refreshPricing(label);
              return `Pricing refreshed — ${usd(r.totals.expected_amount)} expected.`;
            })
          }
        >
          Refresh pricing
        </Button>

        <Button
          variant="surface"
          size="app"
          disabled={!!busy}
          onClick={async () => {
            setBusy("preview");
            try {
              setPreview(await api.notificationPreview(label));
            } catch (e) {
              setNotice({ kind: "error", text: e.message });
            } finally {
              setBusy("");
            }
          }}
        >
          Preview Slack message
        </Button>

        <Button
          variant="surface"
          size="app"
          disabled={!canAct || !!busy}
          onClick={() =>
            act("notify", async () => {
              const r = await api.notify(label);
              return r.status === "sent"
                ? `Notification ${r.mode === "dry_run" ? "rendered (dry run — nothing sent)" : `sent to ${r.channel}`}.`
                : `Not sent: ${r.error || r.status}`;
            })
          }
        >
          Resend review notification
        </Button>

        <Button
          variant="surface"
          size="app"
          disabled={!canAct || readOnly || !!busy || !data.can_mark_ready}
          title={data.can_mark_ready ? "" : data.ready_blocked_reason}
          onClick={() =>
            act("ready", async () => {
              await api.markReady(label);
              return "Period marked Ready to Bill.";
            })
          }
        >
          Mark ready to bill
        </Button>

        <Button
          variant="danger"
          size="app"
          disabled={!canAct || readOnly || !!busy}
          onClick={() => setClosing(true)}
        >
          Close period
        </Button>
      </div>

      {/* Closing is the one irreversible action, so it asks for the period
          label rather than a yes/no a stray click could satisfy. A modal makes
          that deliberate: it takes focus and cannot be scrolled past. */}
      <Dialog
        open={closing}
        onOpenChange={(open) => {
          setClosing(open);
          if (!open) setConfirmText("");
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Close {period?.name}?</DialogTitle>
            <DialogDescription>
              Closed periods are the billed record: automated refreshes skip them, pricing changes
              do not reach back into them, and the customer totals are frozen at today's values.
              Only an administrator can reopen one.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <label htmlFor="close-confirm" className="text-[12.5px] text-foreground/80">
              Type <b className="font-mono">{label}</b> to confirm.
            </label>
            <Input
              id="close-confirm"
              value={confirmText}
              placeholder={label}
              aria-label="Type the period label to confirm"
              onChange={(e) => setConfirmText(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              variant="appGhost"
              size="app"
              onClick={() => {
                setClosing(false);
                setConfirmText("");
              }}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              size="app"
              disabled={confirmText !== label || !!busy}
              onClick={() =>
                act("close", async () => {
                  await api.closePeriod(label, { confirm: confirmText });
                  setClosing(false);
                  setConfirmText("");
                  return `${period.name} is closed.`;
                })
              }
            >
              Yes, close it
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!preview} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex flex-wrap items-center gap-2.5">
              Slack preview
              {preview && <StatusPill status={preview.mode} />}
            </DialogTitle>
            <DialogDescription>
              {preview?.mode !== "live" && "Mentions are inert — nobody is notified. "}
              {preview?.already_sent &&
                "A review notification was already sent for this period."}
            </DialogDescription>
          </DialogHeader>
          <pre className="max-h-[60vh] overflow-auto rounded-[10px] border border-border bg-muted p-3.5 font-mono text-[12.5px] break-words whitespace-pre-wrap text-foreground/80">
            {preview?.message}
          </pre>
        </DialogContent>
      </Dialog>

      {data.latest_run && (
        <div className="mt-2.5 text-xs text-muted-foreground/70">
          Last run #{data.latest_run.id} · {data.latest_run.run_type} · {data.latest_run.status}
          {data.latest_run.finished_at ? ` · ${data.latest_run.finished_at}` : ""}
          {data.latest_run.actor ? ` · ${data.latest_run.actor}` : ""}
          {data.latest_run.error ? ` · ${data.latest_run.error}` : ""}
        </div>
      )}
    </Section>
  );
}
