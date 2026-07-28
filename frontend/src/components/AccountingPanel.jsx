import { useState } from "react";
import { api } from "../api.js";
import { fmt, usd, Notice, Loading } from "./Pill.jsx";

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
    <section>
      <div className="sec-h">
        <h2>Accounting</h2>
        <span className="hint">
          {data.usage_source.describes}
          {!data.usage_source.available && " — not configured"}
        </span>
      </div>

      <div className="acct-grid">
        {cards.map((c) => (
          <div key={c.label} className={`acct-card ${c.tone}`}>
            <div className="v num">{c.value}</div>
            <div className="label">{c.label}</div>
          </div>
        ))}
      </div>

      {notice && <Notice kind={notice.kind}>{notice.text}</Notice>}

      {!data.can_mark_ready && data.ready_blocked_reason && (
        <Notice>Cannot mark ready to bill yet — {data.ready_blocked_reason}.</Notice>
      )}

      <div className="rev-tools">
        <button
          className="btn"
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
        </button>

        <button
          className="btn"
          disabled={!canAct || readOnly || !!busy}
          onClick={() =>
            act("usage", async () => {
              const r = await api.refreshUsage(label);
              return `Usage refreshed: ${r.merge.events_added} new, ${r.merge.events_updated} updated, ${r.merge.events_disqualified} no longer qualifying.`;
            })
          }
        >
          Refresh usage
        </button>

        <button
          className="btn"
          disabled={!canAct || readOnly || !!busy}
          onClick={() =>
            act("pricing", async () => {
              const r = await api.refreshPricing(label);
              return `Pricing refreshed — ${usd(r.totals.expected_amount)} expected.`;
            })
          }
        >
          Refresh pricing
        </button>

        <button
          className="btn"
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
        </button>

        <button
          className="btn"
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
        </button>

        <button
          className="btn"
          disabled={!canAct || readOnly || !!busy || !data.can_mark_ready}
          title={data.can_mark_ready ? "" : data.ready_blocked_reason}
          onClick={() => act("ready", async () => {
            await api.markReady(label);
            return "Period marked Ready to Bill.";
          })}
        >
          Mark ready to bill
        </button>

        <button
          className="btn danger"
          disabled={!canAct || readOnly || !!busy}
          onClick={() => setClosing(true)}
        >
          Close period
        </button>
      </div>

      {/* Closing is the one irreversible action, so it asks for the period
          label rather than a yes/no a stray click could satisfy. */}
      {closing && (
        <div className="confirm-strip">
          <div>
            <b>Close {period.name}?</b> Closed periods are the billed record: automated refreshes
            skip them, pricing changes do not reach back into them, and the customer totals are
            frozen at today's values. Only an administrator can reopen one. Type{" "}
            <b className="mono">{label}</b> to confirm.
          </div>
          <div className="rc-actions">
            <input
              type="text"
              value={confirmText}
              placeholder={label}
              aria-label="Type the period label to confirm"
              onChange={(e) => setConfirmText(e.target.value)}
            />
            <button
              className="btn danger sm"
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
            </button>
            <button
              className="btn ghost sm"
              onClick={() => {
                setClosing(false);
                setConfirmText("");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {preview && (
        <div className="slack-preview">
          <div className="sp-head">
            <b>Slack preview</b>
            <span className={`status-pill s-${preview.mode}`}>{preview.mode}</span>
            {preview.mode !== "live" && (
              <span className="hint">mentions are inert — nobody is notified</span>
            )}
            {preview.already_sent && (
              <span className="hint">a review notification was already sent for this period</span>
            )}
            <button className="btn sm ghost" onClick={() => setPreview(null)}>
              Close
            </button>
          </div>
          <pre>{preview.message}</pre>
        </div>
      )}

      {data.latest_run && (
        <div className="runline">
          Last run #{data.latest_run.id} · {data.latest_run.run_type} · {data.latest_run.status}
          {data.latest_run.finished_at ? ` · ${data.latest_run.finished_at}` : ""}
          {data.latest_run.actor ? ` · ${data.latest_run.actor}` : ""}
          {data.latest_run.error ? ` · ${data.latest_run.error}` : ""}
        </div>
      )}
    </section>
  );
}
