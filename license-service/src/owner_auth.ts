import { base64UrlDecode, base64UrlEncode, constantTimeEqual, pepperedHash, randomToken } from "./crypto";
import {
  auditStatement,
  beginIdempotentRequest,
  completeIdempotentRequest,
  enforceRateLimit,
} from "./database";
import { ApiError, asApiError } from "./errors";
import {
  approveAdminTransfer,
  createAdminActivationCode,
  createAdminCustomer,
  createAdminLicense,
  deleteAdminLicense,
  diagnoseAdminActivationCode,
  setAdminLicenseSources,
} from "./service";
import type { ApiEnvelope, Env } from "./types";
import { asObject, requireString } from "./validation";

const encoder = new TextEncoder();
const OWNER_COOKIE = "__Host-searchcar_owner";
const SESSION_SECONDS = 12 * 60 * 60;

interface OwnerRow {
  id: string;
  login: string;
  password_hash: string;
  is_active: number;
}

interface OwnerSessionRow {
  session_id: string;
  admin_user_id: string;
  login: string;
  is_active: number;
  expires_at: string;
}

function jsonResponse(status: number, body: ApiEnvelope, cookie?: string): Response {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  });
  if (cookie) headers.append("Set-Cookie", cookie);
  return new Response(JSON.stringify(body), { status, headers });
}

function parseCookie(request: Request, name: string): string | null {
  const parts = (request.headers.get("Cookie") ?? "").split(";");
  for (const part of parts) {
    const [key, ...value] = part.trim().split("=");
    if (key === name) return value.join("=") || null;
  }
  return null;
}

function ownerCookie(token: string, maxAge: number): string {
  return `${OWNER_COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}

function normalizeLogin(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9._-]{2,63}$/u.test(normalized)) {
    throw new ApiError(400, "OWNER_LOGIN_INVALID", "The owner login is invalid.");
  }
  return normalized;
}

function readCredentials(value: unknown): { login: string; password: string } {
  const body = asObject(value);
  const login = normalizeLogin(requireString(body, "login", { min: 3, max: 64 }));
  const password = requireString(body, "password", { min: 12, max: 200 });
  return { login, password };
}

function requireOwnerConfigured(env: Env): void {
  if (typeof env.OWNER_PASSWORD_PEPPER !== "string" || env.OWNER_PASSWORD_PEPPER.length < 12) {
    throw new ApiError(503, "OWNER_SERVICE_NOT_CONFIGURED", "Owner authentication is not configured.");
  }
}

async function readOwnerJson(request: Request): Promise<unknown> {
  if (request.headers.get("Content-Type")?.split(";", 1)[0].trim() !== "application/json") {
    throw new ApiError(415, "JSON_REQUIRED", "Content-Type must be application/json.");
  }
  const length = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(length) && length > 4_096) {
    throw new ApiError(413, "REQUEST_TOO_LARGE", "The request body is too large.");
  }
  const text = await request.text();
  if (text.length > 4_096) throw new ApiError(413, "REQUEST_TOO_LARGE", "The request body is too large.");
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(400, "INVALID_JSON", "The request body is not valid JSON.");
  }
}

async function passwordDigest(env: Env, password: string, salt: string): Promise<string> {
  let saltBytes: ArrayBuffer;
  try {
    saltBytes = base64UrlDecode(salt);
  } catch {
    throw new ApiError(500, "OWNER_PASSWORD_STATE_INVALID", "Owner credentials are unavailable.");
  }
  if (saltBytes.byteLength !== 16) {
    throw new ApiError(500, "OWNER_PASSWORD_STATE_INVALID", "Owner credentials are unavailable.");
  }
  const digest = await crypto.subtle.digest(
    "SHA-256",
    encoder.encode(
      `SEARCHCAR-OWNER-PASSWORD-V2\u0000${salt}\u0000${env.OWNER_PASSWORD_PEPPER}\u0000${password}`,
    ),
  );
  return base64UrlEncode(digest);
}

async function createPasswordHash(env: Env, password: string): Promise<string> {
  const salt = randomToken(16);
  return `sha256-pepper-v2.${salt}.${await passwordDigest(env, password, salt)}`;
}

async function passwordMatches(env: Env, password: string, stored: string): Promise<boolean> {
  const [version, salt, digest, ...extra] = stored.split(".");
  if (version !== "sha256-pepper-v2" || !salt || !digest || extra.length > 0) return false;
  try {
    return constantTimeEqual(await passwordDigest(env, password, salt), digest);
  } catch {
    return false;
  }
}

async function requireBootstrap(request: Request, env: Env): Promise<void> {
  requireOwnerConfigured(env);
  const authorization = request.headers.get("Authorization") ?? "";
  if (!authorization.startsWith("Bearer ") || !(await constantTimeEqual(authorization.slice(7), env.ADMIN_API_TOKEN))) {
    throw new ApiError(401, "OWNER_BOOTSTRAP_AUTH_REQUIRED", "Bootstrap authorization is required.");
  }
}

async function requireSession(request: Request, env: Env): Promise<OwnerSessionRow> {
  requireOwnerConfigured(env);
  const token = parseCookie(request, OWNER_COOKIE);
  if (!token || !/^[A-Za-z0-9_-]{40,80}$/u.test(token)) {
    throw new ApiError(401, "OWNER_AUTH_REQUIRED", "Owner authentication is required.");
  }
  const now = new Date().toISOString();
  const row = await env.LICENSE_DB.prepare(
    `SELECT s.id AS session_id, s.admin_user_id, u.login, u.is_active, s.expires_at
       FROM admin_sessions s JOIN admin_users u ON u.id = s.admin_user_id
      WHERE s.token_hash = ? AND s.expires_at > ?`,
  )
    .bind(await pepperedHash(env.OWNER_PASSWORD_PEPPER, `session:${token}`), now)
    .first<OwnerSessionRow>();
  if (!row || row.is_active !== 1) {
    throw new ApiError(401, "OWNER_AUTH_REQUIRED", "Owner authentication is required.");
  }
  await env.LICENSE_DB.prepare("UPDATE admin_sessions SET last_seen_at = ? WHERE id = ?")
    .bind(now, row.session_id)
    .run();
  return row;
}

function requireSameOrigin(request: Request): void {
  const origin = request.headers.get("Origin");
  if (!origin || origin !== new URL(request.url).origin) {
    throw new ApiError(403, "OWNER_CSRF_REJECTED", "The request origin is not allowed.");
  }
}

async function createSession(env: Env, owner: OwnerRow, now: Date): Promise<string> {
  const token = randomToken(32);
  const nowIso = now.toISOString();
  const expiresAt = new Date(now.getTime() + SESSION_SECONDS * 1000).toISOString();
  await env.LICENSE_DB.prepare(
    `INSERT INTO admin_sessions
       (id, admin_user_id, token_hash, expires_at, created_at, last_seen_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      crypto.randomUUID(),
      owner.id,
      await pepperedHash(env.OWNER_PASSWORD_PEPPER, `session:${token}`),
      expiresAt,
      nowIso,
      nowIso,
    )
    .run();
  return ownerCookie(token, SESSION_SECONDS);
}

async function bootstrapOwner(request: Request, env: Env, now: Date): Promise<Response> {
  await requireBootstrap(request, env);
  await enforceRateLimit(request, env, "owner-bootstrap", now, { windowSeconds: 300, limit: 5 });
  const { login, password } = readCredentials(await readOwnerJson(request));
  const id = crypto.randomUUID();
  const nowIso = now.toISOString();
  let passwordHash: string;
  try {
    passwordHash = await createPasswordHash(env, password);
  } catch {
    throw new ApiError(
      503,
      "OWNER_PASSWORD_HASHING_FAILED",
      "Owner password setup is temporarily unavailable.",
    );
  }
  try {
    await env.LICENSE_DB.prepare(
      `INSERT INTO admin_users (id, login, password_hash, is_active, created_at, updated_at)
       VALUES (?, ?, ?, 1, ?, ?)`,
    ).bind(id, login, passwordHash, nowIso, nowIso).run();
  } catch {
    const existingOwner = await env.LICENSE_DB.prepare(
      "SELECT singleton FROM owner_bootstrap WHERE singleton = 1",
    ).first();
    if (existingOwner) throw new ApiError(409, "OWNER_ALREADY_CONFIGURED", "An owner is already configured.");
    throw new ApiError(409, "OWNER_LOGIN_ALREADY_USED", "The owner login is already in use.");
  }
  try {
    await env.LICENSE_DB.batch([
      env.LICENSE_DB.prepare(
        "INSERT INTO owner_bootstrap (singleton, admin_user_id, created_at) VALUES (1, ?, ?)",
      ).bind(id, nowIso),
      auditStatement(env, {
        id: crypto.randomUUID(),
        actorType: "ADMIN",
        actorId: id,
        action: "OWNER_BOOTSTRAPPED",
        targetType: "ADMIN_USER",
        targetId: id,
        createdAt: nowIso,
      }),
    ]);
  } catch {
    await env.LICENSE_DB.prepare(
      `DELETE FROM admin_users
        WHERE id = ? AND NOT EXISTS (
          SELECT 1 FROM owner_bootstrap WHERE admin_user_id = ?
        )`,
    ).bind(id, id).run();
    const existing = await env.LICENSE_DB.prepare("SELECT singleton FROM owner_bootstrap WHERE singleton = 1")
      .first();
    if (existing) throw new ApiError(409, "OWNER_ALREADY_CONFIGURED", "An owner is already configured.");
    throw new ApiError(503, "OWNER_BOOTSTRAP_WRITE_FAILED", "Owner setup could not be saved.");
  }
  return jsonResponse(201, { ok: true, data: { login } }, await createSession(env, { id, login, password_hash: "", is_active: 1 }, now));
}

async function loginOwner(request: Request, env: Env, now: Date): Promise<Response> {
  requireOwnerConfigured(env);
  await enforceRateLimit(request, env, "owner-login", now, { windowSeconds: 300, limit: 10 });
  const { login, password } = readCredentials(await readOwnerJson(request));
  const owner = await env.LICENSE_DB.prepare(
    "SELECT id, login, password_hash, is_active FROM admin_users WHERE login = ?",
  )
    .bind(login)
    .first<OwnerRow>();
  if (!owner || owner.is_active !== 1 || !(await passwordMatches(env, password, owner.password_hash))) {
    throw new ApiError(401, "OWNER_LOGIN_FAILED", "The credentials are not valid.");
  }
  return jsonResponse(200, { ok: true, data: { login: owner.login } }, await createSession(env, owner, now));
}

async function logoutOwner(request: Request, env: Env): Promise<Response> {
  requireSameOrigin(request);
  const session = await requireSession(request, env);
  await env.LICENSE_DB.prepare("DELETE FROM admin_sessions WHERE id = ?").bind(session.session_id).run();
  return jsonResponse(200, { ok: true, data: { logged_out: true } }, ownerCookie("", 0));
}

async function ownerSession(request: Request, env: Env): Promise<Response> {
  const session = await requireSession(request, env);
  return jsonResponse(200, { ok: true, data: { login: session.login } });
}

async function ownerStatus(env: Env): Promise<Response> {
  requireOwnerConfigured(env);
  const owner = await env.LICENSE_DB.prepare("SELECT singleton FROM owner_bootstrap WHERE singleton = 1").first();
  return jsonResponse(200, { ok: true, data: { configured: Boolean(owner) } });
}

type OwnerMutation = (body: unknown, now: Date, owner: OwnerSessionRow) => Promise<unknown>;

async function runOwnerMutation(
  request: Request,
  env: Env,
  scope: string,
  handler: OwnerMutation,
): Promise<Response> {
  requireSameOrigin(request);
  const owner = await requireSession(request, env);
  const now = new Date();
  await enforceRateLimit(request, env, `owner:${scope}`, now, {
    windowSeconds: 60,
    limit: 30,
    subject: owner.admin_user_id,
  });
  const body = await readOwnerJson(request);
  const idempotencyKey = request.headers.get("Idempotency-Key");
  const requestId = request.headers.get("X-Request-Id");
  if (!idempotencyKey || !requestId) {
    throw new ApiError(400, "REQUEST_IDENTIFIERS_REQUIRED", "Request identifiers are required.");
  }
  requireString(asObject({ request_id: requestId }), "request_id", {
    min: 36,
    max: 36,
    pattern: /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/iu,
  });
  const started = await beginIdempotentRequest(env, `owner:${scope}`, idempotencyKey, requestId, body, now);
  if (started.cached) return jsonResponse(started.cached.status, started.cached.body);
  try {
    const data = await handler(body, now, owner);
    const envelope: ApiEnvelope = { ok: true, data };
    await completeIdempotentRequest(env, started.context!, 200, envelope);
    return jsonResponse(200, envelope);
  } catch (caught) {
    const error = asApiError(caught);
    const envelope: ApiEnvelope = { ok: false, error: { code: error.code, message: error.message } };
    await completeIdempotentRequest(env, started.context!, error.status, envelope);
    return jsonResponse(error.status, envelope);
  }
}

async function ownerDashboard(request: Request, env: Env): Promise<Response> {
  await requireSession(request, env);
  const [customers, licenses, codes, transfers, devices, auditEvents, sources, licenseSources] = await Promise.all([
    env.LICENSE_DB.prepare(
      `SELECT id, display_name, contact, created_at, updated_at
         FROM customers ORDER BY updated_at DESC LIMIT 100`,
    ).all(),
    env.LICENSE_DB.prepare(
      `SELECT l.id, l.customer_id, c.display_name AS customer_name, l.kind, l.status,
              l.expires_at, l.perpetual, l.activation_count, l.updated_at,
              l.deleted_at,
              d.id AS active_device_id, d.label AS active_device_label, d.last_seen_at
         FROM licenses l LEFT JOIN customers c ON c.id = l.customer_id
         LEFT JOIN devices d ON d.license_id = l.id AND d.is_active = 1
         ORDER BY l.updated_at DESC LIMIT 100`,
    ).all(),
    env.LICENSE_DB.prepare(
      `SELECT ac.id, ac.license_id, ac.code_hint, ac.duration_months, ac.makes_perpetual,
              ac.expires_at, ac.created_at, ac.used_at, l.kind AS license_kind,
              c.display_name AS customer_name
         FROM activation_codes ac JOIN licenses l ON l.id = ac.license_id
         LEFT JOIN customers c ON c.id = l.customer_id
         ORDER BY ac.created_at DESC LIMIT 100`,
    ).all(),
    env.LICENSE_DB.prepare(
      `SELECT t.id, t.public_code_hint, t.status, t.requested_label, t.requested_at,
              t.expires_at, t.license_id, c.display_name AS customer_name
         FROM device_transfers t LEFT JOIN licenses l ON l.id = t.license_id
         LEFT JOIN customers c ON c.id = l.customer_id
         ORDER BY t.requested_at DESC LIMIT 100`,
    ).all(),
    env.LICENSE_DB.prepare(
      `SELECT d.id, d.license_id, d.label, d.is_active, d.first_seen_at, d.last_seen_at,
              d.deactivated_at, l.kind AS license_kind, c.display_name AS customer_name
         FROM devices d JOIN licenses l ON l.id = d.license_id
         LEFT JOIN customers c ON c.id = l.customer_id
         ORDER BY d.last_seen_at DESC LIMIT 200`,
    ).all(),
    env.LICENSE_DB.prepare(
      `SELECT action, target_type, target_id, outcome, created_at
         FROM audit_events ORDER BY created_at DESC LIMIT 200`,
    ).all(),
    env.LICENSE_DB.prepare(
      `SELECT source_key, display_name, is_active
         FROM source_catalog ORDER BY display_name ASC`,
    ).all(),
    env.LICENSE_DB.prepare(
      `SELECT license_id, source_key
         FROM license_sources ORDER BY license_id ASC, source_key ASC`,
    ).all(),
  ]);
  return jsonResponse(200, {
    ok: true,
    data: {
      customers: customers.results,
      licenses: licenses.results,
      activation_codes: codes.results,
      transfers: transfers.results,
      devices: devices.results,
      audit_events: auditEvents.results,
      sources: sources.results,
      license_sources: licenseSources.results,
    },
  });
}

export async function handleOwnerRoute(request: Request, env: Env): Promise<Response> {
  const path = new URL(request.url).pathname;
  const now = new Date();
  try {
    if (request.method === "POST" && path === "/v1/owner/bootstrap") {
      return bootstrapOwner(request, env, now);
    }
    if (request.method === "POST" && path === "/v1/owner/login") {
      return loginOwner(request, env, now);
    }
    if (request.method === "POST" && path === "/v1/owner/logout") {
      return logoutOwner(request, env);
    }
    if (request.method === "GET" && path === "/v1/owner/session") {
      return ownerSession(request, env);
    }
    if (request.method === "GET" && path === "/v1/owner/status") {
      return ownerStatus(env);
    }
    if (request.method === "GET" && path === "/v1/owner/dashboard") {
      return ownerDashboard(request, env);
    }
    if (request.method === "POST" && path === "/v1/owner/customers") {
      return runOwnerMutation(request, env, "customers", (body, now, owner) =>
        createAdminCustomer(env, body, now, owner.admin_user_id),
      );
    }
    if (request.method === "POST" && path === "/v1/owner/licenses") {
      return runOwnerMutation(request, env, "licenses", (body, now, owner) =>
        createAdminLicense(env, body, now, owner.admin_user_id),
      );
    }
    if (request.method === "POST" && path === "/v1/owner/licenses/delete") {
      return runOwnerMutation(request, env, "licenses/delete", (body, now, owner) =>
        deleteAdminLicense(env, body, now, owner.admin_user_id),
      );
    }
    if (request.method === "POST" && path === "/v1/owner/activation-codes") {
      return runOwnerMutation(request, env, "activation-codes", (body, now, owner) =>
        createAdminActivationCode(env, body, now, owner.admin_user_id),
      );
    }
    if (request.method === "POST" && path === "/v1/owner/license-sources") {
      return runOwnerMutation(request, env, "license-sources", (body, now, owner) =>
        setAdminLicenseSources(env, body, now, owner.admin_user_id),
      );
    }
    if (request.method === "POST" && path === "/v1/owner/activation-codes/diagnose") {
      return runOwnerMutation(request, env, "activation-codes-diagnose", (body, now) =>
        diagnoseAdminActivationCode(env, body, now),
      );
    }
    if (request.method === "POST" && path === "/v1/owner/transfers/approve") {
      return runOwnerMutation(request, env, "transfers/approve", (body, now, owner) =>
        approveAdminTransfer(env, body, now, owner.admin_user_id),
      );
    }
    throw new ApiError(404, "NOT_FOUND", "The endpoint was not found.");
  } catch (caught) {
    const error = asApiError(caught);
    return jsonResponse(error.status, { ok: false, error: { code: error.code, message: error.message } });
  }
}
