import { constantTimeEqual, randomToken, sha256, verifyDeviceProof } from "../../license-service/src/crypto";
import { ApiError } from "../../license-service/src/errors";
import { asObject, assertFreshRequest, parseSignedDeviceRequest, requireString } from "../../license-service/src/validation";
import { deliver } from "./delivery";
import { ChatCheckRateLimit, KeyboardSendError, sendChatKeyboard, verifyChat } from "./telegram-api";

const AUDIENCE = "searchcar-telegram-pairing-v1";
const TTL = 10 * 60 * 1000;
const ID = /^[a-f0-9]{32}$/u;
const UUID = /^[a-f0-9-]{36}$/u;
type Principal = { license: string; device: string; key: string; owner: number; project: number };
type Pairing = {
  id: string; project_id: number; expires_at: number; chat_id: number | null; title: string | null;
  binding_id: string | null; confirmed_at: number | null; revoked_at: number | null;
  target_kind: string; chat_type: string; requested_by: number | null; request_chat_id: number | null;
  shared_chat_id: number | null;
};

function positive(value: unknown): number {
  if (!Number.isSafeInteger(value) || typeof value !== "number" || value <= 0 || value >= 2 ** 52) {
    throw new ApiError(400, "INVALID_REQUEST", "Invalid identifier.");
  }
  return value;
}

async function readJson(request: Request): Promise<unknown> {
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    throw new ApiError(415, "JSON_REQUIRED", "JSON required.");
  }
  if (!request.body) throw new ApiError(400, "INVALID_REQUEST", "Body required.");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > 16384) {
        await reader.cancel(); throw new ApiError(413, "REQUEST_TOO_LARGE", "Request too large.");
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)); }
  catch { throw new ApiError(400, "INVALID_JSON", "Invalid JSON."); }
}

async function authenticate(env: Env, value: unknown, now: number, audience = AUDIENCE) {
  if (!env.LICENSE_AUTH) throw new ApiError(503, "SERVICE_NOT_CONFIGURED", "Authorization is not configured.");
  const body = parseSignedDeviceRequest(value);
  assertFreshRequest(body.requested_at, new Date(now));
  await verifyDeviceProof(body);
  if (body.audience !== audience) throw new ApiError(401, "INVALID_AUDIENCE", "Wrong audience.");
  const telegram = asObject(body.telegram);
  const principal: Principal = {
    license: requireString(body, "license_id", { pattern: UUID }),
    device: requireString(body, "device_id", { pattern: UUID }), key: body.device.public_key,
    owner: positive(telegram.owner_id), project: positive(telegram.project_id),
  };
  let allowed: unknown;
  try { allowed = await env.LICENSE_AUTH.authorize(value); }
  catch { throw new ApiError(503, "AUTHORIZATION_UNAVAILABLE", "Authorization unavailable."); }
  if (typeof allowed !== "boolean") throw new ApiError(503, "AUTHORIZATION_UNAVAILABLE", "Authorization unavailable.");
  if (!allowed) {
    throw new ApiError(403, "DEVICE_NOT_AUTHORIZED", "Device not authorized.");
  }
  const window = Math.floor(now / 60000) * 60000;
  const nonce = await env.PAIRING_DB.prepare(`INSERT OR IGNORE INTO request_nonces VALUES(?,?,?)`)
    .bind(principal.key, body.request_id, now + TTL).run();
  if (nonce.meta.changes !== 1) throw new ApiError(409, "REQUEST_REPLAYED", "Request replayed.");
  // Infrastructure abuse protection, not shared-bot delivery scheduling.
  const count = await env.PAIRING_DB.prepare(`INSERT INTO security_windows VALUES(?,?,1)
      ON CONFLICT(public_key,window_at) DO UPDATE SET attempts=attempts+1 RETURNING attempts`)
    .bind(principal.key, window).first<{ attempts: number }>();
  if (!count || count.attempts > 120) throw new ApiError(429, "PAIRING_RATE_LIMITED", "Retry later.");
  await env.PAIRING_DB.batch([
    env.PAIRING_DB.prepare("DELETE FROM request_nonces WHERE expires_at<?").bind(now),
    env.PAIRING_DB.prepare("DELETE FROM security_windows WHERE window_at<?").bind(window - 60000),
    env.PAIRING_DB.prepare(`DELETE FROM pairing_requests WHERE expires_at<? AND
      (confirmed_at IS NULL OR revoked_at IS NOT NULL)`).bind(now - 86400000),
  ]);
  return { telegram, principal };
}

function view(row: Pairing, botId: number, now: number) {
  const status = row.revoked_at !== null ? "REVOKED" : row.confirmed_at !== null ? "CONNECTED"
    : row.expires_at <= now ? "EXPIRED" : row.chat_id !== null ? "AWAITING_CONFIRMATION" : "WAITING_START";
  const visible = status === "CONNECTED" || status === "AWAITING_CONFIRMATION";
  return {
    pairing_id: row.id, project_id: row.project_id, bot_id: botId, status, expires_at_ms: row.expires_at,
    binding_id: status === "CONNECTED" ? row.binding_id : null,
    target_kind: row.target_kind,
    recipient: visible ? { chat_id: row.chat_id, chat_type: row.chat_type, title: row.title } : null,
  };
}

async function pairings(env: Env, value: unknown, now: number) {
  const { telegram, principal: p } = await authenticate(env, value, now);
  const action = requireString(telegram, "action", { max: 10 });
  const botId = positive(Number(env.BOT_ID));
  if (action === "reuse") {
    const id = requireString(telegram, "pairing_id", { pattern: ID });
    const source = requireString(telegram, "source_pairing_id", { pattern: ID });
    if (id === source) throw new ApiError(409, "PAIRING_CHANGED", "Pairing changed.");
    const existing = await env.PAIRING_DB.prepare(`SELECT * FROM pairing_requests WHERE id=? AND reuse_source_id=?
      AND license_id=? AND device_id=? AND public_key=? AND owner_id=? AND project_id=?
      AND confirmed_at IS NOT NULL AND revoked_at IS NULL`)
      .bind(id, source, p.license, p.device, p.key, p.owner, p.project).first<Pairing>();
    if (existing) {
      if (existing.target_kind !== "private") await verifyChat(env, existing.chat_id!, existing.requested_by, existing.target_kind);
      return view(existing, botId, now);
    }
    const original = await env.PAIRING_DB.prepare(`SELECT * FROM pairing_requests WHERE id=? AND
      license_id=? AND device_id=? AND public_key=? AND owner_id=? AND confirmed_at IS NOT NULL AND revoked_at IS NULL`)
      .bind(source, p.license, p.device, p.key, p.owner).first<Pairing>();
    if (!original) throw new ApiError(409, "PAIRING_CHANGED", "Reconnect recipient.");
    if (original.target_kind !== "private") await verifyChat(env, original.chat_id!, original.requested_by, original.target_kind);
    // INSERT SELECT and retirement share one transaction with Stop/revoke. No copied grant ID.
    const results = await env.PAIRING_DB.batch<Pairing>([
      env.PAIRING_DB.prepare(`INSERT OR IGNORE INTO pairing_requests
        (id,license_id,device_id,public_key,owner_id,project_id,token_hash,created_at,expires_at,
         chat_id,title,claim_message_at,consent_update_id,binding_id,confirmed_at,reuse_source_id,target_kind,chat_type,requested_by,shared_chat_id)
        SELECT ?,license_id,device_id,public_key,owner_id,?,?,?, ?,chat_id,title,claim_message_at,
          coalesce(consent_update_id,claim_update_id),?,?,id,target_kind,chat_type,requested_by,shared_chat_id FROM pairing_requests
        WHERE id=? AND license_id=? AND device_id=? AND public_key=? AND owner_id=?
          AND confirmed_at IS NOT NULL AND revoked_at IS NULL`)
        .bind(id, p.project, await sha256(randomToken()), now, now + TTL,
          crypto.randomUUID().replaceAll("-", ""), now, source, p.license, p.device, p.key, p.owner),
      env.PAIRING_DB.prepare(`UPDATE pairing_requests SET revoked_at=? WHERE id<>? AND license_id=?
        AND device_id=? AND public_key=? AND owner_id=? AND project_id=? AND revoked_at IS NULL
        AND EXISTS(SELECT 1 FROM pairing_requests WHERE id=? AND reuse_source_id=? AND revoked_at IS NULL
          AND license_id=? AND device_id=? AND public_key=? AND owner_id=? AND project_id=?)`)
        .bind(now, id, p.license, p.device, p.key, p.owner, p.project,
          id, source, p.license, p.device, p.key, p.owner, p.project),
      env.PAIRING_DB.prepare(`SELECT * FROM pairing_requests WHERE id=? AND reuse_source_id=?
        AND license_id=? AND device_id=? AND public_key=? AND owner_id=? AND project_id=?
        AND confirmed_at IS NOT NULL AND revoked_at IS NULL`)
        .bind(id, source, p.license, p.device, p.key, p.owner, p.project),
    ]);
    const row = results[2].results[0];
    if (!row) throw new ApiError(409, "PAIRING_CHANGED", "Reconnect recipient.");
    return view(row, botId, now);
  }
  if (action === "start") {
    if (Object.keys(telegram).some(key => !["action", "owner_id", "project_id", "target_kind"].includes(key))) {
      throw new ApiError(400, "INVALID_REQUEST", "Invalid request.");
    }
    const kind = telegram.target_kind ?? "private";
    if (typeof kind !== "string" || !["private", "group", "channel"].includes(kind)) throw new ApiError(400, "INVALID_REQUEST", "Invalid kind.");
    if (kind !== "private" && env.CHAT_PAIRING_ENABLED !== "1") throw new ApiError(503, "CHAT_PAIRING_DISABLED", "Chat pairing unavailable.");
    const id = crypto.randomUUID().replaceAll("-", ""), token = randomToken();
    const requestId = kind === "private" ? null : crypto.getRandomValues(new Int32Array(1))[0];
    // Replacing a pending link does not revoke an already confirmed recipient.
    await env.PAIRING_DB.batch([
      env.PAIRING_DB.prepare(`UPDATE pairing_requests SET revoked_at=? WHERE license_id=? AND device_id=?
        AND public_key=? AND owner_id=? AND project_id=? AND confirmed_at IS NULL AND revoked_at IS NULL`)
        .bind(now, p.license, p.device, p.key, p.owner, p.project),
      env.PAIRING_DB.prepare(`INSERT INTO pairing_requests
        (id,license_id,device_id,public_key,owner_id,project_id,token_hash,created_at,expires_at,target_kind,request_chat_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)`).bind(id, p.license, p.device, p.key, p.owner, p.project, await sha256(token), now, now + TTL, kind, requestId),
    ]);
    return { ...view({ id, project_id: p.project, expires_at: now + TTL, chat_id: null, title: null,
      binding_id: null, confirmed_at: null, revoked_at: null, target_kind: kind, chat_type: "private",
      requested_by: null, request_chat_id: requestId, shared_chat_id: null }, botId, now),
      start_url: `https://t.me/search_car_app_bot?start=p_${token}` };
  }
  if (!["status", "confirm", "revoke"].includes(action)) throw new ApiError(400, "INVALID_ACTION", "Invalid action.");
  const id = requireString(telegram, "pairing_id", { pattern: ID });
  const row = await env.PAIRING_DB.prepare(`SELECT * FROM pairing_requests WHERE id=? AND license_id=?
    AND device_id=? AND public_key=? AND owner_id=? AND project_id=?`)
    .bind(id, p.license, p.device, p.key, p.owner, p.project).first<Pairing>();
  if (!row) throw new ApiError(404, "PAIRING_NOT_FOUND", "Pairing not found.");
  if (action !== "revoke" && row.target_kind !== "private" && env.CHAT_PAIRING_ENABLED !== "1") {
    throw new ApiError(503, "CHAT_PAIRING_DISABLED", "Chat pairing unavailable.");
  }
  if (action !== "revoke" && row.target_kind !== "private" && row.revoked_at === null
      && (row.confirmed_at !== null || row.expires_at > now) && row.shared_chat_id !== null) {
    const checked = await verifyChat(env, row.shared_chat_id, row.requested_by, row.target_kind);
    if (row.chat_id === null) {
      const updated = await env.PAIRING_DB.prepare(`UPDATE pairing_requests SET chat_id=?,chat_type=?,title=?
        WHERE id=? AND revoked_at IS NULL AND confirmed_at IS NULL AND expires_at>? AND shared_chat_id=? RETURNING *`)
        .bind(checked.chat_id, checked.chat_type, checked.title, row.id, now, row.shared_chat_id).first<Pairing>();
      if (!updated) throw new ApiError(409, "PAIRING_CHANGED", "Pairing changed.");
      Object.assign(row, updated);
    }
  }
  if (action === "confirm") {
    const expectedChat = telegram.chat_id;
    if (typeof expectedChat !== "number" || !Number.isSafeInteger(expectedChat) || !expectedChat || Math.abs(expectedChat) >= 2 ** 52) {
      throw new ApiError(400, "INVALID_REQUEST", "Invalid chat.");
    }
    if (row.revoked_at !== null || row.chat_id !== expectedChat || (row.confirmed_at === null && row.expires_at <= now)) {
      throw new ApiError(409, "PAIRING_NOT_CONFIRMABLE", "Pairing not confirmable.");
    }
    if (row.confirmed_at === null) {
      const binding = crypto.randomUUID().replaceAll("-", "");
      const results = await env.PAIRING_DB.batch<Pairing>([
        env.PAIRING_DB.prepare(`UPDATE pairing_requests SET binding_id=?,confirmed_at=?
        WHERE id=? AND revoked_at IS NULL AND confirmed_at IS NULL AND expires_at>? AND chat_id=?
        RETURNING *`).bind(binding, now, id, now, expectedChat),
        env.PAIRING_DB.prepare(`UPDATE pairing_requests SET revoked_at=? WHERE id<>? AND license_id=?
          AND device_id=? AND public_key=? AND owner_id=? AND project_id=? AND confirmed_at IS NOT NULL
          AND revoked_at IS NULL AND EXISTS(SELECT 1 FROM pairing_requests WHERE id=? AND binding_id=?)`)
          .bind(now, id, p.license, p.device, p.key, p.owner, p.project, id, binding),
      ]);
      const updated = results[0].results[0];
      if (!updated) throw new ApiError(409, "PAIRING_CHANGED", "Pairing changed; refresh state.");
      return view(updated, botId, now);
    }
  } else if (action === "revoke") {
    await env.PAIRING_DB.prepare("UPDATE pairing_requests SET revoked_at=coalesce(revoked_at,?) WHERE id=?")
      .bind(now, id).run();
    row.revoked_at = row.revoked_at ?? now;
  }
  return view(row, botId, now);
}

async function webhook(env: Env, request: Request, now: number) {
  if (!env.TELEGRAM_WEBHOOK_SECRET || !await constantTimeEqual(request.headers.get("x-telegram-bot-api-secret-token") ?? "", env.TELEGRAM_WEBHOOK_SECRET)) {
    throw new ApiError(401, "WEBHOOK_UNAUTHORIZED", "Webhook unauthorized.");
  }
  const value = asObject(await readJson(request));
  if (!Number.isSafeInteger(value.update_id) || typeof value.update_id !== "number" || value.update_id < 0) {
    throw new ApiError(400, "INVALID_UPDATE", "Invalid update.");
  }
  if (!value.message || typeof value.message !== "object" || Array.isArray(value.message)) return;
  const message = asObject(value.message);
  if (!message.chat || !message.from) return;
  const chat = asObject(message.chat), from = asObject(message.from);
  if (from.is_bot !== false || typeof from.id !== "number" || !Number.isSafeInteger(from.id) || from.id <= 0 || from.id >= 2 ** 52
      || typeof message.date !== "number" || !Number.isSafeInteger(message.date)
      || message.date * 1000 < now - TTL || message.date * 1000 > now + 60000
      || message.via_bot || message.forward_origin || message.sender_chat) return;
  const match = typeof message.text === "string" ? /^\/start(?:@search_car_app_bot)? p_([A-Za-z0-9_-]{32})$/u.exec(message.text) : null;
  if (chat.type === "group" || chat.type === "supergroup") {
    if (!match || env.CHAT_PAIRING_ENABLED !== "1" || typeof chat.id !== "number" || !Number.isSafeInteger(chat.id)
        || chat.id >= 0 || chat.id <= -(2 ** 52)) return;
    // Native add-bot Start must match the earlier private claimant; permissions and app confirmation still follow.
    await env.PAIRING_DB.prepare(`UPDATE pairing_requests SET shared_chat_id=?,claim_message_at=?,consent_update_id=?
      WHERE token_hash=? AND target_kind='group' AND requested_by=? AND revoked_at IS NULL
      AND confirmed_at IS NULL AND shared_chat_id IS NULL AND expires_at>? AND created_at<=?
      AND (claim_message_at<? OR (claim_message_at=? AND claim_update_id<?))`)
      .bind(chat.id, message.date, value.update_id, await sha256(match[1]), from.id, now,
        message.date * 1000 + 999, message.date, message.date, value.update_id).run();
    return;
  }
  if (chat.type !== "private" || chat.id !== from.id) return;
  if (message.text === "/stop" || message.text === "/stop@search_car_app_bot") {
    // A delayed /stop cannot revoke consent granted by a newer Start.
    await env.PAIRING_DB.prepare(`UPDATE pairing_requests SET revoked_at=coalesce(revoked_at,?)
      WHERE (chat_id=? OR requested_by=?) AND (claim_message_at<? OR (claim_message_at=? AND coalesce(consent_update_id,claim_update_id)<?))`)
      .bind(now, chat.id, chat.id, message.date, message.date, value.update_id).run();
    return;
  }
  if (message.chat_shared && typeof message.chat_shared === "object" && !Array.isArray(message.chat_shared)) {
    if (env.CHAT_PAIRING_ENABLED !== "1") return;
    const shared = asObject(message.chat_shared);
    if (typeof shared.request_id !== "number" || !Number.isInteger(shared.request_id) || shared.request_id < -(2 ** 31) || shared.request_id >= 2 ** 31
        || typeof shared.chat_id !== "number" || !Number.isSafeInteger(shared.chat_id) || shared.chat_id >= 0 || shared.chat_id <= -(2 ** 52)) return;
    await env.PAIRING_DB.prepare(`UPDATE pairing_requests SET shared_chat_id=?,claim_message_at=?,consent_update_id=?
      WHERE request_chat_id=? AND requested_by=? AND target_kind<>'private' AND revoked_at IS NULL
      AND confirmed_at IS NULL AND shared_chat_id IS NULL AND expires_at>?
      AND (claim_message_at<? OR (claim_message_at=? AND claim_update_id<?))`)
      .bind(shared.chat_id, message.date, value.update_id, shared.request_id, chat.id, now, message.date, message.date, value.update_id).run();
    return;
  }
  if (typeof message.text !== "string") return;
  if (!match) return;
  const hash = await sha256(match[1]);
  if (env.CHAT_PAIRING_ENABLED === "1") {
    const group = await env.PAIRING_DB.prepare(`UPDATE pairing_requests SET requested_by=?,claim_update_id=?,claim_message_at=?
      WHERE token_hash=? AND target_kind<>'private' AND revoked_at IS NULL AND confirmed_at IS NULL
      AND requested_by IS NULL AND expires_at>? AND created_at<=? RETURNING *`)
      .bind(chat.id, value.update_id, message.date, hash, now, message.date * 1000 + 999).first<Pairing>();
    if (group) return sendChatKeyboard(env, chat.id, group.request_chat_id!, group.target_kind === "channel", match[1]);
    const prior = await env.PAIRING_DB.prepare(`SELECT * FROM pairing_requests WHERE token_hash=? AND target_kind<>'private'
      AND requested_by=? AND revoked_at IS NULL AND confirmed_at IS NULL AND shared_chat_id IS NULL AND expires_at>?
      AND created_at<=?`).bind(hash, chat.id, now, message.date * 1000 + 999).first<Pairing>();
    if (prior) return sendChatKeyboard(env, chat.id, prior.request_chat_id!, prior.target_kind === "channel", match[1]);
  }
  const title = [from.first_name, from.last_name].filter(v => typeof v === "string").join(" ").trim().slice(0, 128) || String(chat.id);
  // One SQL compare-and-set: duplicate or competing Starts cannot replace a claimant.
  await env.PAIRING_DB.prepare(`UPDATE pairing_requests SET chat_id=?,title=?,claim_update_id=?,claim_message_at=?
      WHERE token_hash=? AND target_kind='private' AND revoked_at IS NULL AND confirmed_at IS NULL AND chat_id IS NULL
      AND expires_at>? AND created_at<=?`).bind(chat.id, title, value.update_id, message.date, hash, now,
        message.date * 1000 + 999).run();
}

function json(status: number, value: unknown): Response {
  return Response.json(value, { status, headers: {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'", "X-Content-Type-Options": "nosniff",
  } });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      if (env.PAIRING_ENABLED !== "1" || !env.PAIRING_DB || !env.LICENSE_AUTH || !/^[1-9][0-9]{0,14}$/u.test(env.BOT_ID)
          || !/^[A-Za-z0-9_-]{32,128}$/u.test(env.TELEGRAM_WEBHOOK_SECRET ?? "")) {
        throw new ApiError(503, "SERVICE_NOT_CONFIGURED", "Pairing is not configured.");
      }
      const url = new URL(request.url);
      if (request.method !== "POST" || url.search || !["/v1/pairings", "/v1/send", "/v1/telegram/webhook"].includes(url.pathname)) {
        throw new ApiError(404, "NOT_FOUND", "Not found.");
      }
      const now = Date.now();
      if (url.pathname === "/v1/telegram/webhook") {
        await webhook(env, request, now); return json(200, { ok: true });
      }
      if (url.pathname === "/v1/send") {
        const { telegram, principal } = await authenticate(env, await readJson(request), now, "searchcar-telegram-delivery-v1");
        return json(200, { ok: true, data: await deliver(env, telegram, principal, now) });
      }
      return json(200, { ok: true, data: await pairings(env, await readJson(request), now) });
    } catch (error) {
      if (error instanceof KeyboardSendError) {
        const response = json(error.status, { ok: false, error: { code: error.code } });
        if (error.retryAfter !== null) response.headers.set("Retry-After", String(error.retryAfter));
        return response;
      }
      if (error instanceof ChatCheckRateLimit) return json(error.status, { ok: false, error: { code: error.code }, retry_after: error.retryAfter });
      if (error instanceof ApiError) return json(error.status, { ok: false, error: { code: error.code } });
      // No request body, raw DB error, pairing URL or Telegram credential in logs.
      console.error(JSON.stringify({ event: "telegram_pairing_failure" }));
      return json(503, { ok: false, error: { code: "PAIRING_UNAVAILABLE" } });
    }
  },
} satisfies ExportedHandler<Env>;
