import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmt, Pill } from "./Pill.jsx";

export default function ExcludedTable({ rows }) {
  if (!rows?.length) return null;

  return (
    <section>
      <div className="sec-h">
        <h2>Excluded from billing — retained for audit</h2>
        <span className="hint">not in billable totals or the summary</span>
      </div>
      <Card variant="app" className="gap-0 py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source Customer</TableHead>
              <TableHead className="text-right">Events</TableHead>
              <TableHead className="text-right">Workers</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.source_customer}>
                <TableCell className="font-semibold text-app-ink">{r.source_customer}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt(r.events)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt(r.workers)}</TableCell>
                <TableCell>
                  <Pill flag="CUSTOMER_EXCLUDED" />
                </TableCell>
                <TableCell className="text-[12.5px] text-app-muted">{r.reason}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </section>
  );
}
