import { useRef, useState } from "react";
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
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
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
      <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
        <Button
          variant="surface"
          size="app"
          disabled={busy || !canAct}
          onClick={() => fileRef.current?.click()}
        >
          Import approved overrides
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importOverrides(f);
            e.target.value = "";
          }}
        />
        <Button variant="surface" size="app" disabled={busy} onClick={exportOverrides}>
          Export approved overrides
        </Button>
        <span className="ml-auto text-xs text-app-muted">
          {queue ? `${queue.confirmed} of ${queue.total} accounts confirmed` : ""}
        </span>
      </div>

      {notice && <Notice kind={notice.kind}>{notice.text}</Notice>}
      {error && <Notice kind="error">{error}</Notice>}

      {queue?.accounts?.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(380px,1fr))] gap-3.5">
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
        <div className="px-4 py-7 text-center text-[13.5px] text-app-muted">
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
    <Card
      variant="app"
      className={`gap-3 px-[18px] py-4 ${o ? "border-conf shadow-[0_0_0_1px_var(--conf-soft),var(--shadow)]" : ""}`}
    >
      <div className="flex items-start justify-between gap-2.5">
        <div>
          <div className="text-[15px] font-[660]">{a.billing_customer}</div>
          <div className="mt-0.5 text-xs text-app-muted">
            SF: {a.sf_account_name} · <span className="font-mono">{a.sf_account_id}</span>
          </div>
        </div>
        <Pill flag={o ? "CSM_CONFIRMED_PRICE" : "CSM_CONFIRM_PRICE"} />
      </div>

      <div className="grid grid-cols-2 gap-x-3.5 gap-y-1.5 text-[12.5px]">
        <div>
          <span className="block text-[10.5px] tracking-[.04em] text-app-faint uppercase">CSM</span>
          {a.csm}
        </div>
        <div>
          <span className="block text-[10.5px] tracking-[.04em] text-app-faint uppercase">
            Billable packets
          </span>
          {fmt(a.packets)} · {fmt(a.workers)} workers
        </div>
        <div>
          <span className="block text-[10.5px] tracking-[.04em] text-app-faint uppercase">
            Salesforce pricing
          </span>
          Not Configured
        </div>
        <div>
          <span className="block text-[10.5px] tracking-[.04em] text-app-faint uppercase">
            Current expected price
          </span>
          {o ? (
            `${usd(o.confirmed_unit_price)} /packet`
          ) : (
            <b className="text-review">Needs confirmation</b>
          )}
        </div>
      </div>

      <div className="rounded-lg bg-review-soft px-2.5 py-2 text-xs font-medium text-review">
        Reason: {a.reason}
      </div>

      {o ? (
        <>
          <div className="grid gap-0.5 rounded-[9px] bg-app-surface-2 px-3 py-2.5 text-xs">
            {[
              ["Salesforce pricing", "Not Configured"],
              ["Billing price used", `${usd(o.confirmed_unit_price)} /packet`],
              ["Pricing source", "CSM Confirmed Override"],
              ["Expected for period", usd(a.period_expected)],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2.5">
                <span className="text-app-muted">{k}</span>
                <b className="tabular-nums">{v}</b>
              </div>
            ))}
          </div>
          <div className="text-xs text-app-muted">
            Confirmed by <b>{o.confirmed_by}</b> ·{" "}
            {(o.confirmed_at || "").replace("T", " ").slice(0, 16)} · effective {o.effective_date}
            {o.note ? ` · “${o.note}”` : ""}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="appGhost"
              size="appSm"
              disabled={saving || !canAct || readOnly}
              onClick={revoke}
            >
              Revoke and re-confirm
            </Button>
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-2.5 border-t border-app-border pt-3">
          <RadioGroup value={mode ?? ""} onValueChange={setMode} className="gap-2.5">
            <div className="flex items-center gap-2.5 text-[13px]">
              <RadioGroupItem value="zero" id={`zero-${a.sf_account_id}`} />
              {/* block, not Label's default flex: the sentence has inline <b>
                  in it and flex would space each fragment as its own item. */}
              <Label
                htmlFor={`zero-${a.sf_account_id}`}
                className="block leading-normal font-normal"
              >
                Confirm this customer is billed <b>$0</b> per Ad Hoc Paperwork packet
              </Label>
            </div>
            <div className="flex flex-wrap items-center gap-2.5 text-[13px]">
              <RadioGroupItem value="price" id={`price-${a.sf_account_id}`} />
              <Label
                htmlFor={`price-${a.sf_account_id}`}
                className="block leading-normal font-normal"
              >
                Confirm another price:
              </Label>
              <span className="flex items-center gap-1.5">
                <span className="font-semibold text-app-muted">$</span>
                <Input
                  type="number"
                  min="0"
                  step="0.01"
                  placeholder="0.00"
                  aria-label="Unit price per packet"
                  className="w-[120px]"
                  value={price}
                  onChange={(e) => {
                    setPrice(e.target.value);
                    setMode("price");
                  }}
                />
                <span className="text-app-muted">/packet</span>
              </span>
            </div>
          </RadioGroup>

          <div className="grid grid-cols-2 gap-2.5">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold text-app-muted">Confirmed by</span>
              <div
                className="border-b border-dashed border-app-border-strong py-1.5 text-[13px] text-app-ink-2"
                title="Taken from your session, not typed"
              >
                {actor || <span className="text-app-faint">sign in to confirm</span>}
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold text-app-muted">Effective date</span>
              <Input
                type="date"
                value={effective}
                onChange={(e) => setEffective(e.target.value)}
              />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-app-muted">Note (optional)</span>
            <Input
              type="text"
              placeholder="e.g. contract rate per CSM"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>

          {err && <div className="text-xs text-missing">{err}</div>}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="appSm"
              onClick={review}
              disabled={saving || !canAct || readOnly}
              title={
                readOnly ? "This period is closed" : canAct ? "" : "Sign in to confirm a price"
              }
            >
              Review and confirm…
            </Button>
          </div>

          {/* Saving an override is durable and applies to future periods, so
              the final step is a modal rather than an inline strip that can
              scroll out of view mid-decision. */}
          <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>Confirm {a.billing_customer}</DialogTitle>
                <DialogDescription>
                  Bill <b>{usd(pending ?? 0)}</b> per packet — {a.packets} packets ={" "}
                  <b>{usd((pending ?? 0) * a.packets)}</b> this period. By <b>{actor}</b>, effective{" "}
                  {effective}. This saves an approved override and applies to future periods too;
                  Salesforce is not modified and closed periods do not change.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button
                  variant="appGhost"
                  size="app"
                  onClick={() => setPending(null)}
                  disabled={saving}
                >
                  Cancel
                </Button>
                <Button variant="ok" size="app" onClick={commit} disabled={saving}>
                  {saving ? "Saving…" : "Yes, save override"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      )}
    </Card>
  );
}
