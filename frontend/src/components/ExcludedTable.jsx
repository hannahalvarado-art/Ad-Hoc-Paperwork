import { fmt, Pill } from "./Pill.jsx";

export default function ExcludedTable({ rows }) {
  if (!rows?.length) return null;

  return (
    <section>
      <div className="sec-h">
        <h2>Excluded from billing — retained for audit</h2>
        <span className="hint">not in billable totals or the summary</span>
      </div>
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Source Customer</th>
              <th className="r">Events</th>
              <th className="r">Workers</th>
              <th>Status</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.source_customer}>
                <td className="cust">{r.source_customer}</td>
                <td className="r num">{fmt(r.events)}</td>
                <td className="r num">{fmt(r.workers)}</td>
                <td>
                  <Pill flag="CUSTOMER_EXCLUDED" />
                </td>
                <td className="muted" style={{ fontSize: 12.5 }}>
                  {r.reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
