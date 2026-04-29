"use client";

import { useCallback, useEffect, useState } from "react";
import ConnectionStatus from "@/components/ConnectionStatus";
import EquityCurve from "@/components/EquityCurve";
import PositionsPanel from "@/components/PositionsPanel";
import SignalQueue from "@/components/SignalQueue";
import StatCards from "@/components/StatCards";
import {
  api,
  type IbkrAccountDashboard,
  type RiskSummary,
} from "@/lib/api";

export default function DashboardPage() {
  const [risk, setRisk] = useState<RiskSummary | null>(null);
  const [ibkr, setIbkr] = useState<IbkrAccountDashboard | null>(null);
  const [positions, setPositions] = useState<any[]>([]);
  const [signals, setSignals] = useState<any[]>([]);
  const [equity, setEquity] = useState<any[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [d, pos, sigs, daily] = await Promise.all([
        api.dashboard(),
        api.positions(),
        api.signals(20),
        api.dailyPerformance(30),
      ]);
      if (d?.available && d.risk) {
        setRisk(d.risk);
      } else {
        setRisk(null);
      }
      setIbkr(d.ibkr_account ?? null);
      setPositions(pos || []);
      setSignals(sigs || []);
      setEquity(daily || []);
    } catch (err) {
      console.error("refresh failed", err);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const onClose = useCallback(
    async (symbol: string) => {
      try {
        await api.closePosition(symbol);
        await refresh();
      } catch (err) {
        console.error(err);
      }
    },
    [refresh],
  );

  return (
    <div className="space-y-4">
      <ConnectionStatus />
      <StatCards risk={risk} ibkr={ibkr} />
      <PositionsPanel positions={positions} onClose={onClose} />
      <EquityCurve data={equity} />
      <SignalQueue signals={signals} />
    </div>
  );
}
