"use client";

import { fmtUsd } from "@/lib/format";

interface Position {
  trade_id: string;
  symbol: string;
  direction: string;
  entry_price: number;
  shares: number;
  stop_loss: number;
  take_profit: number;
  trailing_activated: boolean;
  trailing_stop: number | null;
  moved_to_breakeven: boolean;
  scale_in_count: number;
  entry_time: string;
}

export default function PositionsPanel({
  positions,
  onClose,
}: {
  positions: Position[];
  onClose: (symbol: string) => Promise<void>;
}) {
  if (positions.length === 0) {
    return (
      <div className="card">
        <div className="text-sm text-muted">No open positions.</div>
      </div>
    );
  }
  return (
    <div className="card overflow-hidden">
      <div className="text-xs uppercase tracking-wider text-muted mb-2">
        Open positions
      </div>
      <table className="data">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Shares</th>
            <th>Entry</th>
            <th>Stop</th>
            <th>Target</th>
            <th>Trail</th>
            <th>Scale-ins</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.trade_id}>
              <td className="font-medium">{p.symbol}</td>
              <td>
                <span
                  className={
                    p.direction === "LONG"
                      ? "text-accent"
                      : "text-bad"
                  }
                >
                  {p.direction}
                </span>
              </td>
              <td>{p.shares}</td>
              <td>{fmtUsd(p.entry_price)}</td>
              <td>
                {fmtUsd(p.stop_loss)}
                {p.moved_to_breakeven ? (
                  <span className="badge badge-muted ml-2">BE</span>
                ) : null}
              </td>
              <td>{fmtUsd(p.take_profit)}</td>
              <td>
                {p.trailing_activated && p.trailing_stop != null
                  ? fmtUsd(p.trailing_stop)
                  : "—"}
              </td>
              <td>{p.scale_in_count}</td>
              <td>
                <button
                  type="button"
                  onClick={() => onClose(p.symbol)}
                  className="btn-danger"
                >
                  Close
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
