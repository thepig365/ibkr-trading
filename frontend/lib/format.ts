export const fmtUsd = (value: number | null | undefined, digits = 2): string => {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
};

export const fmtPct = (value: number | null | undefined, digits = 2): string => {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
};

export const fmtNum = (value: number | null | undefined, digits = 2): string => {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
};

export const fmtCountdown = (seconds: number): string => {
  if (seconds <= 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
};

export const fmtDateTime = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
};

export const fmtTime = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString();
};
