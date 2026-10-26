import { WorkerEntrypoint } from "cloudflare:workers";
import { asObject } from "../../license-service/src/validation";
import { boundedJson } from "../src/telegram-api";

const TARGET = "https://searchcar-telegram-relay-staging.mapatar23.workers.dev/v1/telegram/webhook";
const BOT = "8996925581";

async function telegram(token: string, method: "getMe" | "getWebhookInfo" | "setWebhook", body = {}) {
  const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST", redirect: "manual", signal: AbortSignal.timeout(10000),
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (response.status !== 200) throw Error();
  const value = await boundedJson(response);
  if (value.ok !== true) throw Error();
  return value.result;
}

function webhookInfo(value: unknown) {
  const info = asObject(value);
  if (typeof info.url !== "string" || typeof info.pending_update_count !== "number"
      || !Number.isSafeInteger(info.pending_update_count) || info.pending_update_count < 0) throw Error();
  return { url: info.url, pending: info.pending_update_count, updates: info.allowed_updates };
}

// Exported only by the temporary operator deployment, never the normal relay.
export class TelegramSetup extends WorkerEntrypoint<Env> {
  async configure() {
    const env = this.env;
    if (env.PAIRING_ENABLED !== "1" || env.DELIVERY_ENABLED !== "0" || env.BOT_ID !== BOT
        || !new RegExp(`^${BOT}:[A-Za-z0-9_-]{30,100}$`, "u").test(env.TELEGRAM_BOT_TOKEN ?? "")
        || !/^[A-Za-z0-9_-]{32,128}$/u.test(env.TELEGRAM_WEBHOOK_SECRET ?? "")) {
      return { ok: false, code: "SETUP_NOT_CONFIGURED" };
    }
    const token = env.TELEGRAM_BOT_TOKEN!;
    try {
      const bot = asObject(await telegram(token, "getMe"));
      if (bot.id !== Number(BOT) || bot.is_bot !== true || bot.username !== "search_car_app_bot") {
        return { ok: false, code: "WRONG_BOT" };
      }
      const before = webhookInfo(await telegram(token, "getWebhookInfo"));
      if (before.url && before.url !== TARGET) return { ok: false, code: "WEBHOOK_CONFLICT" };
      const receiver = await fetch(TARGET, { method: "POST", redirect: "manual", signal: AbortSignal.timeout(10000),
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET! },
        body: JSON.stringify({ update_id: 0 }) });
      if (receiver.status !== 200 || (await boundedJson(receiver)).ok !== true) {
        return { ok: false, code: "RECEIVER_NOT_READY", receiver_status: receiver.status };
      }
      if (await telegram(token, "setWebhook", { url: TARGET, secret_token: env.TELEGRAM_WEBHOOK_SECRET,
        allowed_updates: ["message"], drop_pending_updates: false }) !== true) throw Error();
      const after = webhookInfo(await telegram(token, "getWebhookInfo"));
      if (after.url !== TARGET || !Array.isArray(after.updates) || after.updates.length !== 1 || after.updates[0] !== "message") throw Error();
      return { ok: true, bot_id: Number(BOT), webhook_configured: true, pending_updates: after.pending, delivery_enabled: false };
    } catch {
      // No raw exceptions, token-bearing URL or webhook secret cross the RPC boundary.
      return { ok: false, code: "WEBHOOK_SETUP_UNCONFIRMED" };
    }
  }
}
