import type { IbkrAccountDashboard, RiskSummary } from "@/lib/api";
import { fmtUsd, fmtPct } from "@/lib/format";

function equityDenom(
  ibkr: IbkrAccountDashboard | null,
  risk: RiskSummary | null,
): number {
  if (
    ibkr?.account_data_available &&
    ibkr.net_liquidation != null &&
    ibkr.net_liquidation > 0
  ) {
    return ibkr.net_liquidation;
  }
  return risk ? Math.max(risk.equity, 1) : 1;
}

export default function StatCards({
  risk,
  ibkr,
}: {
  risk: RiskSummary | null;
  ibkr: IbkrAccountDashboard | null;
}) {
  const denom = equityDenom(ibkr, risk);

  let equityMain: string;
  let equitySub: string;
  let equityColor = "text-slate-100";

  if (ibkr?.account_data_available && ibkr.net_liquidation != null) {
    equityMain = fmtUsd(ibkr.net_liquidation, 0);
    const parts: string[] = [];
    if (ibkr.available_funds != null) {
      parts.push(`avail ${fmtUsd(ibkr.available_funds, 0)}`);
    }
    if (ibkr.unrealized_pnl != null) {
      parts.push(`uPnL ${fmtUsd(ibkr.unrealized_pnl, 0)}`);
    }
    equitySub = parts.join(" · ");
  } else if (ibkr?.warning) {
    equityMain = "Account data unavailable";
    equitySub = "Shows internal sizing equity when IB summary missing.";
    equityColor = "text-bad";
  } else {
    equityMain = "—";
    equitySub = "";
  }

  const cards = [
    {
      label: "Today P&L",
      value: risk ? fmtUsd(risk.realized_pnl_day) : "—",
      sub: risk
        ? fmtPct((risk.realized_pnl_day / denom) * 100)
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
      sub: risk
        ? `daily notional cap ${fmtUsd(risk.daily_capital_limit, 0)}`
        : "",
      color: "text-slate-100",
    },
    {
      label: "Equity (IBKR)",
      value: equityMain,
      sub: equitySub,
      color: equityColor,
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
