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

export function desktopRuntimeOptions<T extends { desktop: boolean }>(read: () => Promise<T>) {
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
export function watchDesktopConnection(recheck: () => void, win = window, doc = document, now = Date.now) {
  let lastTick = now();
  const resume = () => { if (doc.visibilityState !== "hidden") recheck(); };
  const events = ["focus", "pageshow", "online", "searchcar:window-focus"];
  events.forEach(event => win.addEventListener(event, resume));
  doc.addEventListener("visibilitychange", resume);
  const timer = win.setInterval(() => {
    const current = now();
    if (current - lastTick > 5000) resume();
    lastTick = current;
  }, 1000);
  return () => {
    win.clearInterval(timer);
    events.forEach(event => win.removeEventListener(event, resume));
    doc.removeEventListener("visibilitychange", resume);
  };
}
