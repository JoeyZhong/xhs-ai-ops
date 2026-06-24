"use client";

import { useEffect, useRef, useState } from "react";
import { getToken } from "./api";

export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}

export function useSSE(
  path: string | null,
  body: Record<string, unknown>,
  enabled = true
) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bodyStr = JSON.stringify(body);

  useEffect(() => {
    if (!path || !enabled) return;

    setEvents([]); // eslint-disable-line react-hooks/set-state-in-effect
    setError(null);
    setDone(false);

    abortRef.current = new AbortController();
    const signal = abortRef.current.signal;

    const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
    const token = getToken();
    const url = token ? `${API_BASE}${path}?token=${encodeURIComponent(token)}` : `${API_BASE}${path}`;

    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: bodyStr,
      signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) {
          setError(`HTTP ${res.status}`);
          setDone(true);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { done: d, value } = await reader.read();
          if (d) { setDone(true); break; }
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() ?? "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const event = JSON.parse(line.slice(6)) as SSEEvent;
                setEvents((prev) => [...prev, event]);
                if (event.type === "done") setDone(true);
              } catch {
                // ignore malformed SSE line
              }
            }
          }
        }
      })
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setError(String(e));
      });

    return () => abortRef.current?.abort();
    // bodyStr is stable serialization of body; object identity changes on every render
  }, [path, enabled, bodyStr]);

  return { events, error, done };
}
