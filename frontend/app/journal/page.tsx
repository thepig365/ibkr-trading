"use client";

import { useEffect, useMemo, useState } from "react";
import ChartModal from "@/components/ChartModal";
import TradeRow, { TradeRowData } from "@/components/TradeRow";
import { api } from "@/lib/api";

export default function JournalPage() {
  const [trades, setTrades] = useState<TradeRowData[]>([]);
  const [activeTrade, setActiveTrade] = useState<string | null>(null);
  const [filterSymbol, setFilterSymbol] = useState<string>("");
  const [filterStrategy, setFilterStrategy] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");

  useEffect(() => {
    api
      .trades(200)
      .then((rows) => setTrades((rows as TradeRowData[]) || []))
      .catch((err) => console.error(err));
  }, []);

  const symbols = useMemo(
    () => Array.from(new Set(trades.map((t) => t.symbol))).sort(),
    [trades],
  );
  const strategies = useMemo(
    () => Array.from(new Set(trades.map((t) => t.strategy))).sort(),
    [trades],
  );
  const filtered = useMemo(
    () =>
      trades.filter((t) => {
        if (filterSymbol && t.symbol !== filterSymbol) return false;
        if (filterStrategy && t.strategy !== filterStrategy) return false;
        if (filterStatus === "win" && !((t.realized_pnl ?? 0) > 0)) return false;
        if (filterStatus === "loss" && !((t.realized_pnl ?? 0) < 0)) return false;
        if (filterStatus === "open" && t.status !== "open") return false;
        return true;
      }),
    [trades, filterSymbol, filterStrategy, filterStatus],
  );

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap gap-3 items-center">
        <span className="text-xs uppercase tracking-wider text-muted">
          Filters
        </span>
        <select
          className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
          value={filterSymbol}
          onChange={(e) => setFilterSymbol(e.target.value)}
        >
          <option value="">All symbols</option>
          {symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
          value={filterStrategy}
          onChange={(e) => setFilterStrategy(e.target.value)}
        >
          <option value="">All strategies</option>
          {strategies.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">All</option>
          <option value="win">Wins</option>
          <option value="loss">Losses</option>
          <option value="open">Open</option>
        </select>
        <span className="ml-auto text-xs text-muted">
          {filtered.length} / {trades.length}
        </span>
      </div>

      <div className="card overflow-x-auto">
        <table className="data">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Entry time</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>P&L</th>
              <th>R</th>
              <th>Reason</th>
              <th>Chart</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((trade) => (
              <TradeRow
                key={trade.trade_id}
                trade={trade}
                onClick={setActiveTrade}
              />
            ))}
          </tbody>
        </table>
        {filtered.length === 0 ? (
          <div className="text-sm text-muted py-4 text-center">No trades.</div>
        ) : null}
      </div>

      {activeTrade ? (
        <ChartModal
          tradeId={activeTrade}
          onClose={() => setActiveTrade(null)}
        />
      ) : null}
    </div>
  );
}
