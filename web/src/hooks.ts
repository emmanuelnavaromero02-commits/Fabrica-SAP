import { useCallback, useEffect, useRef, useState } from "react";

import { errorText } from "./errors";

export function usePolling<T>(load: () => Promise<T>, intervalMs = 2500) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(timer);
  }, [refresh, intervalMs]);

  return { data, error, refresh };
}

export function useStored<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  const update = (next: T) => {
    setValue(next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      return;
    }
  };
  return [value, update] as const;
}

const ACTIVITY_EVENTS = ["pointerdown", "keydown", "scroll", "pointermove"] as const;
const IDLE_MS = 5 * 60 * 1000;

export function useActivityHeartbeat(send: () => Promise<void>, intervalMs = 60_000) {
  const sendRef = useRef(send);
  sendRef.current = send;

  useEffect(() => {
    let lastActivity = Date.now();
    const markActive = () => {
      lastActivity = Date.now();
    };
    for (const event of ACTIVITY_EVENTS) window.addEventListener(event, markActive, { passive: true });
    const beat = () => {
      const visible = document.visibilityState === "visible";
      if (visible && Date.now() - lastActivity < IDLE_MS) void sendRef.current().catch(() => undefined);
    };
    beat();
    const timer = setInterval(beat, intervalMs);
    return () => {
      clearInterval(timer);
      for (const event of ACTIVITY_EVENTS) window.removeEventListener(event, markActive);
    };
  }, [intervalMs]);
}
