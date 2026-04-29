"use client";

import { useEffect, useState } from "react";
import {
  api,
  ConnectionStatus as Status,
  connectEngineStatus,
} from "@/lib/api";
import { fmtCountdown } from "@/lib/format";

const STATE_BADGE: Record<Status["state"], string> = {
  CONNECTED: "badge-good",
  CONNECTING: "badge-warn",
  AUTO_PAUSED: "badge-warn",
  DISCONNECTED: "badge-muted",
  ERROR: "badge-bad",
};

export default function ConnectionStatus() {
  const [status, setStatus] = useState<Status | null>(null);
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .connectionStatus()
      .then((s) => {
        if (alive) setStatus(s);
      })
      .catch((err) => console.error("connectionStatus failed", err));
    const close = connectEngineStatus((data) => {
      if (alive) setStatus(data);
    });
    return () => {
      alive = false;
      close();
    };
  }, []);

  const handleReconnect = async () => {
    try {
      setReconnecting(true);
      const next = await api.reconnect();
      setStatus(next);
    } catch (err) {
      console.error(err);
    } finally {
      setReconnecting(false);
    }
  };

  if (!status) {
    return (
      <div className="card flex items-center gap-3">
        <span className="badge badge-muted">loading</span>
        <span className="text-sm text-muted">connecting to backend...</span>
      </div>
    );
  }

  const remaining = status.time_remaining;
  const totalSec = status.auto_disconnect_minutes * 60;
  const pct = totalSec > 0 ? Math.max(0, Math.min(100, (remaining / totalSec) * 100)) : 0;

  return (
    <div className="card">
      <div className="flex items-center gap-3 flex-wrap">
        <span className={`badge ${STATE_BADGE[status.state] || "badge-muted"}`}>
          {status.state}
        </span>
        <span className="text-sm text-muted">
          {status.host}:{status.port} · acct {status.account || "—"}
        </span>
        <span className="ml-auto text-xs text-muted">
          {status.last_heartbeat_at
            ? `last heartbeat ${new Date(status.last_heartbeat_at).toLocaleTimeString()}`
            : ""}
        </span>
      </div>

      {status.state === "CONNECTED" ? (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>auto-disconnect in</span>
            <span className="text-slate-100 font-mono">
              {fmtCountdown(remaining)}
            </span>
          </div>
          <div className="h-1.5 bg-slate-800 rounded overflow-hidden">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            className="btn-primary"
            onClick={handleReconnect}
            disabled={reconnecting}
          >
            {reconnecting ? "Reconnecting..." : "Reconnect TWS"}
          </button>
          {status.last_error ? (
            <span className="text-xs text-bad break-all">
              {status.last_error}
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
}
