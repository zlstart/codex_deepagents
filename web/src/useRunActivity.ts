import { useEffect, useRef, useState } from "react";
import { HttpError, messageOf, request, running, scope } from "./api";
import type { EventPage, Run, ToolPayload, Trace } from "./api";

export type Activity = Trace & {
  text: string;
  textTruncated: boolean;
  tool?: ToolPayload;
  hasToolCalls?: boolean;
};

/** Replay immutable rows in cursor order. The map retains each item's insertion order. */
export function useRunActivity(run: Run, enabled: boolean) {
  const [observedStatus, setObservedStatus] = useState<string | null>(null);
  const [items, setItems] = useState<Activity[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const sync = useRef<() => void>(() => {});
  const endpoint =
    scope(run.session_id) + "/runs/" + encodeURIComponent(run.id);
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    const byId = new Map<string, Activity>();
    let cursor = 0;
    let stream: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let reading = false;
    let nextStreamAttempt = 0;
    setItems([]);
    setLoaded(false);
    setObservedStatus(null);
    setError("");

    function append(rows: Trace[]) {
      let changed = false;
      for (const row of rows) {
        if (!Number.isSafeInteger(row.seq) || row.seq <= cursor) continue;
        if (row.kind === "run") setObservedStatus(row.status);
        const previous = byId.get(row.id);
        const delta = row.payload?.type === "model_text" ? row.payload : null;
        byId.set(row.id, {
          ...row,
          detail: row.detail ?? previous?.detail,
          text: (previous?.text ?? "") + (delta?.delta ?? ""),
          textTruncated: Boolean(previous?.textTruncated || delta?.truncated),
          hasToolCalls:
            row.payload?.type === "model_end"
              ? row.payload.has_tool_calls
              : previous?.hasToolCalls,
          tool: row.payload?.type === "tool" ? row.payload : previous?.tool,
        });
        cursor = row.seq;
        changed = true;
      }
      if (changed) setItems(Array.from(byId.values()));
      setLoaded(true);
    }
    function closeStream() {
      stream?.close();
      stream = undefined;
      clearTimeout(timer);
    }
    function reconnect() {
      closeStream();
      nextStreamAttempt = Date.now() + 15_000;
      if (!abort.signal.aborted) timer = setTimeout(read, 0);
    }
    function connect() {
      if (abort.signal.aborted) return;
      if (
        typeof EventSource === "undefined" ||
        Date.now() < nextStreamAttempt
      ) {
        timer = setTimeout(read, 1000);
        return;
      }
      try {
        const source = new EventSource(endpoint + "/stream?after=" + cursor);
        stream = source;
        const watchdog = () => {
          clearTimeout(timer);
          // Some proxies buffer a healthy stream until it closes.
          timer = setTimeout(() => {
            closeStream();
            void read();
          }, 30_000);
        };
        source.addEventListener("activity", (event: MessageEvent<string>) => {
          if (stream !== source || abort.signal.aborted) return;
          try {
            watchdog();
            append([JSON.parse(event.data) as Trace]);
          } catch {
            reconnect();
          }
        });
        source.addEventListener("end", () => {
          if (stream !== source || abort.signal.aborted) return;
          // Read through the terminal cursor even when a proxy coalesced the last frames.
          closeStream();
          void read();
        });
        source.onerror = () => {
          if (stream === source && !abort.signal.aborted) reconnect();
        };
        watchdog();
      } catch {
        reconnect();
      }
    }
    async function read() {
      if (reading || abort.signal.aborted) return;
      reading = true;
      closeStream();
      try {
        let page: EventPage;
        do {
          const before = cursor;
          page = await request<EventPage>(
            endpoint + "/events?after=" + cursor,
            { signal: abort.signal },
          );
          if (abort.signal.aborted) return;
          append(page.events);
          if (cursor === before) break;
        } while (page.has_more ?? page.events.length === 500);
        setObservedStatus(page.status);
        setError("");
        if (running(page.status)) connect();
      } catch (cause) {
        if (abort.signal.aborted) return;
        setError(messageOf(cause));
        if (!(
          cause instanceof HttpError && [401, 403, 404].includes(cause.status)
        )) {
          timer = setTimeout(read, 2400);
        }
      } finally {
        reading = false;
      }
    }
    sync.current = () => {
      closeStream();
      void read();
    };
    void read();
    return () => {
      abort.abort();
      closeStream();
      sync.current = () => {};
    };
  }, [endpoint, enabled]);
  useEffect(() => {
    if (!running(run.status)) sync.current();
  }, [run.status]);
  return { items, observedStatus, error, loaded, retry: () => sync.current() };
}
