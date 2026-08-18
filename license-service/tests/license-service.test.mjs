import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import net from "node:net";
import { after, before, test } from "node:test";
import { webcrypto } from "node:crypto";
import { Miniflare } from "miniflare";

const encoder = new TextEncoder();
const migrations = await Promise.all(
  ["0001_initial.sql", "0002_owner_admin.sql"].map((name) =>
    readFile(new URL(`../migrations/${name}`, import.meta.url), "utf8"),
  ),
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
  const publicKeyBytes = new Uint8Array(
    await webcrypto.subtle.exportKey("raw", keys.publicKey),
  );
  const publicKey = base64Url(publicKeyBytes);
  const prefix = encoder.encode("SEARCHCAR-DEVICE-FINGERPRINT-V1\n");
  const fingerprintInput = new Uint8Array(prefix.byteLength + publicKeyBytes.byteLength);
  fingerprintInput.set(prefix, 0);
  fingerprintInput.set(publicKeyBytes, prefix.byteLength);
  const fingerprintHash = Buffer.from(
    await webcrypto.subtle.digest("SHA-256", fingerprintInput),
  ).toString("hex");
  return {
    keys,
    device: {
      public_key: publicKey,
      fingerprint_hash: fingerprintHash,
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
let signingPublicKeyValue;
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
  signingPublicKeyValue = base64Url(
    await webcrypto.subtle.exportKey("raw", signingKeys.publicKey),
  );
  const privateKey = base64Url(await webcrypto.subtle.exportKey("pkcs8", signingKeys.privateKey));
  mf = new Miniflare({
    modules: true,
    scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
    compatibilityDate: "2026-05-15",
    d1Databases: { LICENSE_DB: "license-tests" },
    bindings: {
      LICENSE_SIGNING_PRIVATE_KEY: privateKey,
      LICENSE_SIGNING_PUBLIC_KEY: signingPublicKeyValue,
      LICENSE_SIGNING_KEY_ID: "test-key-v1-01",
      CODE_PEPPER: "test-code-pepper-with-enough-entropy",
      RATE_LIMIT_PEPPER: "test-rate-pepper-with-enough-entropy",
      ADMIN_API_TOKEN: "test-admin-token-with-enough-entropy",
      OWNER_PASSWORD_PEPPER: "test-owner-password-pepper-with-enough-entropy",
      LEASE_HOURS: "48",
      TRIAL_DAYS: "30",
      PUBLIC_RATE_LIMIT_PER_MINUTE: "100",
      TRIAL_RATE_LIMIT_PER_HOUR: "100",
    },
  });
  await mf.ready;
  database = await mf.getD1Database("LICENSE_DB");
  for (const migration of migrations) {
    for (const statement of splitMigrationStatements(migration)) {
      await database.prepare(statement).run();
    }
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

async function ownerApi(path, body, cookie) {
  const response = await mf.dispatchFetch(`https://license.test${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Cookie: cookie,
      Origin: "https://license.test",
      "Idempotency-Key": crypto.randomUUID(),
      "X-Request-Id": crypto.randomUUID(),
      "CF-Connecting-IP": "203.0.113.25",
    },
    body: JSON.stringify(body),
  });
  return { response, json: await response.json() };
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

test("Worker signing identity matches the desktop trust store", async () => {
  const [workerDocument, desktopDocument] = await Promise.all([
    readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"),
    readFile(new URL("../../desktop/license-service.json", import.meta.url), "utf8"),
  ]);
  const worker = JSON.parse(workerDocument);
  const desktop = JSON.parse(desktopDocument);
  const keyId = worker.vars.LICENSE_SIGNING_KEY_ID;
  assert.equal(keyId, "searchcar-license-v1");
  assert.equal(worker.vars.LICENSE_SIGNING_PUBLIC_KEY, desktop.public_keys[keyId]);
});

test("owner bootstrap creates a protected cookie session exactly once", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const password = "correct-horse-battery-staple-owner";
  const created = await api(
    "/v1/owner/bootstrap",
    { login: "searchcar-owner", password },
    { Authorization: "Bearer test-admin-token-with-enough-entropy" },
  );
  assert.equal(created.response.status, 201);
  assert.equal(created.json.data.login, "searchcar-owner");
  const cookie = created.response.headers.get("Set-Cookie");
  assert.match(cookie, /__Host-searchcar_owner=[A-Za-z0-9_-]{43}; Path=\/; HttpOnly; Secure; SameSite=Strict/u);

  const invalidLogin = await api("/v1/owner/login", {
    login: "searchcar-owner",
    password: "wrong-password-which-is-long-enough",
  });
  assert.equal(invalidLogin.response.status, 401);
  assert.equal(invalidLogin.json.error.code, "OWNER_LOGIN_FAILED");

  const login = await api("/v1/owner/login", { login: "searchcar-owner", password });
  assert.equal(login.response.status, 200);
  const loginCookie = login.response.headers.get("Set-Cookie");
  const session = await mf.dispatchFetch("https://license.test/v1/owner/session", {
    headers: { Cookie: loginCookie.split(";", 1)[0] },
  });
  assert.equal(session.status, 200);
  assert.equal((await session.json()).data.login, "searchcar-owner");

  const status = await mf.dispatchFetch("https://license.test/v1/owner/status");
  assert.equal(status.status, 200);
  assert.equal((await status.json()).data.configured, true);

  const ownerCookie = loginCookie.split(";", 1)[0];
  const customer = await ownerApi(
    "/v1/owner/customers",
    { display_name: "Owner dashboard customer", contact: "owner-dashboard@example.test" },
    ownerCookie,
  );
  assert.equal(customer.response.status, 200);
  const sameCustomer = await ownerApi(
    "/v1/owner/customers",
    { display_name: "Owner dashboard customer again", contact: "owner-dashboard@example.test" },
    ownerCookie,
  );
  assert.equal(sameCustomer.response.status, 200);
  assert.equal(sameCustomer.json.data.customer_id, customer.json.data.customer_id);
  assert.equal(sameCustomer.json.data.already_exists, true);
  const license = await ownerApi(
    "/v1/owner/licenses",
    { customer_id: customer.json.data.customer_id },
    ownerCookie,
  );
  assert.equal(license.response.status, 200);
  const activation = await ownerApi(
    "/v1/owner/activation-codes",
    { license_id: license.json.data.license_id, duration: "P1M" },
    ownerCookie,
  );
  assert.equal(activation.response.status, 200);
  assert.match(activation.json.data.activation_code, /^SC-/u);
  const storedActivation = await database.prepare(
    "SELECT code_hash FROM activation_codes WHERE id = ?",
  ).bind(activation.json.data.activation_code_id).first();
  const expectedActivationHash = Buffer.from(
    await webcrypto.subtle.digest(
      "SHA-256",
      encoder.encode(`SEARCHCAR-ACTIVATION-CODE-V2\n${activation.json.data.activation_code}`),
    ),
  ).toString("base64url");
  assert.equal(storedActivation.code_hash, expectedActivationHash);
  const diagnostic = await ownerApi(
    "/v1/owner/activation-codes/diagnose",
    { activation_code: activation.json.data.activation_code },
    ownerCookie,
  );
  assert.equal(diagnostic.response.status, 200);
  assert.equal(diagnostic.json.data.matched_by, "STABLE_V2");
  assert.equal(diagnostic.json.data.availability, "AVAILABLE");
  assert.equal(
    diagnostic.json.data.activation_code_id,
    activation.json.data.activation_code_id,
  );
  const dashboard = await mf.dispatchFetch("https://license.test/v1/owner/dashboard", {
    headers: { Cookie: ownerCookie },
  });
  assert.equal(dashboard.status, 200);
  const dashboardData = (await dashboard.json()).data;
  assert.equal(dashboardData.customers.some((row) => row.id === customer.json.data.customer_id), true);
  assert.equal(dashboardData.audit_events.some((row) => row.action === "ACTIVATION_CODE_CREATED"), true);

  const logout = await mf.dispatchFetch("https://license.test/v1/owner/logout", {
    method: "POST",
    headers: {
      Cookie: ownerCookie,
      Origin: "https://license.test",
    },
  });
  assert.equal(logout.status, 200);
  assert.match(logout.headers.get("Set-Cookie"), /Max-Age=0/u);

  const repeated = await api(
    "/v1/owner/bootstrap",
    { login: "another-owner", password },
    { Authorization: "Bearer test-admin-token-with-enough-entropy" },
  );
  assert.equal(repeated.response.status, 409);
  assert.equal(repeated.json.error.code, "OWNER_ALREADY_CONFIGURED");
});

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

  const recovered = await api(
    "/v1/trials/activate",
    await signedBody(device, { subject_hash: "a".repeat(64) }),
  );
  assert.equal(recovered.response.status, 200);
  assert.equal(recovered.json.data.license_id, first.json.data.license_id);
  assert.equal(recovered.json.data.device_id, first.json.data.device_id);
  await verifyLease(recovered.json.data.lease);

  const changedSubject = await api(
    "/v1/trials/activate",
    await signedBody(device, { subject_hash: "b".repeat(64) }),
  );
  assert.equal(changedSubject.response.status, 409);
  assert.equal(changedSubject.json.error.code, "TRIAL_UNAVAILABLE");

  const otherDevice = await createDevice("trial-other-device");
  const reusedSubject = await api(
    "/v1/trials/activate",
    await signedBody(otherDevice, { subject_hash: "a".repeat(64) }),
  );
  assert.equal(reusedSubject.response.status, 409);
  assert.equal(reusedSubject.json.error.code, "TRIAL_UNAVAILABLE");

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

test("concurrent exact trial retries converge on one binding", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const device = await createDevice("trial-concurrent-device");
  const subjectHash = "9".repeat(64);
  const [left, right] = await Promise.all([
    api("/v1/trials/activate", await signedBody(device, { subject_hash: subjectHash })),
    api("/v1/trials/activate", await signedBody(device, { subject_hash: subjectHash })),
  ]);
  assert.equal(left.response.status, 200);
  assert.equal(right.response.status, 200);
  assert.equal(left.json.data.license_id, right.json.data.license_id);
  assert.equal(left.json.data.device_id, right.json.data.device_id);
  const count = await database
    .prepare("SELECT COUNT(*) AS count FROM trial_claims WHERE subject_hash = ?")
    .bind(subjectHash)
    .first("count");
  assert.equal(count, 1);
});

test("invalid signing key does not consume a trial", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const broken = new Miniflare({
    modules: true,
    scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
    compatibilityDate: "2026-05-15",
    d1Databases: { LICENSE_DB: "license-invalid-signing-tests" },
    bindings: {
      LICENSE_SIGNING_PRIVATE_KEY: "not-a-valid-private-key",
      LICENSE_SIGNING_PUBLIC_KEY: signingPublicKeyValue,
      LICENSE_SIGNING_KEY_ID: "test-key-v1-01",
      CODE_PEPPER: "test-code-pepper-with-enough-entropy",
      RATE_LIMIT_PEPPER: "test-rate-pepper-with-enough-entropy",
      ADMIN_API_TOKEN: "test-admin-token-with-enough-entropy",
      LEASE_HOURS: "48",
      TRIAL_DAYS: "30",
      PUBLIC_RATE_LIMIT_PER_MINUTE: "100",
      TRIAL_RATE_LIMIT_PER_HOUR: "100",
    },
  });
  try {
    await broken.ready;
    const brokenDatabase = await broken.getD1Database("LICENSE_DB");
    for (const migration of migrations) {
      for (const statement of splitMigrationStatements(migration)) {
        await brokenDatabase.prepare(statement).run();
      }
    }
    const device = await createDevice("invalid-signing-device");
    const body = await signedBody(device, { subject_hash: "d".repeat(64) });
    const response = await broken.dispatchFetch(
      "https://license.test/v1/trials/activate",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
          "CF-Connecting-IP": "203.0.113.26",
        },
        body: JSON.stringify(body),
      },
    );
    const result = await response.json();
    assert.equal(response.status, 500);
    assert.equal(result.error.code, "SIGNING_KEY_INVALID");
    for (const table of ["licenses", "devices", "trial_claims"]) {
      const count = await brokenDatabase
        .prepare(`SELECT COUNT(*) AS count FROM ${table}`)
        .first("count");
      assert.equal(count, 0);
    }
  } finally {
    await broken.dispose();
  }
});

test("mismatched signing pair consumes neither trial nor activation code", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const wrongKeys = await webcrypto.subtle.generateKey(
    { name: "Ed25519" },
    true,
    ["sign", "verify"],
  );
  const wrongPrivateKey = base64Url(
    await webcrypto.subtle.exportKey("pkcs8", wrongKeys.privateKey),
  );
  const mismatched = new Miniflare({
    modules: true,
    scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
    compatibilityDate: "2026-05-15",
    d1Databases: { LICENSE_DB: "license-mismatched-signing-tests" },
    bindings: {
      LICENSE_SIGNING_PRIVATE_KEY: wrongPrivateKey,
      LICENSE_SIGNING_PUBLIC_KEY: signingPublicKeyValue,
      LICENSE_SIGNING_KEY_ID: "test-key-v1-01",
      CODE_PEPPER: "test-code-pepper-with-enough-entropy",
      RATE_LIMIT_PEPPER: "test-rate-pepper-with-enough-entropy",
      ADMIN_API_TOKEN: "test-admin-token-with-enough-entropy",
      LEASE_HOURS: "48",
      TRIAL_DAYS: "30",
      PUBLIC_RATE_LIMIT_PER_MINUTE: "100",
      TRIAL_RATE_LIMIT_PER_HOUR: "100",
    },
  });
  const mismatchedApi = async (path, body, headers = {}) => {
    const response = await mismatched.dispatchFetch(`https://license.test${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
        "CF-Connecting-IP": "203.0.113.27",
        ...headers,
      },
      body: JSON.stringify(body),
    });
    return { response, json: await response.json() };
  };
  try {
    await mismatched.ready;
    const mismatchedDatabase = await mismatched.getD1Database("LICENSE_DB");
    for (const migration of migrations) {
      for (const statement of splitMigrationStatements(migration)) {
        await mismatchedDatabase.prepare(statement).run();
      }
    }

    const trialDevice = await createDevice("mismatched-trial-device");
    const trial = await mismatchedApi(
      "/v1/trials/activate",
      await signedBody(trialDevice, { subject_hash: "f".repeat(64) }),
    );
    assert.equal(trial.response.status, 500);
    assert.equal(trial.json.error.code, "SIGNING_KEY_MISMATCH");
    assert.equal(
      await mismatchedDatabase.prepare("SELECT COUNT(*) AS count FROM trial_claims").first("count"),
      0,
    );

    const adminHeaders = {
      Authorization: "Bearer test-admin-token-with-enough-entropy",
      "X-Request-Id": crypto.randomUUID(),
    };
    const customer = await mismatchedApi(
      "/v1/admin/customers",
      { display_name: "Mismatch customer" },
      adminHeaders,
    );
    const license = await mismatchedApi(
      "/v1/admin/licenses",
      { customer_id: customer.json.data.customer_id },
      { ...adminHeaders, "X-Request-Id": crypto.randomUUID() },
    );
    const code = await mismatchedApi(
      "/v1/admin/activation-codes",
      { license_id: license.json.data.license_id, duration: "P1M" },
      { ...adminHeaders, "X-Request-Id": crypto.randomUUID() },
    );
    const redeemDevice = await createDevice("mismatched-redeem-device");
    const redeemed = await mismatchedApi(
      "/v1/licenses/redeem",
      await signedBody(redeemDevice, {
        activation_code: code.json.data.activation_code,
      }),
    );
    assert.equal(redeemed.response.status, 500);
    assert.equal(redeemed.json.error.code, "SIGNING_KEY_MISMATCH");
    const storedCode = await mismatchedDatabase
      .prepare("SELECT used_at FROM activation_codes WHERE license_id = ?")
      .bind(license.json.data.license_id)
      .first();
    assert.equal(storedCode.used_at, null);
    assert.equal(
      await mismatchedDatabase.prepare("SELECT COUNT(*) AS count FROM renewals").first("count"),
      0,
    );
  } finally {
    await mismatched.dispose();
  }
});

test("device fingerprint must be derived from the signed public key", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const device = await createDevice("forged-fingerprint-device");
  device.device.fingerprint_hash = "0".repeat(64);
  const result = await api(
    "/v1/trials/activate",
    await signedBody(device, { subject_hash: "e".repeat(64) }),
  );

  assert.equal(result.response.status, 400);
  assert.equal(result.json.error.code, "INVALID_DEVICE_FINGERPRINT");
  const claimCount = await database
    .prepare("SELECT COUNT(*) AS count FROM trial_claims WHERE subject_hash = ?")
    .bind("e".repeat(64))
    .first("count");
  assert.equal(claimCount, 0);
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
  const winner = left.response.status === 200
    ? { result: left, device: deviceA }
    : { result: right, device: deviceB };
  const recovered = await api(
    "/v1/licenses/redeem",
    await signedBody(winner.device, { activation_code: code.json.data.activation_code }),
  );
  assert.equal(recovered.response.status, 200);
  assert.equal(recovered.json.data.license_id, winner.result.json.data.license_id);
  assert.equal(recovered.json.data.device_id, winner.result.json.data.device_id);
  await verifyLease(recovered.json.data.lease);
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

test("concurrent same-device redemption retries converge on one renewal", async (context) => {
  if (!localRuntimeAvailable) return context.skip("loopback sockets are blocked by this sandbox");
  const customer = await admin("/v1/admin/customers", { display_name: "Retry customer" });
  const license = await admin("/v1/admin/licenses", {
    customer_id: customer.json.data.customer_id,
  });
  const code = await admin("/v1/admin/activation-codes", {
    license_id: license.json.data.license_id,
    duration: "P1M",
  });
  const device = await createDevice("redeem-retry-device");
  const [left, right] = await Promise.all([
    api(
      "/v1/licenses/redeem",
      await signedBody(device, { activation_code: code.json.data.activation_code }),
    ),
    api(
      "/v1/licenses/redeem",
      await signedBody(device, { activation_code: code.json.data.activation_code }),
    ),
  ]);

  assert.equal(left.response.status, 200);
  assert.equal(right.response.status, 200);
  assert.equal(left.json.data.license_id, right.json.data.license_id);
  assert.equal(left.json.data.device_id, right.json.data.device_id);
  const renewalCount = await database
    .prepare("SELECT COUNT(*) AS count FROM renewals WHERE license_id = ?")
    .bind(license.json.data.license_id)
    .first("count");
  assert.equal(renewalCount, 1);
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
