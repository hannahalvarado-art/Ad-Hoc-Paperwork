import { usd, Pill, Notice, Loading } from "./Pill.jsx";

/** The rules panel, driven by the live config tables rather than by prose
 *  hardcoded in the markup — so it cannot drift from what the pipeline does. */
export default function MethodNotes({ config, loading, error }) {
  const mappings = config?.customer_map ?? [];
  const exclusions = config?.exclusions ?? [];
  const splits = config?.entity_split_rules ?? [];
  const threshold = config?.settings?.find((s) => s.key === "price_outlier_threshold")?.value;

  return (
    <section>
      <div className="sec-h">
        <h2>Logic and assumptions</h2>
        <span className="hint">read from the live rule tables</span>
      </div>
      <div className="method">
        <details open>
          <summary>Pricing hierarchy and CSM overrides</summary>
          <div className="body">
            <Rule k="1 · Salesforce">
              Contracted Ad Hoc Paperwork price from a Closed-Won opportunity → use it
            </Rule>
            <Rule k="2 · CSM override">
              No Salesforce Ad Hoc product but an approved override exists →{" "}
              <Pill flag="CSM_CONFIRMED_PRICE" />, bill at the confirmed price (may be $0)
            </Rule>
            <Rule k="3 · Neither">
              <Pill flag="CSM_CONFIRM_PRICE" /> — never auto-$0, never a borrowed price
            </Rule>
            <Rule k="Override record">
              Account ID, billing customer, confirmed unit price, confirmed by, timestamp, source,
              note and effective date — stored server-side and reused across periods
            </Rule>
            <Rule k="Outlier guard">
              A Salesforce price above <code>{usd(threshold ?? 16)}</code> is held for review rather
              than billed
            </Rule>
            <Rule k="Salesforce">
              Never modified — overrides live only in the approved-override layer
            </Rule>
          </div>
        </details>

        <details>
          <summary>Customer mapping, entity splits and exclusions</summary>
          <div className="body">
            {mappings.map((m) => (
              <Rule key={m.source_customer} k={m.source_customer}>
                Billed under <b>{m.billing_customer}</b>
                <span className="muted"> — {m.reason}</span>
              </Rule>
            ))}
            {splits.map((s) => (
              <Rule key={s.id} k={s.source_customer.split(" ")[0]}>
                Contract-name split: <code>{s.token_b}</code> → {s.entity_b}, <code>{s.token_a}</code>{" "}
                → {s.entity_a}. A contract naming both is resolved by sender (
                {s.senders.map((x) => x.sender_name).join(", ") || "none configured"}), otherwise
                held for review.
              </Rule>
            ))}
            {exclusions.map((e) => (
              <Rule key={e.source_customer} k="Excluded">
                <b>{e.source_customer}</b> — <code>CUSTOMER_EXCLUDED</code>, retained for audit
              </Rule>
            ))}
            {/* "Nothing configured" is a claim about the rule tables, so it is
                only made once they have actually been read. A failed or
                in-flight /api/config used to render the same sentence, which
                made a broken request indistinguishable from an empty ruleset. */}
            {error ? (
              <Notice kind="error">
                {error} — rules could not be loaded, so this list is not the live
                configuration.
              </Notice>
            ) : loading ? (
              <Loading rows={2} />
            ) : (
              !mappings.length &&
              !splits.length &&
              !exclusions.length && (
                <p className="muted">No mapping, split or exclusion rules configured.</p>
              )
            )}
          </div>
        </details>

        <details>
          <summary>Population, period and dedupe</summary>
          <div className="body">
            <Rule k="Include">
              Completed signature packets, excluding W-4, disciplinary, read-only, job-contract and
              test or internal documents
            </Rule>
            <Rule k="Period">
              <b>Sent date</b> in Los Angeles time; an active contract is not required
            </Rule>
            <Rule k="Dedupe">
              Worker + document + sent + signed; only contract-ID differences collapse, and contract
              names and IDs are preserved
            </Rule>
          </div>
        </details>

        <details>
          <summary>Salesforce accounts and prices</summary>
          <div className="body">
            {error && <Notice kind="error">{error}</Notice>}
            {loading && <Loading rows={3} />}
            <table style={{ fontSize: 12.5 }}>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>CSM</th>
                  <th className="r">Ad Hoc price</th>
                </tr>
              </thead>
              <tbody>
                {(config?.accounts ?? []).map((a) => (
                  <tr key={a.account_id}>
                    <td>
                      {a.name}
                      <br />
                      <span className="faint mono" style={{ fontSize: 11 }}>
                        {a.account_id}
                      </span>
                    </td>
                    <td className="muted">{a.csm || "—"}</td>
                    <td className="r num">
                      {a.adhoc_price == null ? (
                        <span className="faint">not configured</span>
                      ) : (
                        usd(a.adhoc_price)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </section>
  );
}

const Rule = ({ k, children }) => (
  <div className="rule-row">
    <div className="k">{k}</div>
    <div className="v">{children}</div>
  </div>
);
