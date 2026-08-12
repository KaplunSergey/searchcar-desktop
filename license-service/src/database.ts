import {
  canonicalJson,
  openIdempotencyResponse,
  pepperedHash,
  sealIdempotencyResponse,
  sha256,
} from "./crypto";
import { ApiError } from "./errors";
import type { ApiEnvelope, Env } from "./types";
import { parsePositiveInt } from "./validation";

interface IdempotencyRow {
  request_hash: string;
  state: "PROCESSING" | "COMPLETED";
  response_status: number | null;
  response_json: string | null;
}

export interface IdempotencyContext {
  scope: string;
  key: string;
}

export interface CachedResponse {
  status: number;
  body: ApiEnvelope;
}

export async function beginIdempotentRequest(
  env: Env,
  scope: string,
  key: string,
  requestId: string,
  body: unknown,
  now: Date,
): Promise<{ context?: IdempotencyContext; cached?: CachedResponse }> {
  if (key.length < 8 || key.length > 128 || !/^[A-Za-z0-9._:-]+$/u.test(key)) {
    throw new ApiError(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key is invalid.");
  }
  const requestHash = await sha256(canonicalJson(body));
  const existing = await env.LICENSE_DB.prepare(
    `SELECT request_hash, state, response_status, response_json
       FROM idempotency_keys
      WHERE scope = ? AND idempotency_key = ?`,
  )
    .bind(scope, key)
    .first<IdempotencyRow>();
  if (existing) return resolveExisting(env, existing, requestHash);

  const expiresAt = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();
  try {
    await env.LICENSE_DB.batch([
      env.LICENSE_DB.prepare(
        `INSERT INTO idempotency_keys
           (scope, idempotency_key, request_hash, state, created_at, expires_at)
         VALUES (?, ?, ?, 'PROCESSING', ?, ?)`,
      ).bind(scope, key, requestHash, now.toISOString(), expiresAt),
      env.LICENSE_DB.prepare(
        `INSERT INTO request_nonces (scope, request_id, created_at, expires_at)
         VALUES (?, ?, ?, ?)`,
      ).bind(scope, requestId, now.toISOString(), expiresAt),
    ]);
  } catch {
    const raced = await env.LICENSE_DB.prepare(
      `SELECT request_hash, state, response_status, response_json
         FROM idempotency_keys
        WHERE scope = ? AND idempotency_key = ?`,
    )
      .bind(scope, key)
      .first<IdempotencyRow>();
    if (raced) return resolveExisting(env, raced, requestHash);
    throw new ApiError(409, "REPLAY_DETECTED", "The request identifier was already used.");
  }
  return { context: { scope, key } };
}

async function resolveExisting(
  env: Env,
  existing: IdempotencyRow,
  requestHash: string,
): Promise<{ cached?: CachedResponse }> {
  if (existing.request_hash !== requestHash) {
    throw new ApiError(
      409,
      "IDEMPOTENCY_KEY_REUSED",
      "The idempotency key was used for a different request.",
    );
  }
  if (existing.state !== "COMPLETED" || existing.response_json === null) {
    throw new ApiError(409, "REQUEST_IN_PROGRESS", "The original request is still processing.");
  }
  return {
    cached: {
      status: existing.response_status ?? 200,
      body: JSON.parse(
        await openIdempotencyResponse(env.CODE_PEPPER, existing.response_json),
      ) as ApiEnvelope,
    },
  };
}

export async function completeIdempotentRequest(
  env: Env,
  context: IdempotencyContext,
  status: number,
  body: ApiEnvelope,
): Promise<void> {
  await env.LICENSE_DB.prepare(
    `UPDATE idempotency_keys
        SET state = 'COMPLETED', response_status = ?, response_json = ?
      WHERE scope = ? AND idempotency_key = ? AND state = 'PROCESSING'`,
  )
    .bind(
      status,
      await sealIdempotencyResponse(env.CODE_PEPPER, canonicalJson(body)),
      context.scope,
      context.key,
    )
    .run();
}

export async function enforceRateLimit(
  request: Request,
  env: Env,
  scope: string,
  now: Date,
  options: { windowSeconds?: number; limit?: number; subject?: string } = {},
): Promise<void> {
  const windowSeconds = options.windowSeconds ?? 60;
  const defaultLimit = parsePositiveInt(env.PUBLIC_RATE_LIMIT_PER_MINUTE, 30, 1000);
  const limit = options.limit ?? defaultLimit;
  const rawSubject =
    options.subject ?? request.headers.get("CF-Connecting-IP") ?? "unknown-local-address";
  const subjectHash = await pepperedHash(env.RATE_LIMIT_PEPPER, rawSubject);
  const epochSeconds = Math.floor(now.getTime() / 1000);
  const windowStart = epochSeconds - (epochSeconds % windowSeconds);
  const expiresAt = new Date((windowStart + windowSeconds * 2) * 1000).toISOString();
  await env.LICENSE_DB.prepare(
    `INSERT INTO rate_limit_windows
       (scope, subject_hash, window_start, request_count, expires_at)
     VALUES (?, ?, ?, 1, ?)
     ON CONFLICT(scope, subject_hash, window_start)
     DO UPDATE SET request_count = request_count + 1`,
  )
    .bind(scope, subjectHash, windowStart, expiresAt)
    .run();
  const row = await env.LICENSE_DB.prepare(
    `SELECT request_count FROM rate_limit_windows
      WHERE scope = ? AND subject_hash = ? AND window_start = ?`,
  )
    .bind(scope, subjectHash, windowStart)
    .first<{ request_count: number }>();
  if ((row?.request_count ?? limit + 1) > limit) {
    throw new ApiError(429, "RATE_LIMITED", "Too many requests. Try again later.");
  }
}

export async function ipHash(request: Request, env: Env): Promise<string | null> {
  const address = request.headers.get("CF-Connecting-IP");
  return address ? pepperedHash(env.RATE_LIMIT_PEPPER, address) : null;
}

export function auditStatement(
  env: Env,
  event: {
    id: string;
    dedupeKey?: string;
    actorType: "DEVICE" | "ADMIN" | "SYSTEM";
    actorId?: string;
    action: string;
    targetType: string;
    targetId?: string;
    outcome?: string;
    metadata?: Record<string, unknown>;
    createdAt: string;
  },
): D1PreparedStatement {
  return env.LICENSE_DB.prepare(
    `INSERT INTO audit_events
       (id, dedupe_key, actor_type, actor_id, action, target_type, target_id,
        outcome, metadata_json, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    event.id,
    event.dedupeKey ?? null,
    event.actorType,
    event.actorId ?? null,
    event.action,
    event.targetType,
    event.targetId ?? null,
    event.outcome ?? "SUCCESS",
    canonicalJson(event.metadata ?? {}),
    event.createdAt,
  );
}

export async function cleanupExpiredState(env: Env, now = new Date()): Promise<void> {
  const timestamp = now.toISOString();
  await env.LICENSE_DB.batch([
    env.LICENSE_DB.prepare("DELETE FROM idempotency_keys WHERE expires_at < ?").bind(timestamp),
    env.LICENSE_DB.prepare("DELETE FROM request_nonces WHERE expires_at < ?").bind(timestamp),
    env.LICENSE_DB.prepare("DELETE FROM rate_limit_windows WHERE expires_at < ?").bind(timestamp),
    env.LICENSE_DB.prepare(
      `UPDATE device_transfers SET status = 'EXPIRED'
        WHERE status = 'PENDING' AND expires_at < ?`,
    ).bind(timestamp),
    env.LICENSE_DB.prepare("DELETE FROM admin_sessions WHERE expires_at < ?").bind(timestamp),
  ]);
}
