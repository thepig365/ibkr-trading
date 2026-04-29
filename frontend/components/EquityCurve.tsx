"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface Point {
  date: string;
  ending_equity: number;
}

export default function EquityCurve({ data }: { data: Point[] }) {
  if (!data || data.length === 0) {
    return (
      <div className="card">
        <div className="text-xs uppercase tracking-wider text-muted mb-2">
          Equity curve
        </div>
        <div className="text-sm text-muted">No history yet — run paper trades.</div>
      </div>
    );
  }
  const sorted = [...data].sort((a, b) => a.date.localeCompare(b.date));
  return (
    <div className="card">
      <div className="text-xs uppercase tracking-wider text-muted mb-2">
        Equity curve ({sorted.length} days)
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={sorted}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              stroke="#334155"
            />
            <YAxis
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              stroke="#334155"
              domain={["auto", "auto"]}
            />
            <Tooltip
              contentStyle={{
                background: "#0f172a",
                border: "1px solid #1e293b",
                borderRadius: 6,
                color: "#e2e8f0",
              }}
            />
            <Area
              dataKey="ending_equity"
              stroke="#22c55e"
              fill="#22c55e22"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
