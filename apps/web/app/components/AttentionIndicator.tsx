"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, AttentionEvent, AttentionList, getJson, relativeTime } from "./intelligence";

const REFRESH_MS = 60_000;

type Props = {
  onOpenTransaction: (id: string) => void;
  onSupplier: (key: string) => void;
  onOverview: () => void;
};

async function patch(id: string, body: { seen?: boolean; dismissed?: boolean }) {
  await fetch(`${API_URL}/api/v1/attention/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export default function AttentionIndicator({ onOpenTransaction, onSupplier, onOverview }: Props) {
  const [data, setData] = useState<AttentionList | null>(null);
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await getJson<AttentionList>("/api/v1/attention"));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    function close(event: MouseEvent | KeyboardEvent) {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !root.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && data) {
      const unseen = data.events.filter((event) => !event.seen_at);
      if (unseen.length) {
        await Promise.all(unseen.map((event) => patch(event.id, { seen: true })));
        void refresh();
      }
    }
  }

  async function dismiss(event: AttentionEvent) {
    setData((current) =>
      current
        ? {
            ...current,
            active_count: current.active_count - 1,
            events: current.events.filter((item) => item.id !== event.id),
          }
        : current
    );
    await patch(event.id, { dismissed: true });
    void refresh();
  }

  function go(event: AttentionEvent) {
    setOpen(false);
    if (event.transaction_id) onOpenTransaction(event.transaction_id);
    else if (event.entity_type === "supplier" && event.entity_id) onSupplier(event.entity_id);
    else onOverview();
  }

  const count = data?.active_count ?? 0;
  const unseen = data?.unseen_count ?? 0;

  return (
    <div className="attention" ref={root}>
      <button
        type="button"
        className={`attentionToggle ${unseen > 0 ? "unseen" : ""}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => void toggle()}
        disabled={failed && !data}
        title={failed ? "Attention queue unavailable" : undefined}
      >
        Attention
        <span className="attentionCount">{failed && !data ? "—" : count}</span>
        {unseen > 0 && <span className="visuallyHidden">, {unseen} new</span>}
      </button>

      {open && (
        <div className="attentionPanel" role="dialog" aria-label="Attention queue">
          <div className="attentionHead">
            <span>Attention</span>
            <span>{count} active</span>
          </div>
          {!data || data.events.length === 0 ? (
            <p className="attentionEmpty">Nothing needs attention right now.</p>
          ) : (
            <ul>
              {data.events.map((event) => (
                <li key={event.id} className={event.seen_at ? "" : "new"}>
                  <button type="button" className="attentionItem" onClick={() => go(event)}>
                    <span className={`severity ${event.severity}`}>{event.severity}</span>
                    <span className="attentionBody">
                      <small>{event.entity_label ?? "Workspace"}</small>
                      <strong>{event.title}</strong>
                      <span>{event.message}</span>
                      <em>{relativeTime(event.created_at)}</em>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="attentionDismiss"
                    aria-label={`Dismiss: ${event.title}`}
                    onClick={() => void dismiss(event)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
