"use client";

import { fmtUsd, fmtNum, fmtDateTime } from "@/lib/format";

export interface TradeRowData {
  trade_id: string;
  symbol: string;
  strategy: string;
  direction: string;
  entry_price: number;
  entry_time: string;
  entry_shares: number;
  exit_price: number | null;
  exit_time: string | null;
  exit_reason: string | null;
  realized_pnl: number | null;
  realized_r: number | null;
  status: string;
}

export default function TradeRow({
  trade,
  onClick,
}: {
  trade: TradeRowData;
  onClick: (trade_id: string) => void;
}) {
  const positive = (trade.realized_pnl ?? 0) >= 0;
  const pnlClass =
    trade.realized_pnl == null
      ? "text-slate-200"
      : positive
      ? "text-accent"
      : "text-bad";
  return (
    <tr
      className="cursor-pointer hover:bg-slate-800/40"
      onClick={() => onClick(trade.trade_id)}
    >
      <td className="font-medium">{trade.symbol}</td>
      <td className={trade.direction === "LONG" ? "text-accent" : "text-bad"}>
        {trade.direction}
      </td>
      <td>{fmtDateTime(trade.entry_time)}</td>
      <td>{fmtUsd(trade.entry_price)}</td>
      <td>{trade.exit_price != null ? fmtUsd(trade.exit_price) : "—"}</td>
      <td className={pnlClass}>{fmtUsd(trade.realized_pnl)}</td>
      <td className={pnlClass}>
        {trade.realized_r == null ? "—" : `${fmtNum(trade.realized_r, 2)}R`}
      </td>
      <td className="text-xs text-muted">{trade.exit_reason || trade.status}</td>
      <td className="w-32">
        <Sparkline tradeId={trade.trade_id} />
      </td>
    </tr>
  );
}

function Sparkline({ tradeId }: { tradeId: string }) {
  return (
    <div className="h-6 w-28 bg-slate-800/40 rounded relative overflow-hidden text-[10px] text-muted flex items-center justify-center">
      <span>view ↗</span>
    </div>
  );
}
