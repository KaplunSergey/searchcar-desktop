import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import net from "node:net";
import { after, before, test } from "node:test";
import { webcrypto } from "node:crypto";
import { Miniflare } from "miniflare";

const encoder = new TextEncoder();
const migration = await readFile(
  new URL("../migrations/0001_initial.sql", import.meta.url),
  "utf8",
);

function splitMigrationStatements(sql) {
  const statements = [];
  let start = 0;
  let quote = null;

  for (let index = 0; index < sql.length; index += 1) {
    const character = sql[index];
    if (quote !== null) {
      if (character === quote) {
        if (sql[index + 1] === quote) {
          index += 1;
        } else {
          quote = null;
        }
      }
      continue;
    }
    if (character === "'" || character === '"' || character === "`") {
      quote = character;
      continue;
    }
    if (character !== ";") continue;
    const statement = sql.slice(start, index).trim();
    if (statement) statements.push(statement);
    start = index + 1;
  }

  const finalStatement = sql.slice(start).trim();
  if (finalStatement) statements.push(finalStatement);
  return statements;
}

function base64Url(bytes) {
  return Buffer.from(bytes).toString("base64url");
}

function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value)
    .sort()
    .filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(",")}}`;
}

async function createDevice(label) {
  const keys = await webcrypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const publicKey = base64Url(await webcrypto.subtle.exportKey("raw", keys.publicKey));
  return {
    keys,
    device: {
      public_key: publicKey,
      fingerprint_hash: Buffer.from(`${label}:fingerprint`.padEnd(32, "0")).toString("hex").slice(0, 64),
      label,
    },
  };
}

async function signedBody(device, extra = {}, requestId = crypto.randomUUID()) {
  const unsigned = {
    protocol_version: 1,
    request_id: requestId,
    requested_at: new Date().toISOString(),
    app_version: "0.1.0-test",
    device: device.device,
    ...extra,
  };
  const signature = await webcrypto.subtle.sign(
    { name: "Ed25519" },
    device.keys.privateKey,
    encoder.encode(`SEARCHCAR-DEVICE-REQUEST-V1\n${canonicalJson(unsigned)}`),
  );
  return { ...unsigned, proof: base64Url(signature) };
}

let mf;
let database;
let signingPublicKey;
let localRuntimeAvailable = false;

async function canBindLoopback() {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.listen(0, "127.0.0.1", () => {
      server.close(() => resolve(true));
    });
  });
}

before(async () => {
  if (!(await canBindLoopback())) return;
  const signingKeys = await webcrypto.subtle.generateKey({ name: "Ed25519" }, true, [
    "sign",
    "verify",
  ]);
  signingPublicKey = signingKeys.publicKey;
  const privateKey = base64Url(await webcrypto.subtle.exportKey("pkcs8", signingKeys.privateKey));
  mf = new Miniflare({
    modules: true,
    scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
    compatibilityDate: "2026-05-15",
    d1Databases: { LICENSE_DB: "license-tests" },
    bindings: {
      LICENSE_SIGNING_PRIVATE_KEY: privateKey,
      LICENSE_SIGNING_KEY_ID: "test-key-v1",
      CODE_PEPPER: "test-code-pepper-with-enough-entropy",
      RATE_LIMIT_PEPPER: "test-rate-pepper-with-enough-entropy",
      ADMIN_API_TOKEN: "test-admin-token-with-enough-entropy",
      LEASE_HOURS: "48",
      TRIAL_DAYS: "30",
      PUBLIC_RATE_LIMIT_PER_MINUTE: "100",
      TRIAL_RATE_LIMIT_PER_HOUR: "100",
    },
  });
  await mf.ready;
  database = await mf.getD1Database("LICENSE_DB");
  for (const statement of splitMigrationStatements(migration)) {
    await database.prepare(statement).run();
  }
  localRuntimeAvailable = true;
});

after(async () => {
  await mf?.dispose();
});

async function api(path, body, headers = {}) {
  const response = await mf.dispatchFetch(`https://license.test${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
      "CF-Connecting-IP": "203.0.113.25",
      ...headers,
    },
    body: JSON.stringify(body),
  });
  return { response, json: await response.json() };
}

async function admin(path, body) {
  return api(path, body, {
    Authorization: "Bearer test-admin-token-with-enough-entropy",
    "X-Request-Id": crypto.randomUUID(),
  });
}

async function verifyLease(lease) {
  const valid = await webcrypto.subtle.verify(
    { name: "Ed25519" },
    signingPublicKey,
    Buffer.from(lease.signature, "base64url"),
    encoder.encode(`SEARCHCAR-LICENSE-LEASE-V1\n${canonicalJson(lease.payload)}`),
  );
  assert.equal(valid, true);
  assert.equal(lease.payload.server_time, lease.payload.issued_at);
  assert.equal(lease.payload.entitlements.data_access, true);
}

test("trial is single-use, idempotent and returns a verifiable server-time lease", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const device = await createDevice("trial-device");
  const requestId = crypto.randomUUID();
  const body = await signedBody(
    device,
    { subject_hash: "a".repeat(64) },
    requestId,
  );
  const idempotencyKey = crypto.randomUUID();
  const first = await api("/v1/trials/activate", body, { "Idempotency-Key": idempotencyKey });
  assert.equal(first.response.status, 200);
  assert.equal(first.json.ok, true);
  assert.equal(first.json.data.lease.payload.entitlements.search, true);
  await verifyLease(first.json.data.lease);

  const repeated = await api("/v1/trials/activate", body, { "Idempotency-Key": idempotencyKey });
  assert.equal(repeated.response.status, 200);
  assert.deepEqual(repeated.json, first.json);

  const replayed = await api("/v1/trials/activate", body);
  assert.equal(replayed.response.status, 409);
  assert.equal(replayed.json.error.code, "REPLAY_DETECTED");

  const secondAttempt = await api(
    "/v1/trials/activate",
    await signedBody(device, { subject_hash: "b".repeat(64) }),
  );
  assert.equal(secondAttempt.response.status, 409);
  assert.equal(secondAttempt.json.error.code, "TRIAL_UNAVAILABLE");

  const checked = await api(
    "/v1/licenses/check",
    await signedBody(device, {
      license_id: first.json.data.license_id,
      device_id: first.json.data.device_id,
    }),
  );
  assert.equal(checked.response.status, 200);
  await verifyLease(checked.json.data.lease);
});

test("concurrent redemption applies a one-time code exactly once", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const customer = await admin("/v1/admin/customers", { display_name: "Test customer" });
  assert.equal(customer.response.status, 200);
  const license = await admin("/v1/admin/licenses", {
    customer_id: customer.json.data.customer_id,
  });
  const code = await admin("/v1/admin/activation-codes", {
    license_id: license.json.data.license_id,
    duration: "P1M",
  });
  const storedCodeResponse = await database
    .prepare(
      "SELECT response_json FROM idempotency_keys WHERE scope = '/v1/admin/activation-codes' ORDER BY created_at DESC LIMIT 1",
    )
    .first("response_json");
  assert.match(storedCodeResponse, /^v1\./u);
  assert.equal(storedCodeResponse.includes(code.json.data.activation_code), false);
  const deviceA = await createDevice("redeem-a");
  const deviceB = await createDevice("redeem-b");
  const [left, right] = await Promise.all([
    api(
      "/v1/licenses/redeem",
      await signedBody(deviceA, { activation_code: code.json.data.activation_code }),
    ),
    api(
      "/v1/licenses/redeem",
      await signedBody(deviceB, { activation_code: code.json.data.activation_code }),
    ),
  ]);
  assert.deepEqual(
    [left.response.status, right.response.status].sort((a, b) => a - b),
    [200, 409],
  );
  const renewalCount = await database
    .prepare("SELECT COUNT(*) AS count FROM renewals WHERE license_id = ?")
    .bind(license.json.data.license_id)
    .first("count");
  assert.equal(renewalCount, 1);
  const activeDeviceCount = await database
    .prepare("SELECT COUNT(*) AS count FROM devices WHERE license_id = ? AND is_active = 1")
    .bind(license.json.data.license_id)
    .first("count");
  assert.equal(activeDeviceCount, 1);
});

test("approved transfer atomically moves the binding and invalidates old checks", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const oldDevice = await createDevice("transfer-old");
  const trial = await api(
    "/v1/trials/activate",
    await signedBody(oldDevice, { subject_hash: "c".repeat(64) }),
  );
  assert.equal(trial.response.status, 200);
  const newDevice = await createDevice("transfer-new");
  const requested = await api(
    "/v1/transfers/request",
    await signedBody(newDevice),
  );
  assert.equal(requested.response.status, 200);
  const storedTransferResponse = await database
    .prepare(
      "SELECT response_json FROM idempotency_keys WHERE scope = '/v1/transfers/request' ORDER BY created_at DESC LIMIT 1",
    )
    .first("response_json");
  assert.match(storedTransferResponse, /^v1\./u);
  assert.equal(storedTransferResponse.includes(requested.json.data.claim_token), false);

  const pendingClaim = await api(
    "/v1/transfers/claim",
    await signedBody(newDevice, {
      transfer_code: requested.json.data.transfer_code,
      claim_token: requested.json.data.claim_token,
    }),
  );
  assert.equal(pendingClaim.response.status, 409);
  assert.equal(pendingClaim.json.error.code, "TRANSFER_PENDING");

  const approved = await admin("/v1/admin/transfers/approve", {
    transfer_code: requested.json.data.transfer_code,
    license_id: trial.json.data.license_id,
  });
  assert.equal(approved.response.status, 200);

  const claimed = await api(
    "/v1/transfers/claim",
    await signedBody(newDevice, {
      transfer_code: requested.json.data.transfer_code,
      claim_token: requested.json.data.claim_token,
    }),
  );
  assert.equal(claimed.response.status, 200);
  assert.equal(claimed.json.data.license_id, trial.json.data.license_id);
  await verifyLease(claimed.json.data.lease);

  const oldCheck = await api(
    "/v1/licenses/check",
    await signedBody(oldDevice, {
      license_id: trial.json.data.license_id,
      device_id: trial.json.data.device_id,
    }),
  );
  assert.equal(oldCheck.response.status, 403);
  assert.equal(oldCheck.json.error.code, "LICENSE_NOT_AVAILABLE");

  const binding = await database
    .prepare(
      `SELECT COUNT(*) AS active_count, MAX(l.activation_count) AS activation_count
         FROM devices d JOIN licenses l ON l.id = d.license_id
        WHERE d.license_id = ? AND d.is_active = 1`,
    )
    .bind(trial.json.data.license_id)
    .first();
  assert.equal(binding.active_count, 1);
  assert.equal(binding.activation_count, 2);
});
