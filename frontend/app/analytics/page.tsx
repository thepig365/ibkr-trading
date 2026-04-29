"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import EquityCurve from "@/components/EquityCurve";
import { api } from "@/lib/api";
import { fmtNum, fmtPct, fmtUsd } from "@/lib/format";

interface Analytics {
  summary: {
    total_trades: number;
    wins: number;
    losses: number;
    win_rate: number;
    avg_r: number;
  };
  equity_curve: Array<{ date: string; ending_equity: number; daily_pnl: number }>;
  monthly_pnl: Array<{ month: string; pnl: number }>;
  by_symbol: any[];
  by_strategy: any[];
  by_kill_zone: any[];
  r_histogram: Array<{ bucket: string; count: number }>;
  score_vs_outcome: Array<{ score: number; executed: boolean }>;
}

const TOOLTIP_STYLE = {
  background: "#0f172a",
  border: "1px solid #1e293b",
  borderRadius: 6,
  color: "#e2e8f0",
};

export default function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);

  useEffect(() => {
    api
      .request<Analytics>("/api/analytics")
      .then(setData)
      .catch((err) => console.error(err));
  }, []);

  if (!data) {
    return (
      <div className="card text-sm text-muted">Loading analytics…</div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <Stat label="Trades" value={data.summary.total_trades.toString()} />
        <Stat
          label="Win rate"
          value={fmtPct(data.summary.win_rate, 1)}
          color={data.summary.win_rate >= 45 ? "text-accent" : "text-warn"}
        />
        <Stat
          label="Avg R"
          value={fmtNum(data.summary.avg_r, 2)}
          color={data.summary.avg_r >= 1.5 ? "text-accent" : "text-warn"}
        />
        <Stat
          label="W / L"
          value={`${data.summary.wins} / ${data.summary.losses}`}
        />
      </div>

      <EquityCurve data={data.equity_curve} />

      <div className="card">
        <div className="text-xs uppercase tracking-wider text-muted mb-2">
          Monthly P&L
        </div>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.monthly_pnl}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis dataKey="month" stroke="#334155" tick={{ fill: "#94a3b8" }} />
              <YAxis stroke="#334155" tick={{ fill: "#94a3b8" }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Bar dataKey="pnl" fill="#22c55e" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <BreakdownTable
          title="By symbol"
          rows={data.by_symbol}
          keyField="symbol"
        />
        <BreakdownTable
          title="By strategy"
          rows={data.by_strategy}
          keyField="strategy"
        />
        <BreakdownTable
          title="By kill zone"
          rows={data.by_kill_zone}
          keyField="zone"
        />
      </div>

      <div className="card">
        <div className="text-xs uppercase tracking-wider text-muted mb-2">
          R-multiple histogram
        </div>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.r_histogram}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis dataKey="bucket" stroke="#334155" tick={{ fill: "#94a3b8" }} />
              <YAxis stroke="#334155" tick={{ fill: "#94a3b8" }} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Bar dataKey="count" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <div className="text-xs uppercase tracking-wider text-muted mb-2">
          Signal score vs outcome
        </div>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis
                type="number"
                dataKey="score"
                domain={[0, 100]}
                stroke="#334155"
                tick={{ fill: "#94a3b8" }}
                label={{ value: "score", fill: "#94a3b8" }}
              />
              <YAxis
                type="number"
                dataKey="outcome"
                domain={[-0.2, 1.2]}
                ticks={[0, 1]}
                stroke="#334155"
                tick={{ fill: "#94a3b8" }}
              />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Legend />
              <Scatter
                name="executed"
                data={data.score_vs_outcome
                  .filter((s) => s.executed)
                  .map((s) => ({ score: s.score, outcome: 1 }))}
                fill="#22c55e"
              />
              <Scatter
                name="alert-only"
                data={data.score_vs_outcome
                  .filter((s) => !s.executed)
                  .map((s) => ({ score: s.score, outcome: 0 }))}
                fill="#f59e0b"
              />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="card">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-2xl font-semibold mt-1 ${color || "text-slate-100"}`}>
        {value}
      </div>
    </div>
  );
}

function BreakdownTable({
  title,
  rows,
  keyField,
}: {
  title: string;
  rows: any[];
  keyField: string;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="text-xs uppercase tracking-wider text-muted mb-2">
        {title}
      </div>
      {rows.length === 0 ? (
        <div className="text-sm text-muted">No data.</div>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th>{keyField}</th>
              <th>Trades</th>
              <th>Win%</th>
              <th>Σ R</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r[keyField]}>
                <td className="font-medium">{r[keyField]}</td>
                <td>{r.trades}</td>
                <td>{fmtPct(r.win_rate, 0)}</td>
                <td className={r.r_sum >= 0 ? "text-accent" : "text-bad"}>
                  {fmtNum(r.r_sum, 2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
