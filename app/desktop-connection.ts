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
