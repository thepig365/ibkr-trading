import { fmtTime } from "@/lib/format";

interface Signal {
  signal_id: string;
  symbol: string;
  strategy: string;
  direction: string;
  timestamp: string;
  score: number;
  auto_execute: boolean;
  executed: boolean;
  reject_reason: string | null;
  reason: string;
}

export default function SignalQueue({ signals }: { signals: Signal[] }) {
  if (!signals || signals.length === 0) {
    return (
      <div className="card">
        <div className="text-xs uppercase tracking-wider text-muted mb-2">
          Today&apos;s signals
        </div>
        <div className="text-sm text-muted">No signals yet today.</div>
      </div>
    );
  }
  return (
    <div className="card overflow-hidden">
      <div className="text-xs uppercase tracking-wider text-muted mb-2">
        Today&apos;s signals
      </div>
      <table className="data">
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Score</th>
            <th>Status</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <tr key={s.signal_id}>
              <td>{fmtTime(s.timestamp)}</td>
              <td className="font-medium">{s.symbol}</td>
              <td className={s.direction === "LONG" ? "text-accent" : "text-bad"}>
                {s.direction}
              </td>
              <td className="font-mono">{s.score?.toFixed(0)}</td>
              <td>
                {s.executed ? (
                  <span className="badge badge-good">EXEC</span>
                ) : s.reject_reason ? (
                  <span className="badge badge-bad" title={s.reject_reason}>
                    REJ
                  </span>
                ) : (
                  <span className="badge badge-warn">ALERT</span>
                )}
              </td>
              <td className="text-xs text-muted">{s.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
