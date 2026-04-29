"use client";

import { useCallback, useEffect, useState } from "react";
import ConnectionStatus from "@/components/ConnectionStatus";
import EquityCurve from "@/components/EquityCurve";
import PositionsPanel from "@/components/PositionsPanel";
import SignalQueue from "@/components/SignalQueue";
import StatCards from "@/components/StatCards";
import { api } from "@/lib/api";

export default function DashboardPage() {
  const [risk, setRisk] = useState<any>(null);
  const [positions, setPositions] = useState<any[]>([]);
  const [signals, setSignals] = useState<any[]>([]);
  const [equity, setEquity] = useState<any[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [engine, pos, sigs, daily] = await Promise.all([
        api.engineStatus(),
        api.positions(),
        api.signals(20),
        api.dailyPerformance(30),
      ]);
      if (engine?.available) {
        setRisk(engine.risk);
      }
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
      <StatCards risk={risk} />
      <PositionsPanel positions={positions} onClose={onClose} />
      <EquityCurve data={equity} />
      <SignalQueue signals={signals} />
    </div>
  );
}
