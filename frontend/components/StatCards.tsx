import { fmtUsd, fmtPct } from "@/lib/format";

interface Risk {
  equity: number;
  daily_capital_used: number;
  daily_capital_limit: number;
  realized_pnl_day: number;
  trades_today: number;
  max_trades_per_day: number;
  circuit_broken: boolean;
}

export default function StatCards({ risk }: { risk: Risk | null }) {
  const cards = [
    {
      label: "Today P&L",
      value: risk ? fmtUsd(risk.realized_pnl_day) : "—",
      sub: risk
        ? fmtPct((risk.realized_pnl_day / Math.max(risk.equity, 1)) * 100)
        : "",
      color:
        !risk || risk.realized_pnl_day === 0
          ? "text-slate-100"
          : risk.realized_pnl_day > 0
          ? "text-accent"
          : "text-bad",
    },
    {
      label: "Trades",
      value: risk ? `${risk.trades_today} / ${risk.max_trades_per_day}` : "—",
      sub: risk?.circuit_broken ? "circuit broken" : "",
      color: risk?.circuit_broken ? "text-bad" : "text-slate-100",
    },
    {
      label: "Capital used",
      value: risk ? fmtUsd(risk.daily_capital_used, 0) : "—",
      sub: risk ? `cap ${fmtUsd(risk.daily_capital_limit, 0)}` : "",
      color: "text-slate-100",
    },
    {
      label: "Equity",
      value: risk ? fmtUsd(risk.equity, 0) : "—",
      sub: "",
      color: "text-slate-100",
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="card">
          <div className="text-xs text-muted">{c.label}</div>
          <div className={`text-2xl font-semibold mt-1 ${c.color}`}>{c.value}</div>
          {c.sub ? (
            <div className="text-xs text-muted mt-1">{c.sub}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
