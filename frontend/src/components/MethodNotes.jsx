import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usd, Pill, Notice, Loading } from "./Pill.jsx";
import Section from "./Section.jsx";

/** The rules panel, driven by the live config tables rather than by prose
 *  hardcoded in the markup — so it cannot drift from what the pipeline does. */
export default function MethodNotes({ config, loading, error }) {
  const mappings = config?.customer_map ?? [];
  const exclusions = config?.exclusions ?? [];
  const splits = config?.entity_split_rules ?? [];
  const threshold = config?.settings?.find((s) => s.key === "price_outlier_threshold")?.value;

  return (
    <Section title="Logic and assumptions" hint="read from the live rule tables">
      {/* The accordion root is laid out as the two-column card grid rather than
          a single stack, so each rule group stays its own panel. */}
      {/* multiple, because these were four independent <details> — opening the
          mapping rules must not collapse the pricing hierarchy. Base UI
          defaults this to false. */}
      <Accordion
        multiple
        defaultValue={["pricing"]}
        className="grid gap-3.5 md:grid-cols-2"
      >
        <AccordionItem value="pricing" className={ITEM}>
          <AccordionTrigger className={TRIGGER}>
            Pricing hierarchy and CSM overrides
          </AccordionTrigger>
          <AccordionContent className={CONTENT}>
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
              A Salesforce price above <Code>{usd(threshold ?? 16)}</Code> is held for review rather
              than billed
            </Rule>
            <Rule k="Salesforce">
              Never modified — overrides live only in the approved-override layer
            </Rule>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="mapping" className={ITEM}>
          <AccordionTrigger className={TRIGGER}>
            Customer mapping, entity splits and exclusions
          </AccordionTrigger>
          <AccordionContent className={CONTENT}>
            {mappings.map((m) => (
              <Rule key={m.source_customer} k={m.source_customer}>
                Billed under <b>{m.billing_customer}</b>
                <span className="text-muted-foreground"> — {m.reason}</span>
              </Rule>
            ))}
            {splits.map((s) => (
              <Rule key={s.id} k={s.source_customer.split(" ")[0]}>
                Contract-name split: <Code>{s.token_b}</Code> → {s.entity_b}, <Code>{s.token_a}</Code>{" "}
                → {s.entity_a}. A contract naming both is resolved by sender (
                {s.senders.map((x) => x.sender_name).join(", ") || "none configured"}), otherwise
                held for review.
              </Rule>
            ))}
            {exclusions.map((e) => (
              <Rule key={e.source_customer} k="Excluded">
                <b>{e.source_customer}</b> — <Code>CUSTOMER_EXCLUDED</Code>, retained for audit
              </Rule>
            ))}
            {/* "Nothing configured" is a claim about the rule tables, so it is
                only made once they have actually been read. A failed or
                in-flight /api/config used to render the same sentence, which
                made a broken request indistinguishable from an empty ruleset. */}
            {error ? (
              <Notice kind="error">
                {error} — rules could not be loaded, so this list is not the live configuration.
              </Notice>
            ) : loading ? (
              <Loading rows={2} />
            ) : (
              !mappings.length &&
              !splits.length &&
              !exclusions.length && (
                <p className="text-muted-foreground">No mapping, split or exclusion rules configured.</p>
              )
            )}
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="population" className={ITEM}>
          <AccordionTrigger className={TRIGGER}>Population, period and dedupe</AccordionTrigger>
          <AccordionContent className={CONTENT}>
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
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="accounts" className={ITEM}>
          <AccordionTrigger className={TRIGGER}>Salesforce accounts and prices</AccordionTrigger>
          <AccordionContent className={CONTENT}>
            {error && <Notice kind="error">{error}</Notice>}
            {loading && <Loading rows={3} />}
            <Table className="text-[12.5px]">
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead>CSM</TableHead>
                  <TableHead className="text-right">Ad Hoc price</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(config?.accounts ?? []).map((a) => (
                  <TableRow key={a.account_id}>
                    <TableCell>
                      {a.name}
                      <br />
                      <span className="font-mono text-[11px] text-muted-foreground/70">{a.account_id}</span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{a.csm || "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {a.adhoc_price == null ? (
                        <span className="text-muted-foreground/70">not configured</span>
                      ) : (
                        usd(a.adhoc_price)
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </Section>
  );
}

// Each panel is its own card, matching the surrounding dashboard rather than
// the flat stacked accordion shadcn ships by default.
const ITEM = "self-start rounded-xl border bg-card px-4 not-last:border-b";
const TRIGGER = "py-3.5 text-sm font-[620] hover:no-underline";
const CONTENT = "pb-4 text-[13.5px] text-foreground/80";

const Code = ({ children }) => (
  <code className="rounded-[5px] bg-muted px-1.5 py-px font-mono text-xs text-foreground">
    {children}
  </code>
);

const Rule = ({ k, children }) => (
  <div className="flex gap-2.5 border-b border-dashed border-border py-2.5 last:border-b-0">
    <div className="w-[150px] flex-none text-[12.5px] text-muted-foreground">{k}</div>
    <div className="text-[13.5px] text-foreground">{children}</div>
  </div>
);
