"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface ConfigPayload {
  strategy: string;
  symbols: string[];
  available_strategies: string[];
  risk: Record<string, number>;
  ict: Record<string, number>;
  ibkr: { host: string; port: number; account: string };
  connection: Record<string, number>;
}

export default function SettingsPage() {
  const [cfg, setCfg] = useState<ConfigPayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [symbolsInput, setSymbolsInput] = useState("");

  useEffect(() => {
    api
      .request<ConfigPayload>("/api/config")
      .then((data) => {
        setCfg(data);
        setSymbolsInput((data.symbols || []).join(", "));
      })
      .catch((err) => setError(String(err)));
  }, []);

  const updateRisk = (key: string, value: number) =>
    setCfg((prev) =>
      prev ? { ...prev, risk: { ...prev.risk, [key]: value } } : prev,
    );
  const updateIct = (key: string, value: number) =>
    setCfg((prev) =>
      prev ? { ...prev, ict: { ...prev.ict, [key]: value } } : prev,
    );

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const symbols = symbolsInput
        .split(",")
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean);
      const payload = {
        strategy: cfg.strategy,
        symbols,
        risk: cfg.risk,
        ict: cfg.ict,
      };
      const next = await api.request<ConfigPayload>("/api/config", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setCfg(next);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) {
    return <div className="card text-sm text-muted">Loading config…</div>;
  }

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="card space-y-3">
        <div className="text-xs uppercase tracking-wider text-muted">
          Strategy
        </div>
        <div className="flex items-center gap-3">
          <select
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm"
            value={cfg.strategy}
            onChange={(e) =>
              setCfg((prev) =>
                prev ? { ...prev, strategy: e.target.value } : prev,
              )
            }
          >
            {cfg.available_strategies.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted">
            Switch active strategy. Persist by editing config.yaml.
          </span>
        </div>
      </div>

      <div className="card space-y-3">
        <div className="text-xs uppercase tracking-wider text-muted">
          Watchlist
        </div>
        <input
          type="text"
          className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm"
          value={symbolsInput}
          onChange={(e) => setSymbolsInput(e.target.value)}
        />
        <div className="text-xs text-muted">
          Comma-separated tickers, e.g. SPY, AAPL, NVDA
        </div>
      </div>

      <ParamGrid
        title="Risk"
        params={cfg.risk}
        onChange={updateRisk}
        labels={{
          max_risk_per_trade: "Max risk per trade (frac)",
          max_daily_loss: "Daily loss circuit (frac)",
          max_trades_per_day: "Max trades / day",
          min_rr_ratio: "Min R:R",
          max_sl_width_pct: "Max stop width (frac)",
          daily_capital_limit: "Daily capital cap ($)",
        }}
      />

      <ParamGrid
        title="ICT"
        params={cfg.ict}
        onChange={updateIct}
        labels={{
          min_fvg_size: "Min FVG size ($)",
          auto_threshold: "Auto-execute score",
          alert_threshold: "Alert score",
          trailing_activation_r: "Trailing activation R",
          trailing_distance_r: "Trailing distance R",
          max_scale_ins: "Max scale-ins",
          scale_in_threshold_r: "Scale-in trigger R",
        }}
      />

      <div className="card flex items-center gap-3">
        <button type="button" onClick={save} className="btn-primary" disabled={saving}>
          {saving ? "Saving..." : "Save (in-memory)"}
        </button>
        {savedAt ? (
          <span className="text-xs text-accent">Saved at {savedAt}</span>
        ) : null}
        {error ? <span className="text-xs text-bad">{error}</span> : null}
        <span className="ml-auto text-xs text-muted">
          Persistence: edit ./config.yaml and restart
        </span>
      </div>
    </div>
  );
}

function ParamGrid({
  title,
  params,
  onChange,
  labels,
}: {
  title: string;
  params: Record<string, number>;
  onChange: (key: string, value: number) => void;
  labels: Record<string, string>;
}) {
  return (
    <div className="card">
      <div className="text-xs uppercase tracking-wider text-muted mb-3">
        {title}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {Object.entries(params).map(([key, value]) => (
          <label key={key} className="block">
            <div className="text-xs text-muted mb-1">
              {labels[key] || key}
            </div>
            <input
              type="number"
              step="any"
              value={value}
              onChange={(e) => onChange(key, Number(e.target.value))}
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm"
            />
          </label>
        ))}
      </div>
    </div>
  );
}
