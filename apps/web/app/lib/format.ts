// Display formatting. Values are never recalculated here — only presented.
import type { AnomalySignal } from "./types";

export function money(currency: string | null, value: number, signed = false) {
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "−" : "") : "";
  const amount = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${sign}${currency ? `${currency} ` : ""}${amount}`;
}

export function percent(value: number | null, signed = false) {
  if (value === null) return "—";
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "−" : "") : "";
  return `${sign}${Math.abs(value).toFixed(1)}%`;
}

export function duration(hours: number | null) {
  if (hours === null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} d`;
}

export function dateLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" }).format(
    new Date(value)
  );
}

export function relativeTime(value: string, now = Date.now()) {
  const minutes = Math.round((now - new Date(value).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}

export function observed(signal: AnomalySignal, value: number | null) {
  if (value === null) return "—";
  switch (signal.unit) {
    case "percent":
      return percent(value, true);
    case "amount":
      return money(signal.currency, value);
    case "days":
      return `${value.toFixed(1)} d`;
    default:
      return String(Math.round(value * 100) / 100);
  }
}

export function documentTypeName(type: string) {
  return (
    {
      purchase_order: "Purchase order",
      delivery_order: "Delivery order",
      invoice: "Invoice",
      receipt: "Receipt",
    } as Record<string, string>
  )[type] ?? type;
}

export function statusLabel(status: string) {
  return (
    {
      collecting: "Open",
      ready: "Open",
      matched: "Matched",
      review_required: "Needs review",
      insufficient_data: "More data",
      resolved: "Resolved",
    } as Record<string, string>
  )[status] ?? status;
}

export function statusClass(status: string) {
  return ["collecting", "ready", "insufficient_data"].includes(status) ? "open" : status;
}

export function fieldLabel(path: string) {
  const line = /^line_items\.(\d+)\.(.+)$/.exec(path);
  if (line) return `Line ${Number(line[1]) + 1} · ${line[2].replaceAll("_", " ")}`;
  return path.replaceAll("_", " ");
}

export function dateTimeLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function plainMoney(currency: string | null, value: number | null) {
  if (value === null) return "—";
  return money(currency, value);
}

export function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
