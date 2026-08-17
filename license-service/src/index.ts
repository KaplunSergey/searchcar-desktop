import { constantTimeEqual, verifyDeviceProof } from "./crypto";
import {
  beginIdempotentRequest,
  cleanupExpiredState,
  completeIdempotentRequest,
  enforceRateLimit,
} from "./database";
import { ApiError, asApiError } from "./errors";
import { handleOwnerRoute } from "./owner_auth";
import { ownerPage } from "./owner_ui";
import {
  activateTrial,
  approveAdminTransfer,
  checkLicense,
  claimTransfer,
  createAdminActivationCode,
  createAdminCustomer,
  createAdminLicense,
  latestRelease,
  redeemActivationCode,
  requestTransfer,
} from "./service";
import type { ApiEnvelope, Env, SignedDeviceRequest } from "./types";
import {
  assertFreshRequest,
  asObject,
  parsePositiveInt,
  parseSignedDeviceRequest,
  requireString,
} from "./validation";

type PublicHandler = (
  request: Request,
  env: Env,
  body: SignedDeviceRequest & Record<string, unknown>,
  now: Date,
) => Promise<unknown>;

type AdminHandler = (env: Env, body: unknown, now: Date) => Promise<unknown>;

const PUBLIC_ROUTES = new Map<string, PublicHandler>([
  ["/v1/trials/activate", activateTrial],
  ["/v1/licenses/check", checkLicense],
  ["/v1/licenses/redeem", redeemActivationCode],
  ["/v1/transfers/request", requestTransfer],
  ["/v1/transfers/claim", claimTransfer],
]);

const ADMIN_ROUTES = new Map<string, AdminHandler>([
  ["/v1/admin/customers", createAdminCustomer],
  ["/v1/admin/licenses", createAdminLicense],
  ["/v1/admin/activation-codes", createAdminActivationCode],
  ["/v1/admin/transfers/approve", approveAdminTransfer],
]);

function responseHeaders(requestId?: string): Headers {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  });
  if (requestId) headers.set("X-Request-Id", requestId);
  return headers;
}

function jsonResponse(status: number, body: ApiEnvelope, requestId?: string): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: responseHeaders(requestId),
  });
}

function errorEnvelope(error: ApiError): ApiEnvelope {
  return { ok: false, error: { code: error.code, message: error.message } };
}

async function readJson(request: Request): Promise<unknown> {
  const contentType = request.headers.get("Content-Type")?.split(";", 1)[0].trim();
  if (contentType !== "application/json") {
    throw new ApiError(415, "JSON_REQUIRED", "Content-Type must be application/json.");
  }
  const contentLength = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > 16_384) {
    throw new ApiError(413, "REQUEST_TOO_LARGE", "The request body is too large.");
  }
  const text = await request.text();
  if (text.length > 16_384) {
    throw new ApiError(413, "REQUEST_TOO_LARGE", "The request body is too large.");
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(400, "INVALID_JSON", "The request body is not valid JSON.");
  }
}

function requireConfigured(env: Env): void {
  const required: Array<keyof Env> = [
    "LICENSE_SIGNING_PRIVATE_KEY",
    "LICENSE_SIGNING_PUBLIC_KEY",
    "LICENSE_SIGNING_KEY_ID",
    "CODE_PEPPER",
    "RATE_LIMIT_PEPPER",
    "ADMIN_API_TOKEN",
  ];
  for (const key of required) {
    if (typeof env[key] !== "string" || (env[key] as string).length < 12) {
      throw new ApiError(503, "SERVICE_NOT_CONFIGURED", "The license service is not configured.");
    }
  }
}

async function runPublicRoute(
  request: Request,
  env: Env,
  scope: string,
  handler: PublicHandler,
): Promise<Response> {
  requireConfigured(env);
  const now = new Date();
  await enforceRateLimit(request, env, scope, now);
  const rawBody = await readJson(request);
  const body = parseSignedDeviceRequest(rawBody);
  assertFreshRequest(body.requested_at, now);
  await verifyDeviceProof(body);
  if (scope === "/v1/trials/activate") {
    await enforceRateLimit(request, env, "trial-device", now, {
      windowSeconds: 60 * 60,
      limit: parsePositiveInt(env.TRIAL_RATE_LIMIT_PER_HOUR, 5, 100),
      subject: body.device.fingerprint_hash,
    });
  }
  const idempotencyKey = request.headers.get("Idempotency-Key");
  if (!idempotencyKey) {
    throw new ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required.");
  }
  const started = await beginIdempotentRequest(
    env,
    scope,
    idempotencyKey,
    body.request_id,
    rawBody,
    now,
  );
  if (started.cached) {
    return jsonResponse(started.cached.status, started.cached.body, body.request_id);
  }
  try {
    const data = await handler(request, env, body, now);
    const envelope: ApiEnvelope = { ok: true, data };
    await completeIdempotentRequest(env, started.context!, 200, envelope);
    return jsonResponse(200, envelope, body.request_id);
  } catch (caught) {
    const error = asApiError(caught);
    const envelope = errorEnvelope(error);
    await completeIdempotentRequest(env, started.context!, error.status, envelope);
    return jsonResponse(error.status, envelope, body.request_id);
  }
}

async function verifyAdmin(request: Request, env: Env): Promise<void> {
  requireConfigured(env);
  const authorization = request.headers.get("Authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) {
    throw new ApiError(401, "ADMIN_AUTH_REQUIRED", "Administrator authentication is required.");
  }
  if (!(await constantTimeEqual(authorization.slice(7), env.ADMIN_API_TOKEN))) {
    throw new ApiError(401, "ADMIN_AUTH_REQUIRED", "Administrator authentication is required.");
  }
}

async function runAdminRoute(
  request: Request,
  env: Env,
  scope: string,
  handler: AdminHandler,
): Promise<Response> {
  await verifyAdmin(request, env);
  const now = new Date();
  await enforceRateLimit(request, env, `admin:${scope}`, now, { limit: 60, subject: "admin" });
  const rawBody = await readJson(request);
  const idempotencyKey = request.headers.get("Idempotency-Key");
  const requestId = request.headers.get("X-Request-Id");
  if (!idempotencyKey || !requestId) {
    throw new ApiError(
      400,
      "REQUEST_IDENTIFIERS_REQUIRED",
      "Idempotency-Key and X-Request-Id are required.",
    );
  }
  requireString(asObject({ request_id: requestId }), "request_id", {
    min: 36,
    max: 36,
    pattern: /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/iu,
  });
  const started = await beginIdempotentRequest(
    env,
    scope,
    idempotencyKey,
    requestId,
    rawBody,
    now,
  );
  if (started.cached) return jsonResponse(started.cached.status, started.cached.body, requestId);
  try {
    const data = await handler(env, rawBody, now);
    const envelope: ApiEnvelope = { ok: true, data };
    await completeIdempotentRequest(env, started.context!, 200, envelope);
    return jsonResponse(200, envelope, requestId);
  } catch (caught) {
    const error = asApiError(caught);
    const envelope = errorEnvelope(error);
    await completeIdempotentRequest(env, started.context!, error.status, envelope);
    return jsonResponse(error.status, envelope, requestId);
  }
}

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/health") {
    return jsonResponse(200, {
      ok: true,
      data: { service: "searchcar-license-service", protocol_version: 1 },
    });
  }
  if (request.method === "GET" && url.pathname === "/v1/releases/latest") {
    requireConfigured(env);
    return jsonResponse(200, { ok: true, data: await latestRelease(env) });
  }
  if (request.method === "GET" && (url.pathname === "/owner" || url.pathname === "/owner/")) {
    return ownerPage();
  }
  if (url.pathname.startsWith("/v1/owner/")) return handleOwnerRoute(request, env);
  if (request.method !== "POST") {
    throw new ApiError(405, "METHOD_NOT_ALLOWED", "This endpoint only accepts POST.");
  }
  const publicHandler = PUBLIC_ROUTES.get(url.pathname);
  if (publicHandler) return runPublicRoute(request, env, url.pathname, publicHandler);
  const adminHandler = ADMIN_ROUTES.get(url.pathname);
  if (adminHandler) return runAdminRoute(request, env, url.pathname, adminHandler);
  throw new ApiError(404, "NOT_FOUND", "The endpoint was not found.");
}

const worker = {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await route(request, env);
    } catch (caught) {
      const error = asApiError(caught);
      return jsonResponse(error.status, errorEnvelope(error));
    }
  },

  async scheduled(
    _controller: ScheduledController,
    env: Env,
    context: ExecutionContext,
  ): Promise<void> {
    context.waitUntil(cleanupExpiredState(env));
  },
};

export default worker;
