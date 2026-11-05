import assert from 'node:assert/strict';
import test from 'node:test';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { desktopConnectionState, desktopRuntimeOptions, readRequestSignal, recheckDesktopConnection, watchDesktopConnection } from '../app/desktop-connection.ts';

test('reads have a finite transport deadline and retain caller cancellation; writes are not retried or timed out', async () => {
  const caller = new AbortController();
  const read = readRequestSignal('GET', caller.signal, 15);
  const unbound = readRequestSignal('GET', undefined, 15);
  assert.ok(read && unbound);
  caller.abort();
  assert.equal(read.aborted, true);
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(unbound.aborted, true);
  assert.equal(readRequestSignal('POST', caller.signal), caller.signal);
  assert.equal(readRequestSignal('DELETE'), undefined);
});

test('native-menu recovery reports completion counts without sending query values', async () => {
  const client = new QueryClient(), reports = [];
  try {
    await recheckDesktopConnection(client, {afterNativeMenu:true, report:event => reports.push(event)});
    assert.deepEqual(reports.map(x=>x.event), ['recheck-start','recheck-finish']);
    assert.ok(reports.every(x => typeof x.pending_queries === 'number' && typeof x.error_queries === 'number'));
    assert.equal(reports[1].pending_queries, 0);
  } finally { client.clear(); }
});

test('native menu exit replaces a stuck read once, retaining data and never repeating a mutation', async () => {
  const client = new QueryClient();
  client.setQueryData(['desktop-runtime'], {desktop:true});
  client.setQueryData(['projects','copart'], ['saved']);
  const signals = [], stops = [];
  let sends = 0;
  const mutation = client.getMutationCache().build(client, {mutationFn: async () => ++sends});
  await mutation.execute();
  for (const queryKey of [['desktop-runtime'], ['projects','copart']]) {
    const observer = new QueryObserver(client, {queryKey, retry:false, queryFn: ({signal}) => {
      signals.push(signal);
      return new Promise(() => {});
    }});
    stops.push(observer.subscribe(() => {}));
  }
  try {
    void recheckDesktopConnection(client, {afterNativeMenu:true});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(signals.length,4,'one replacement per active read');
    assert.ok(signals[0].aborted && signals[1].aborted);
    assert.ok(!signals[2].aborted && !signals[3].aborted);
    assert.deepEqual(client.getQueryData(['projects','copart']),['saved']);
    void recheckDesktopConnection(client);
    assert.equal(signals.length,4,'ordinary health pulses still share the pending read');
    assert.equal(sends,1,'never retry mutations after a native menu');
  } finally {stops.forEach(stop=>stop());client.clear();}
});

test('repeated native pulses must not restart a slower recovering UI read forever', async () => {
  const client = new QueryClient();
  client.setQueryData(['desktop-runtime'], {desktop:true});
  const replies = [];
  const observer = new QueryObserver(client, {queryKey:['desktop-runtime'],staleTime:Infinity,retry:false,
    queryFn: async ({signal}) => {
      await new Promise(resolve=>replies.push(resolve));
      if (signal.aborted) throw new Error('cancelled recovery');
      return {desktop:true,recovered:true};
    }});
  const stop = observer.subscribe(()=>{});
  client.getQueryCache().find({queryKey:['desktop-runtime'],exact:true}).setState({status:'error',error:new Error('menu transport paused')});
  try {
    const recovered = recheckDesktopConnection(client);
    for(let pulse=0;pulse<5;pulse++) void recheckDesktopConnection(client);
    assert.equal(replies.length,1,'native pulses must share the pending read, not cancel it');
    replies[0]();
    await recovered;
    assert.equal(client.getQueryState(['desktop-runtime']).status,'success');
    assert.equal(client.getQueryData(['desktop-runtime']).recovered,true);
  } finally {replies.forEach(resolve=>resolve());stop();client.clear();}
});

test('paused WebView reads are not a backend outage when the native probe is healthy or stale', () => {
  const now = 100_000;
  assert.equal(desktopConnectionState(true, { ready: true, checked_at: now }, now), 'checking');
  assert.equal(desktopConnectionState(true, { ready: false, checked_at: now - 11_000 }, now), 'checking');
  assert.equal(desktopConnectionState(true, { ready: null, checked_at: 0 }, now), 'checking');
  assert.equal(desktopConnectionState(true, { ready: false, checked_at: now }, now), 'disconnected');
  assert.equal(desktopConnectionState(true, undefined, now), 'disconnected', 'web/older shells retain error handling');
  assert.equal(desktopConnectionState(false, { ready: false, checked_at: now }, now), 'connected', 'a successful runtime read wins');
  for (const snapshot of [null, {}, {ready:false,checked_at:NaN}, {ready:false,checked_at:now+1}]) {
    assert.equal(desktopConnectionState(true, snapshot, now), 'checking');
  }
});

test('a single stalled WebView request is rechecked before declaring the sidecar disconnected', async () => {
  const client = new QueryClient();
  client.setQueryData(['desktop-runtime'], { desktop: true });
  let calls = 0;
  const states = [];
  const options = desktopRuntimeOptions(async () => {
    if (++calls === 1) throw new DOMException('fixture native menu paused transport', 'TimeoutError');
    return { desktop: true };
  });
  const observer = new QueryObserver(client, { ...options, retryDelay: 0 });
  const stop = observer.subscribe(result => states.push(result.status));
  try {
    await observer.refetch();
    assert.equal(calls, 2);
    assert.equal(observer.getCurrentResult().status, 'success');
    assert.ok(!states.includes('error'), 'a recovered transient timeout must not emit a disconnect');
  } finally { stop(); client.clear(); }
});

test('a real outage still surfaces an error and retains the cached desktop identity', async () => {
  const client = new QueryClient();
  client.setQueryData(['desktop-runtime'], { desktop: true });
  const options = desktopRuntimeOptions(async () => { throw new TypeError('fixture connection refused'); });
  try {
    await assert.rejects(client.fetchQuery({ ...options, retryDelay: 0, staleTime: 0 }));
    assert.equal(client.getQueryState(['desktop-runtime']).status, 'error');
    assert.equal(client.getQueryData(['desktop-runtime']).desktop, true);
  } finally { client.clear(); }
});

test('focus rechecks even a previously successful cached runtime snapshot', () => {
  const client = new QueryClient();
  client.setQueryData(['desktop-runtime'], { desktop: true });
  const observer = new QueryObserver(client, desktopRuntimeOptions(async () => ({ desktop: true })));
  try { assert.equal(observer.shouldFetchOnWindowFocus(), true); }
  finally { observer.destroy(); client.clear(); }
});

test('a web-only runtime does not start desktop polling on window focus', () => {
  const client = new QueryClient();
  client.setQueryData(['desktop-runtime'], { desktop: false });
  const options = desktopRuntimeOptions(async () => ({ desktop: false }));
  const observer = new QueryObserver(client, options);
  try {
    assert.equal(observer.shouldFetchOnWindowFocus(), false);
    assert.equal(options.refetchInterval({ state: { data: { desktop: false } } }), false);
  } finally { observer.destroy(); client.clear(); }
});

test('native focus and timer gaps recheck after a menu pause; hidden windows and cleanup do not', () => {
  const win = new EventTarget(), doc = new EventTarget();
  doc.visibilityState = 'visible';
  let tick, cleared, now = 0, checks = 0;
  win.setInterval = (callback, delay) => { assert.equal(delay, 1000); tick = callback; return 42; };
  win.clearInterval = id => { cleared = id; };
  const stop = watchDesktopConnection(() => checks++, win, doc, () => now);
  now = 1000; tick(); assert.equal(checks, 0);
  now = 31000; tick(); assert.equal(checks, 1);
  for (const name of ['focus', 'pageshow', 'online', 'searchcar:window-focus']) win.dispatchEvent(new Event(name));
  assert.equal(checks, 5);
  doc.visibilityState = 'hidden'; win.dispatchEvent(new Event('focus'));
  assert.equal(checks, 5);
  doc.visibilityState = 'visible'; doc.dispatchEvent(new Event('visibilitychange'));
  assert.equal(checks, 6);
  stop(); assert.equal(cleared, 42);
  win.dispatchEvent(new Event('focus')); doc.dispatchEvent(new Event('visibilitychange'));
  assert.equal(checks, 6);
});

test('ordinary desktop heartbeat reuses the watchdog and records timer gaps', () => {
  const win = new EventTarget(), doc = new EventTarget(), lags=[];
  doc.visibilityState='visible';
  let tick, now=0;
  win.setInterval=fn=>{tick=fn;return 1;};win.clearInterval=()=>{};
  const stop=watchDesktopConnection(()=>{},win,doc,()=>now,lag=>lags.push(lag));
  now=1000;tick();assert.deepEqual(lags,[]);
  now=30000;tick();assert.deepEqual(lags,[28000]);
  now=31000;tick();assert.equal(lags.length,1);
  stop();
});
