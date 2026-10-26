import { ApiError } from "../../license-service/src/errors";
import { asObject, requireString } from "../../license-service/src/validation";
import { sha256 } from "../../license-service/src/crypto";
import catalog from "../../backend/app/sources/catalog.json";
import { boundedJson, verifyChat } from "./telegram-api";

type Principal = { license: string; device: string; key: string; owner: number; project: number };
type Receipt = { state: string; message_id: number | null; retry_after: number | null; error_code: string | null; payload_hash: string };
const ID = /^[a-f0-9]{32}$/u;
const wellFormed = (value: string) => !/[\uD800-\uDFFF]/u.test(value);

function keys(value: Record<string, unknown>, allowed: string[]) {
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new ApiError(400, "INVALID_MESSAGE", "Invalid message.");
}
function calendarUrl(value: unknown): string {
  if (typeof value !== "string" || value.length > 2048 || /[\u0000-\u0020\u007f]/u.test(value)) throw Error();
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password || url.port) throw Error();
  if (url.hostname === "searchcar-auction-calendar-preview.pages.dev" && url.pathname === "/" && !url.search && url.hash) return url.href;
  if (url.hostname === "calendar.google.com" && url.pathname === "/calendar/render" && url.searchParams.get("action") === "TEMPLATE" && !url.hash
      && [...url.searchParams.keys()].every(key => ["action", "text", "dates", "details"].includes(key))) return url.href;
  throw Error();
}
function messageUrl(value: unknown): string {
  try { return calendarUrl(value); } catch { /* A second link can point only to an allowed auction detail page. */ }
  if (typeof value !== "string" || value.length > 512 || /[\u0000-\u0020\u007f]/u.test(value)) throw Error();
  const url = new URL(value), source = catalog.find(source => source.domains.includes(url.hostname));
  if (url.protocol !== "https:" || url.username || url.password || url.port || url.search || url.hash) throw Error();
  if (source?.key === "copart" && /^\/(?:[a-z]{2}\/)?lot\/\d+(?:\/[^/]+)?\/?$/u.test(url.pathname)
    || source?.key === "iaa" && /^\/VehicleDetail\/\d+~US\/?$/iu.test(url.pathname)) return url.href;
  throw Error();
}
function message(value: unknown) {
  try {
    const item = asObject(value); keys(item, ["text", "photo", "entities"]);
    const text = requireString(item, "text", { max: item.photo ? 1024 : 4096 });
    if (/[\u0000-\u0008\u000b-\u001f\u007f\u202a-\u202e\u2066-\u2069]/u.test(text) || !wellFormed(text)) throw Error();
    let photo: string | null = null;
    if (item.photo !== null) {
      photo = requireString(item, "photo", { max: 2048 }); const url = new URL(photo);
      if (url.protocol !== "https:" || url.username || url.password || url.port || /[\u0000-\u0020\u007f]/u.test(photo)
          || !catalog.some(source => source.image_domains?.includes(url.hostname))) throw Error();
    }
    if (!Array.isArray(item.entities) || item.entities.length > 16) throw Error();
    let end = 0;
    const entities = item.entities.map(value => {
      const entity = asObject(value);
      const { offset, length } = entity;
      if (typeof offset !== "number" || !Number.isSafeInteger(offset) || offset < end
          || typeof length !== "number" || !Number.isSafeInteger(length) || length < 1 || offset + length > text.length
          || !wellFormed(text.slice(0, offset)) || !wellFormed(text.slice(offset, offset + length))) throw Error();
      end = offset + length;
      if (entity.type === "bold") {
        keys(entity, ["type", "offset", "length"]); return { type: "bold" as const, offset, length };
      }
      keys(entity, ["type", "offset", "length", "url"]);
      if (entity.type !== "text_link" || length > 100) throw Error();
      return { type: "text_link" as const, offset, length, url: messageUrl(entity.url) };
    });
    return { text, photo, entities };
  } catch { throw new ApiError(400, "INVALID_MESSAGE", "Invalid message."); }
}

export async function deliver(env: Env, telegram: Record<string, unknown>, p: Principal, now: number) {
  if (env.DELIVERY_ENABLED !== "1" || !new RegExp(`^${env.BOT_ID}:[A-Za-z0-9_-]{20,100}$`, "u").test(env.TELEGRAM_BOT_TOKEN ?? "")) {
    throw new ApiError(503, "DELIVERY_NOT_CONFIGURED", "Delivery unavailable.");
  }
  keys(telegram, ["action", "owner_id", "project_id", "attempt_id", "binding_id", "bot_id", "message"]);
  if (telegram.action !== "send" || telegram.bot_id !== Number(env.BOT_ID)) throw new ApiError(400, "INVALID_REQUEST", "Invalid request.");
  const attempt = requireString(telegram, "attempt_id", { pattern: ID });
  const binding = requireString(telegram, "binding_id", { pattern: ID });
  const grant = await env.PAIRING_DB.prepare(`SELECT chat_id,chat_type,target_kind,requested_by FROM pairing_requests WHERE binding_id=? AND
    license_id=? AND device_id=? AND public_key=? AND owner_id=? AND project_id=? AND
    confirmed_at IS NOT NULL AND revoked_at IS NULL`).bind(binding, p.license, p.device, p.key, p.owner, p.project)
    .first<{ chat_id: number; chat_type: string; target_kind: string; requested_by: number | null }>();
  if (!grant) throw new ApiError(403, "RECIPIENT_NOT_AUTHORIZED", "Recipient not authorized.");
  const content = message(telegram.message);
  const hash = await sha256(JSON.stringify({ binding, bot: telegram.bot_id, content }));
  const args = [p.license, p.device, p.key, p.owner, attempt];
  const replay = await env.PAIRING_DB.prepare(`SELECT * FROM delivery_receipts WHERE
    license_id=? AND device_id=? AND public_key=? AND owner_id=? AND attempt_id=?`).bind(...args).first<Receipt>();
  if (replay) {
    if (replay.payload_hash !== hash) throw new ApiError(409, "ATTEMPT_CHANGED", "Attempt changed.");
    return { attempt_id: attempt, state: replay.state, message_id: replay.message_id, retry_after: replay.retry_after, error_code: replay.error_code };
  }
  // Read-only permission checks precede the HTTP-send receipt: their failure is known not sent.
  if (grant.target_kind !== "private") await verifyChat(env, grant.chat_id, grant.requested_by, grant.target_kind);
  // A receipt is not a queue. Claim before HTTP; an interrupted attempt stays UNKNOWN forever.
  const inserted = await env.PAIRING_DB.prepare(`INSERT OR IGNORE INTO delivery_receipts
    (license_id,device_id,public_key,owner_id,attempt_id,project_id,payload_hash,state,created_at,error_code)
    VALUES(?,?,?,?,?,?,?,'UNKNOWN',?,'TELEGRAM_RESULT_UNKNOWN')`).bind(...args, p.project, hash, now).run();
  if (inserted.meta.changes !== 1) {
    const receipt = await env.PAIRING_DB.prepare(`SELECT * FROM delivery_receipts WHERE
      license_id=? AND device_id=? AND public_key=? AND owner_id=? AND attempt_id=?`).bind(...args).first<Receipt>();
    if (!receipt || receipt.payload_hash !== hash) throw new ApiError(409, "ATTEMPT_CHANGED", "Attempt changed.");
    return { attempt_id: attempt, state: receipt.state, message_id: receipt.message_id, retry_after: receipt.retry_after, error_code: receipt.error_code };
  }
  let result: Omit<Receipt, "payload_hash"> = { state: "UNKNOWN", message_id: null, retry_after: null, error_code: "TELEGRAM_RESULT_UNKNOWN" };
  try {
    const entity = content.entities.find(entity => {
      if (entity.type !== "text_link") return false;
      try { calendarUrl(entity.url); return true; } catch { return false; }
    });
    const payload = { chat_id: grant.chat_id, allow_paid_broadcast: false,
      ...(content.photo ? { photo: content.photo, caption: content.text, caption_entities: content.entities }
        : { text: content.text, entities: content.entities, link_preview_options: { is_disabled: true } }),
      ...(entity?.type === "text_link" ? { reply_markup: { inline_keyboard: [[{ text: content.text.slice(entity.offset, entity.offset + entity.length), url: entity.url }]] } } : {}) };
    const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${content.photo ? "sendPhoto" : "sendMessage"}`, {
      method: "POST", redirect: "manual", signal: AbortSignal.timeout(10000), headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const value = await boundedJson(response);
    if (response.status === 200 && value.ok === true) {
      const sent = asObject(value.result), chat = asObject(sent.chat);
      if (Number.isSafeInteger(sent.message_id) && typeof sent.message_id === "number" && sent.message_id > 0 && chat.id === grant.chat_id && chat.type === grant.chat_type) {
        result = { state: "SENT", message_id: sent.message_id, retry_after: null, error_code: null };
      }
    } else if (value.ok === false && response.status === value.error_code) {
      if (value.error_code === 429) {
        const retry = value.parameters && typeof value.parameters === "object" ? asObject(value.parameters).retry_after : null;
        result = { state: "RATE_LIMITED", message_id: null, retry_after: typeof retry === "number" && Number.isSafeInteger(retry) && retry > 0 && retry <= 2 ** 31 ? retry : null, error_code: "TELEGRAM_RATE_LIMIT" };
      } else if ([400, 401, 403].includes(Number(value.error_code))) {
        const photoError = content.photo && typeof value.description === "string" && /failed to get HTTP URL content|IMAGE_PROCESS_FAILED|wrong file identifier\/HTTP URL specified/u.test(value.description);
        result = { state: photoError ? "PHOTO_REJECTED" : "FAILED", message_id: null, retry_after: null,
          error_code: photoError ? "TELEGRAM_PHOTO_REJECTED" : value.error_code === 403 ? "TELEGRAM_FORBIDDEN" : "TELEGRAM_MESSAGE_REJECTED" };
      }
    }
  } catch { /* Ambiguous HTTP never authorizes a second send. No raw token URL is logged. */ }
  await env.PAIRING_DB.prepare(`UPDATE delivery_receipts SET state=?,message_id=?,retry_after=?,error_code=? WHERE
    license_id=? AND device_id=? AND public_key=? AND owner_id=? AND attempt_id=?`)
    .bind(result.state, result.message_id, result.retry_after, result.error_code, ...args).run();
  return { attempt_id: attempt, ...result };
}
