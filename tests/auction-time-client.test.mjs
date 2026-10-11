import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { QueryClient } from "@tanstack/react-query";
import { AUCTION_QUERY_KEY, computerTimeZone, withAuctionTimeZone, watchAuctionClock } from "../app/auction-time-client.ts";

test("runtime reads a fresh Intl formatter instead of caching the startup zone", (t) => {
  let zone = "Europe/Warsaw";
  let calls = 0;
  t.mock.method(Intl, "DateTimeFormat", function () {
    calls += 1;
    return { resolvedOptions: () => ({ timeZone: zone }) };
  });
  assert.equal(computerTimeZone(), "Europe/Warsaw");
  zone = "America/Los_Angeles";
  assert.equal(computerTimeZone(), "America/Los_Angeles");
  assert.equal(calls, 2);
});

test("missing/abbreviated/offset timezone is unknown, never an invented UTC fallback", (t) => {
  let zone;
  t.mock.method(Intl, "DateTimeFormat", function () {
    return { resolvedOptions: () => ({ timeZone: zone }) };
  });
  for (const value of [undefined, "", "CST", "+02:00", "../Warsaw", "a".repeat(65)]) {
    zone = value;
    assert.equal(computerTimeZone(), null);
  }
  zone = "UTC";
  assert.equal(computerTimeZone(), "UTC");
  zone = "GMT"; // Intl's zero-offset alias, not a guessed US timezone.
  assert.equal(computerTimeZone(), "UTC");
  t.mock.method(Intl, "DateTimeFormat", () => { throw new Error("Intl unavailable"); });
  assert.equal(computerTimeZone(), null);
});

test("only auction reads carry the fresh runtime zone and retain other query filters", () => {
  let zone = "Europe/Warsaw";
  const read = () => zone;
  const first = new URL(withAuctionTimeZone("/projects/12/auction-lots?today_only=true", read), "https://local.test");
  assert.equal(first.searchParams.get("computer_timezone"), zone);
  assert.equal(first.searchParams.get("today_only"), "true");
  zone = "America/Los_Angeles";
  const next = new URL(withAuctionTimeZone("/cars/42/auction?project_id=12&computer_timezone=Europe%2FWarsaw", read), "https://local.test");
  assert.equal(next.searchParams.get("computer_timezone"), zone);
  assert.equal(next.searchParams.get("project_id"), "12");
  for (const path of ["/projects", "/projects/12/cars?status=NEW", "/cars/42", "/scans/projects", "/desktop/license"]) {
    assert.equal(withAuctionTimeZone(path, () => { throw new Error("should not read zone"); }), path);
  }
  assert.throws(() => withAuctionTimeZone("/projects/12/auction-lots", () => null), /computer_timezone_unavailable/);
});

test("wake/focus/visibility and periodic clock refresh use current zone; cleanup removes handlers", () => {
  const win = new EventTarget();
  const doc = new EventTarget();
  doc.visibilityState = "visible";
  let tick;
  let cleared;
  win.setInterval = (callback, delay) => { assert.equal(delay, 60000); tick = callback; return 42; };
  win.clearInterval = (id) => { cleared = id; };
  let zone = "Europe/Warsaw";
  const seen = [];
  const stop = watchAuctionClock((value) => seen.push(value), () => zone, win, doc);
  assert.deepEqual(seen, [zone]);
  zone = "America/Chicago";
  win.dispatchEvent(new Event("focus"));
  assert.equal(seen.at(-1), zone);
  tick(); // Midnight/countdown refresh even when timezone itself is unchanged.
  assert.equal(seen.length, 3);
  doc.visibilityState = "hidden";
  tick();
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(seen.length, 3);
  zone = "Asia/Tokyo";
  doc.visibilityState = "visible";
  doc.dispatchEvent(new Event("visibilitychange"));
  win.dispatchEvent(new Event("pageshow"));
  assert.deepEqual(seen.slice(-2), [zone, zone]);
  zone = null;
  tick();
  assert.equal(seen.at(-1), null);
  const count = seen.length;
  stop();
  assert.equal(cleared, 42);
  tick();
  win.dispatchEvent(new Event("focus"));
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(seen.length, count);
});

test("refresh invalidates only the auction query family, leaving existing Encar caches alone", () => {
  const client = new QueryClient();
  client.setQueryData([...AUCTION_QUERY_KEY, "project", 1], { fixture: true });
  client.setQueryData([...AUCTION_QUERY_KEY, "lot", 42], { fixture: true });
  client.setQueryData(["project-cars", 1], ["legacy encar"]);
  client.setQueryData(["scans"], []);
  const win = new EventTarget();
  const doc = new EventTarget();
  doc.visibilityState = "visible";
  win.setInterval = () => 42;
  win.clearInterval = () => {};
  const stop = watchAuctionClock(() => void client.invalidateQueries({ queryKey: AUCTION_QUERY_KEY }), () => "UTC", win, doc);
  assert.equal(client.getQueryState([...AUCTION_QUERY_KEY, "project", 1]).isInvalidated, true);
  assert.equal(client.getQueryState([...AUCTION_QUERY_KEY, "lot", 42]).isInvalidated, true);
  assert.equal(client.getQueryState(["project-cars", 1]).isInvalidated, false);
  assert.equal(client.getQueryState(["scans"]).isInvalidated, false);
  stop();
  client.clear();
});

test("native Intl runtime reflects a changed timezone in a fresh isolated Node process", () => {
  const moduleUrl = new URL("../app/auction-time-client.ts", import.meta.url).href;
  const result = execFileSync(process.execPath, ["--input-type=module", "-e", `
    import { computerTimeZone, withAuctionTimeZone } from ${JSON.stringify(moduleUrl)};
    process.env.TZ = "Europe/Warsaw";
    const first = computerTimeZone();
    process.env.TZ = "America/Los_Angeles";
    const second = computerTimeZone();
    console.log(JSON.stringify([first, second, withAuctionTimeZone("/projects/1/auction-lots")]));
  `], { encoding: "utf8" });
  const [first, second, path] = JSON.parse(result);
  assert.equal(first, "Europe/Warsaw");
  assert.equal(second, "America/Los_Angeles");
  assert.equal(new URL(path, "https://local.test").searchParams.get("computer_timezone"), second);
});
