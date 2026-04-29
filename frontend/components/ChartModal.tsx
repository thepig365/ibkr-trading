"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  LineStyle,
  Time,
  createChart,
} from "lightweight-charts";
import { api } from "@/lib/api";
import { fmtUsd, fmtNum, fmtDateTime } from "@/lib/format";

interface Candle {
  timestamp: string;
  time_unix: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface ScaleIn {
  price: number;
  shares: number;
  time: string;
  time_unix: number;
  reason: string;
}

interface Trade {
  trade_id: string;
  symbol: string;
  direction: string;
  entry_price: number;
  entry_time: string;
  entry_shares: number;
  exit_price: number | null;
  exit_time: string | null;
  exit_reason: string | null;
  realized_pnl: number | null;
  realized_r: number | null;
  stop_loss: number;
  take_profit: number;
  trailing_stop_final: number | null;
  trailing_activated: number | boolean;
  entry_fvg_top: number | null;
  entry_fvg_bottom: number | null;
  entry_signal_score: number | null;
  entry_reason: string | null;
}

export default function ChartModal({
  tradeId,
  onClose,
}: {
  tradeId: string;
  onClose: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [trade, setTrade] = useState<Trade | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [scaleIns, setScaleIns] = useState<ScaleIn[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .tradeCandles(tradeId)
      .then((data) => {
        if (!alive) return;
        setTrade(data.trade);
        setCandles(data.candles || []);
        setScaleIns(data.scale_ins || []);
      })
      .catch((err) => {
        console.error(err);
        if (alive) setError(String(err));
      });
    return () => {
      alive = false;
    };
  }, [tradeId]);

  useEffect(() => {
    if (!containerRef.current || candles.length === 0 || !trade) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 520,
      layout: {
        background: { type: ColorType.Solid, color: "#0b1220" },
        textColor: "#cbd5e1",
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#334155" },
    });
    chartRef.current = chart;

    const series = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });
    const seriesData = candles
      .map((c) => ({
        time: c.time_unix as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number));
    series.setData(seriesData);

    // FVG zone (price line approximation since shaded zones are unsupported in
    // this build). Render top + bottom + midpoint as horizontal lines.
    if (trade.entry_fvg_top != null && trade.entry_fvg_bottom != null) {
      series.createPriceLine({
        price: trade.entry_fvg_top,
        color: "#3b82f6",
        lineStyle: LineStyle.Solid,
        lineWidth: 1,
        title: "FVG top",
        axisLabelVisible: true,
      });
      series.createPriceLine({
        price: trade.entry_fvg_bottom,
        color: "#3b82f6",
        lineStyle: LineStyle.Solid,
        lineWidth: 1,
        title: "FVG bottom",
        axisLabelVisible: true,
      });
      series.createPriceLine({
        price: (trade.entry_fvg_top + trade.entry_fvg_bottom) / 2,
        color: "#60a5fa",
        lineStyle: LineStyle.Dotted,
        lineWidth: 1,
        title: "FVG mid",
        axisLabelVisible: false,
      });
    }

    series.createPriceLine({
      price: trade.stop_loss,
      color: "#ef4444",
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      title: "SL",
      axisLabelVisible: true,
    });
    series.createPriceLine({
      price: trade.take_profit,
      color: "#22c55e",
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      title: "TP",
      axisLabelVisible: true,
    });
    series.createPriceLine({
      price: trade.entry_price,
      color: "#94a3b8",
      lineStyle: LineStyle.Dotted,
      lineWidth: 1,
      title: "BE",
      axisLabelVisible: true,
    });
    if (trade.trailing_stop_final != null) {
      series.createPriceLine({
        price: trade.trailing_stop_final,
        color: "#f59e0b",
        lineStyle: LineStyle.Dashed,
        lineWidth: 1,
        title: "Trail",
        axisLabelVisible: true,
      });
    }

    // Markers: entry, exit, scale-ins
    const markers: Array<{
      time: Time;
      position: "aboveBar" | "belowBar";
      color: string;
      shape: "arrowUp" | "arrowDown" | "circle";
      text: string;
    }> = [];

    const entryUnix = Math.floor(new Date(trade.entry_time).getTime() / 1000);
    markers.push({
      time: entryUnix as Time,
      position: trade.direction === "LONG" ? "belowBar" : "aboveBar",
      color: trade.direction === "LONG" ? "#22c55e" : "#ef4444",
      shape: trade.direction === "LONG" ? "arrowUp" : "arrowDown",
      text: `Entry ${fmtUsd(trade.entry_price)}`,
    });

    if (trade.exit_time && trade.exit_price != null) {
      const exitUnix = Math.floor(new Date(trade.exit_time).getTime() / 1000);
      markers.push({
        time: exitUnix as Time,
        position: trade.direction === "LONG" ? "aboveBar" : "belowBar",
        color: "#f59e0b",
        shape: trade.direction === "LONG" ? "arrowDown" : "arrowUp",
        text: `Exit ${fmtUsd(trade.exit_price)} ${trade.exit_reason ?? ""}`,
      });
    }

    for (const scale of scaleIns) {
      markers.push({
        time: scale.time_unix as Time,
        position: "aboveBar",
        color: "#3b82f6",
        shape: "circle",
        text: `+${scale.shares} @ ${fmtUsd(scale.price)}`,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers as any);
    chart.timeScale().fitContent();

    const onResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [candles, scaleIns, trade]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-slate-800 rounded-lg w-full max-w-6xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
          <div className="flex items-center gap-3 text-sm">
            {trade ? (
              <>
                <span className="font-semibold">{trade.symbol}</span>
                <span
                  className={
                    trade.direction === "LONG" ? "text-accent" : "text-bad"
                  }
                >
                  {trade.direction}
                </span>
                <span className="text-muted">
                  {fmtDateTime(trade.entry_time)}
                </span>
                <span
                  className={
                    (trade.realized_pnl ?? 0) >= 0
                      ? "text-accent"
                      : "text-bad"
                  }
                >
                  {fmtUsd(trade.realized_pnl)}
                </span>
                <span>
                  {trade.realized_r == null
                    ? ""
                    : `${fmtNum(trade.realized_r, 2)}R`}
                </span>
              </>
            ) : (
              "Loading…"
            )}
          </div>
          <button type="button" onClick={onClose} className="btn">
            ✕
          </button>
        </div>
        {error ? (
          <div className="p-4 text-sm text-bad">{error}</div>
        ) : (
          <div ref={containerRef} className="w-full" />
        )}
        <div className="px-4 py-3 border-t border-slate-800 text-xs text-muted flex flex-wrap gap-4">
          <span>▶ entry {trade ? fmtUsd(trade.entry_price) : ""}</span>
          <span>● scale-ins {scaleIns.length}</span>
          <span>◀ exit {trade?.exit_reason ?? "—"}</span>
          <span>FVG zone shown as price lines</span>
          <span>--- SL --- TP --- trail --- breakeven</span>
        </div>
      </div>
    </div>
  );
}
