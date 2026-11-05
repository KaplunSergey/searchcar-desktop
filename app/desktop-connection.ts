import type { QueryClient } from "@tanstack/react-query";

export function readRequestSignal(method: string, signal?: AbortSignal | null, timeoutMs = 15_000) {
  if (!["GET", "HEAD"].includes(method)) return signal;
  const deadline = AbortSignal.timeout(timeoutMs);
  return signal ? AbortSignal.any([signal, deadline]) : deadline;
}

type RecoveryEvent = {event: "menu-resume" | "recheck-start" | "recheck-finish" | "recheck-error";
  elapsed_ms: number; pending_queries: number; error_queries: number};

export async function recheckDesktopConnection(client: QueryClient, {afterNativeMenu = false, report}: {
  afterNativeMenu?: boolean; report?: (event: RecoveryEvent) => void;
} = {}) {
  const started = Date.now();
  const emit = (event: RecoveryEvent["event"]) => report?.({event, elapsed_ms: Math.min(3600000, Math.max(0, Date.now()-started)),
    pending_queries: Math.min(1000, client.isFetching()),
    error_queries: Math.min(1000, client.getQueryCache().findAll({predicate:q=>q.state.status==='error'}).length)});
  emit("recheck-start");
  try {
    if (afterNativeMenu) {
      // Query cancellation retains cached data; mutations are deliberately untouched.
      await client.cancelQueries({type:"active", fetchStatus:"fetching"});
      await client.refetchQueries({type:"active"}, {cancelRefetch:false});
    } else {
      await client.refetchQueries({ type: "active", predicate: query =>
        ["desktop-runtime", "scans"].includes(String(query.queryKey[0])) || query.state.status === "error" },
        {cancelRefetch:false}); // Native pulses share a slower recovery read.
    }
    emit("recheck-finish");
  } catch (error) { emit("recheck-error"); throw error; }
}

export function nativeServiceHealth(win = window): unknown {
  return (win as Window & { __searchcarServiceHealth?: unknown }).__searchcarServiceHealth;
}

export function desktopConnectionState(failed: boolean, snapshot?: unknown, now = Date.now()) {
  if (!failed) return "connected";
  if (snapshot === undefined) return "disconnected";
  if (snapshot && typeof snapshot === "object") {
    const { ready, checked_at } = snapshot as { ready?: unknown; checked_at?: unknown };
    if (ready === false && typeof checked_at === "number" && Number.isFinite(checked_at)
      && now >= checked_at && now - checked_at <= 10_000) return "disconnected";
  }
  return "checking";
}

export function desktopRuntimeOptions<T extends { desktop: boolean }>(read: (context: {signal: AbortSignal}) => Promise<T>) {
  return {
    queryKey: ["desktop-runtime"],
    queryFn: read,
    retry: 1,
    retryDelay: 1000,
    staleTime: 5000,
    refetchOnWindowFocus: (query: { state: { data?: T } }) => query.state.data?.desktop ? "always" as const : false as const,
    refetchInterval: (query: { state: { data?: T } }) => query.state.data?.desktop ? 1000 : false as const,
  };
}

// Native menus can pause WebView timers/transport without stopping the sidecar.
export function watchDesktopConnection(recheck: () => void, win = window, doc = document, now = Date.now,
  heartbeat?: (lagMs: number) => void) {
  let lastTick = now();
  let lastHeartbeat = lastTick;
  const resume = () => { if (doc.visibilityState !== "hidden") recheck(); };
  const events = ["focus", "pageshow", "online", "searchcar:window-focus"];
  events.forEach(event => win.addEventListener(event, resume));
  doc.addEventListener("visibilitychange", resume);
  const timer = win.setInterval(() => {
    const current = now();
    if (current - lastTick > 5000) resume();
    if (current - lastHeartbeat >= 30000) {
      heartbeat?.(Math.min(3600000, Math.max(0, current-lastTick-1000)));
      lastHeartbeat = current;
    }
    lastTick = current;
  }, 1000);
  return () => {
    win.clearInterval(timer);
    events.forEach(event => win.removeEventListener(event, resume));
    doc.removeEventListener("visibilitychange", resume);
  };
}
