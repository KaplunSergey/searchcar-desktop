import assert from 'node:assert/strict';
import test from 'node:test';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { desktopConnectionState, desktopRuntimeOptions, watchDesktopConnection } from '../app/desktop-connection.ts';

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
