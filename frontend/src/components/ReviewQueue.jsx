import { useRef, useState } from "react";
import { api } from "../api.js";
import { fmt, usd, Pill, Notice, Loading } from "./Pill.jsx";

const todayISO = () => new Date().toISOString().slice(0, 10);

export default function ReviewQueue({ queue, loading, error, onChange, canAct, readOnly, auth }) {
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);

  const exportOverrides = async () => {
    setBusy(true);
    try {
      const payload = await api.exportOverrides();
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "adhoc_csm_pricing_overrides.json";
      a.click();
      URL.revokeObjectURL(a.href);
      setNotice({ kind: "ok", text: `Exported ${payload.overrides.length} approved override(s).` });
    } catch (e) {
      setNotice({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const importOverrides = async (file) => {
    setBusy(true);
    try {
      const parsed = JSON.parse(await file.text());
      const overrides = Array.isArray(parsed) ? parsed : parsed.overrides || [];
      const result = await api.importOverrides({ overrides });
      const skipped = result.skipped.length
        ? ` ${result.skipped.length} skipped (${result.skipped[0].why}).`
        : "";
      setNotice({
        kind: result.imported ? "ok" : "error",
        text: `Imported ${result.imported} approved override(s).${skipped}`,
      });
      onChange();
    } catch (e) {
      setNotice({
        kind: "error",
        text: e instanceof SyntaxError ? "That file isn't valid JSON." : e.message,
      });
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Loading rows={3} />;

  return (
    <>
      <div className="rev-tools">
        <button
          className="btn"
          disabled={busy || !canAct}
          onClick={() => fileRef.current?.click()}
        >
          Import approved overrides
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importOverrides(f);
            e.target.value = "";
          }}
        />
        <button className="btn" disabled={busy} onClick={exportOverrides}>
          Export approved overrides
        </button>
        <span className="count" style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--muted)" }}>
          {queue ? `${queue.confirmed} of ${queue.total} accounts confirmed` : ""}
        </span>
      </div>

      {notice && <Notice kind={notice.kind}>{notice.text}</Notice>}
      {error && <Notice kind="error">{error}</Notice>}

      {queue?.accounts?.length ? (
        <div className="revgrid">
          {queue.accounts.map((a) => (
            <ReviewCard
              key={a.sf_account_id}
              account={a}
              onChange={onChange}
              onNotice={setNotice}
              canAct={canAct}
              readOnly={readOnly}
              auth={auth}
            />
          ))}
        </div>
      ) : (
        <div className="empty">
          No accounts awaiting a CSM price. Every billable account has a price.
        </div>
      )}
    </>
  );
}

function ReviewCard({ account: a, onChange, onNotice, canAct, readOnly, auth }) {
  const [mode, setMode] = useState(null); // 'zero' | 'price'
  const [price, setPrice] = useState("");
  const [effective, setEffective] = useState(todayISO());
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [pending, setPending] = useState(null); // the value awaiting final confirmation
  const [saving, setSaving] = useState(false);

  // Who is confirming is no longer a text box. It used to be, which meant the
  // audit trail recorded what somebody typed rather than who they were.
  const actor = auth?.user?.email ?? "";

  const review = () => {
    if (!mode) return setErr("Choose $0 or enter a price.");
    let value = 0;
    if (mode === "price") {
      value = Number.parseFloat(price);
      if (!Number.isFinite(value) || value < 0) return setErr("Enter a valid price (0 or more).");
    }
    setErr("");
    setPending(value);
  };

  const commit = async () => {
    setSaving(true);
    try {
      await api.saveOverride({
        sf_account_id: a.sf_account_id,
        confirmed_unit_price: pending,
        effective_date: effective || todayISO(),
        confirm: true,
        note: note.trim(),
        billing_customer: a.billing_customer,
        sf_account_name: a.sf_account_name,
      });
      setPending(null);
      onNotice({
        kind: "ok",
        text: `${a.billing_customer} confirmed at ${usd(pending)} per packet.`,
      });
      onChange();
    } catch (e) {
      setErr(e.message);
      setPending(null);
    } finally {
      setSaving(false);
    }
  };

  const revoke = async () => {
    setSaving(true);
    try {
      await api.revokeOverride(a.sf_account_id);
      onNotice({
        kind: "",
        text: `${a.billing_customer} returned to CSM price review. The revoked price stays in the audit trail.`,
      });
      onChange();
    } catch (e) {
      onNotice({ kind: "error", text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const o = a.override;

  return (
    <div className={`rcard ${o ? "confirmed" : ""}`}>
      <div className="rc-head">
        <div>
          <div className="rc-cust">{a.billing_customer}</div>
          <div className="rc-sub">
            SF: {a.sf_account_name} · <span className="mono">{a.sf_account_id}</span>
          </div>
        </div>
        <Pill flag={o ? "CSM_CONFIRMED_PRICE" : "CSM_CONFIRM_PRICE"} />
      </div>

      <div className="rc-meta">
        <div>
          <span className="k">CSM</span>
          {a.csm}
        </div>
        <div>
          <span className="k">Billable packets</span>
          {fmt(a.packets)} · {fmt(a.workers)} workers
        </div>
        <div>
          <span className="k">Salesforce pricing</span>
          Not Configured
        </div>
        <div>
          <span className="k">Current expected price</span>
          {o ? (
            `${usd(o.confirmed_unit_price)} /packet`
          ) : (
            <b style={{ color: "var(--review)" }}>Needs confirmation</b>
          )}
        </div>
      </div>

      <div className="rc-reason">Reason: {a.reason}</div>

      {o ? (
        <>
          <div className="audit">
            <div className="a">
              <span className="muted">Salesforce pricing</span>
              <b>Not Configured</b>
            </div>
            <div className="a">
              <span className="muted">Billing price used</span>
              <b>{usd(o.confirmed_unit_price)} /packet</b>
            </div>
            <div className="a">
              <span className="muted">Pricing source</span>
              <b>CSM Confirmed Override</b>
            </div>
            <div className="a">
              <span className="muted">Expected for period</span>
              <b>{usd(a.period_expected)}</b>
            </div>
          </div>
          <div className="confirmed-by">
            Confirmed by <b>{o.confirmed_by}</b> · {(o.confirmed_at || "").replace("T", " ").slice(0, 16)} ·
            effective {o.effective_date}
            {o.note ? ` · “${o.note}”` : ""}
          </div>
          <div className="rc-actions">
            <button
              className="btn sm ghost"
              disabled={saving || !canAct || readOnly}
              onClick={revoke}
            >
              Revoke and re-confirm
            </button>
          </div>
        </>
      ) : (
        <div className="rc-form">
          <label className="opt">
            <input
              type="radio"
              name={`opt-${a.sf_account_id}`}
              checked={mode === "zero"}
              onChange={() => setMode("zero")}
            />
            <span>
              Confirm this customer is billed <b>$0</b> per Ad Hoc Paperwork packet
            </span>
          </label>
          <label className="opt">
            <input
              type="radio"
              name={`opt-${a.sf_account_id}`}
              checked={mode === "price"}
              onChange={() => setMode("price")}
            />
            <span>Confirm another price:</span>
            <span className="price-in">
              <span className="dol">$</span>
              <input
                type="number"
                min="0"
                step="0.01"
                placeholder="0.00"
                aria-label="Unit price per packet"
                value={price}
                onChange={(e) => {
                  setPrice(e.target.value);
                  setMode("price");
                }}
              />
              <span className="muted">/packet</span>
            </span>
          </label>

          <div className="row2">
            <div className="fld">
              <span className="k">Confirmed by</span>
              <div className="fld-static" title="Taken from your session, not typed">
                {actor || <span className="faint">sign in to confirm</span>}
              </div>
            </div>
            <div className="fld">
              <span className="k">Effective date</span>
              <input
                type="date"
                value={effective}
                onChange={(e) => setEffective(e.target.value)}
              />
            </div>
          </div>

          <div className="fld">
            <span className="k">Note (optional)</span>
            <input
              type="text"
              placeholder="e.g. contract rate per CSM"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>

          {err && <div className="err">{err}</div>}

          <div className="rc-actions">
            <button
              className="btn primary sm"
              onClick={review}
              disabled={saving || !canAct || readOnly}
              title={
                readOnly
                  ? "This period is closed"
                  : canAct
                    ? ""
                    : "Sign in to confirm a price"
              }
            >
              Review and confirm…
            </button>
          </div>

          {pending !== null && (
            <div className="confirm-strip">
              <div>
                <b>Confirm:</b> bill <b>{usd(pending)}</b> per packet for{" "}
                <b>{a.billing_customer}</b> — {a.packets} packets ={" "}
                <b>{usd(pending * a.packets)}</b> this period. By <b>{actor}</b>, effective{" "}
                {effective}. This saves an approved override and applies to future periods too;
                Salesforce is not modified and closed periods do not change.
              </div>
              <div className="rc-actions">
                <button className="btn ok sm" onClick={commit} disabled={saving}>
                  {saving ? "Saving…" : "Yes, save override"}
                </button>
                <button className="btn ghost sm" onClick={() => setPending(null)} disabled={saving}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
