/**
 * REST + WebSocket client for the IBKR Trading Engine backend.
 * The backend listens on `NEXT_PUBLIC_BACKEND_URL` (default http://localhost:8000).
 */

const BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

const WS_BACKEND = BACKEND.replace(/^http/, "ws");

export type ConnectionState =
  | "DISCONNECTED"
  | "CONNECTING"
  | "CONNECTED"
  | "AUTO_PAUSED"
  | "ERROR";

export interface ConnectionStatus {
  state: ConnectionState;
  connected: boolean;
  time_remaining: number;
  auto_disconnect_minutes: number;
  connected_at: string | null;
  auto_disconnect_at: string | null;
  last_heartbeat_at: string | null;
  last_error: string | null;
  host: string;
  port: number;
  account: string;
}

export interface IbkrAccountDashboard {
  account_data_available: boolean;
  net_liquidation: number | null;
  available_funds: number | null;
  total_cash_value: number | null;
  unrealized_pnl: number | null;
  realized_pnl: number | null;
  fallback_equity_hint: boolean;
  warning: string | null;
  ib_account: string | null;
}

/** Mirrors backend RiskManager.status_dict numeric fields used by the dashboard. */
export interface RiskSummary {
  equity: number;
  daily_capital_used: number;
  daily_capital_limit: number;
  realized_pnl_day: number;
  trades_today: number;
  max_trades_per_day: number;
  circuit_broken: boolean;
}

export interface DashboardPayload {
  available: boolean;
  paused?: boolean;
  connection?: string;
  strategy?: string;
  risk?: RiskSummary;
  open_positions?: unknown[];
  ibkr_account: IbkrAccountDashboard;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json();
}

export const api = {
  request: fetchJson,
  connectionStatus: () =>
    fetchJson<ConnectionStatus>("/api/connection-status"),
  reconnect: () =>
    fetchJson<ConnectionStatus>("/api/reconnect", { method: "POST" }),
  engineStatus: () => fetchJson<any>("/api/engine-status"),
  positions: () => fetchJson<any[]>("/api/positions"),
  trades: (limit = 100) => fetchJson<any[]>(`/api/trades?limit=${limit}`),
  trade: (id: string) => fetchJson<any>(`/api/trade/${id}`),
  tradeCandles: (id: string) =>
    fetchJson<any>(`/api/trade/${id}/candles`),
  signals: (limit = 100) => fetchJson<any[]>(`/api/signals?limit=${limit}`),
  dailyPerformance: (limit = 60) =>
    fetchJson<any[]>(`/api/daily-performance?limit=${limit}`),
  pause: () => fetchJson<any>("/api/engine/pause", { method: "POST" }),
  resume: () => fetchJson<any>("/api/engine/resume", { method: "POST" }),
  closePosition: (symbol: string) =>
    fetchJson<any>(`/api/positions/${symbol}/close`, { method: "POST" }),
  dashboard: () => fetchJson<DashboardPayload>("/api/dashboard"),
};

/**
 * Connect to the engine status WebSocket with automatic reconnect.
 * Returns a function that closes the underlying socket.
 */
export function connectEngineStatus(
  onMessage: (data: ConnectionStatus) => void,
  onError?: (e: Event) => void,
): () => void {
  let socket: WebSocket | null = null;
  let stopped = false;
  let retryDelay = 1000;

  const connect = () => {
    if (stopped) return;
    socket = new WebSocket(`${WS_BACKEND}/ws/engine-status`);
    socket.onopen = () => {
      retryDelay = 1000;
    };
    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data) as ConnectionStatus);
      } catch (err) {
        console.error("WS payload parse error", err);
      }
    };
    socket.onerror = (event) => {
      onError?.(event);
    };
    socket.onclose = () => {
      if (stopped) return;
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    };
  };

  connect();
  return () => {
    stopped = true;
    socket?.close();
  };
}
