// Temporary preview-only hooks. Removal points: docs/TEST_DIAGNOSTICS.md.
export function watchTestDiagnostics(enabled: boolean, send: (data: object) => Promise<unknown>,
  counts: () => {pending_queries:number;error_queries:number}, win = window, doc = document, now = Date.now) {
  if (!enabled) return () => {};
  let stopped = false, busy = false, previous = now();
  const emit = (event: string, lag = 0) => {
    if (stopped || busy) return;
    const native = (win as Window & {__searchcarServiceHealth?:{checked_at?:number}}).__searchcarServiceHealth;
    const age = now() - (native?.checked_at || now());
    busy = true;
    void send({event, focused:doc.hasFocus(), hidden:doc.visibilityState === 'hidden',
      lag_ms:Math.min(3600000,Math.max(0,Math.round(lag))),
      native_age_ms:Math.min(3600000,Math.max(0,Math.round(age))),...counts()})
      .catch(()=>{}).finally(()=>{busy=false;});
  };
  const focus = () => emit('focus'), blur = () => emit('blur'), visibility = () => emit('visibility');
  const menuResumed = () => emit('menu-resume');
  const key = (event: KeyboardEvent) => { if(event.key === 'Escape' || (event.altKey && event.code === 'Space')) emit('menu-key'); };
  win.addEventListener('focus',focus);win.addEventListener('blur',blur);win.addEventListener('keydown',key);
  win.addEventListener('searchcar:menu-resume',menuResumed);
  doc.addEventListener('visibilitychange',visibility);
  const timer = win.setInterval(()=>{const current=now();const lag=current-previous-15000;previous=current;
    emit(lag>5000?'timer-gap':'heartbeat',lag);},15000);
  emit('heartbeat');
  return ()=>{stopped=true;win.clearInterval(timer);win.removeEventListener('focus',focus);
    win.removeEventListener('searchcar:menu-resume',menuResumed);
    win.removeEventListener('blur',blur);win.removeEventListener('keydown',key);doc.removeEventListener('visibilitychange',visibility);};
}
