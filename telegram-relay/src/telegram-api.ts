import { ApiError } from "../../license-service/src/errors";
import { asObject } from "../../license-service/src/validation";

export class ChatCheckRateLimit extends ApiError {
  constructor(readonly retryAfter: number | null) { super(429, "CHAT_VERIFICATION_RATE_LIMITED", "Retry later."); }
}

export class KeyboardSendError extends ApiError {
  constructor(status: number, code: string, readonly retryAfter: number | null = null) {
    super(status, code, "Pairing keyboard unavailable.");
  }
}

export async function boundedJson(response: Response) {
  if (!response.body) throw Error();
  const reader = response.body.getReader(); let size = 0; const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > 65536) { await reader.cancel(); throw Error(); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return asObject(JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)));
}

export async function chatApi(env: Env, method: "getChat" | "getChatMember", body: Record<string, number>) {
  if (env.CHAT_PAIRING_ENABLED !== "1" || !new RegExp(`^${env.BOT_ID}:[A-Za-z0-9_-]{20,100}$`, "u").test(env.TELEGRAM_BOT_TOKEN ?? "")) {
    throw new ApiError(503, "CHAT_PAIRING_DISABLED", "Chat pairing unavailable.");
  }
  try {
    const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
      method: "POST", redirect: "manual", signal: AbortSignal.timeout(5000),
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const value = await boundedJson(response);
    if (response.status === 200 && value.ok === true) return asObject(value.result);
    if (response.status === 429 && value.ok === false && value.error_code === 429) {
      const parameters = value.parameters && typeof value.parameters === "object" ? asObject(value.parameters) : {};
      const retry = parameters.retry_after;
      throw new ChatCheckRateLimit(typeof retry === "number" && Number.isSafeInteger(retry) && retry > 0 && retry <= 2 ** 31 ? retry : null);
    }
    if ([400, 403].includes(response.status) && value.ok === false && value.error_code === response.status) {
      throw new ApiError(403, "CHAT_PERMISSION_REQUIRED", "Chat permission required.");
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
  }
  throw new ApiError(503, "CHAT_VERIFICATION_UNAVAILABLE", "Chat verification unavailable.");
}

export async function verifyChat(env: Env, chatId: number, userId: number | null, kind: string) {
  if (!Number.isSafeInteger(chatId) || chatId >= 0 || chatId <= -(2 ** 52) || !userId || userId <= 0 || !["group", "channel"].includes(kind)) {
    throw new ApiError(403, "CHAT_PERMISSION_REQUIRED", "Chat permission required.");
  }
  const chat = await chatApi(env, "getChat", { chat_id: chatId });
  if (chat.id !== chatId || (kind === "channel" ? chat.type !== "channel" : !["group", "supergroup"].includes(String(chat.type)))) {
    throw new ApiError(403, "CHAT_PERMISSION_REQUIRED", "Chat permission required.");
  }
  const [bot, user] = await Promise.all([
    chatApi(env, "getChatMember", { chat_id: chatId, user_id: Number(env.BOT_ID) }),
    chatApi(env, "getChatMember", { chat_id: chatId, user_id: userId }),
  ]);
  const botUser = bot.user && typeof bot.user === "object" ? asObject(bot.user) : {};
  const human = user.user && typeof user.user === "object" ? asObject(user.user) : {};
  if (botUser.id !== Number(env.BOT_ID) || botUser.is_bot !== true || bot.status !== "administrator" || bot.can_manage_chat !== true
      || human.id !== userId || human.is_bot !== false || !["creator", "administrator"].includes(String(user.status))
      || (user.status === "administrator" && user.can_manage_chat !== true)
      || (kind === "channel" && (bot.can_post_messages !== true || user.status !== "creator" && user.can_post_messages !== true))) {
    throw new ApiError(403, "CHAT_PERMISSION_REQUIRED", "Chat permission required.");
  }
  const title = typeof chat.title === "string" && chat.title.trim() ? chat.title.trim().slice(0, 128) : String(chatId);
  return { chat_id: chatId, chat_type: String(chat.type), title };
}

export function chatKeyboard(chatId: number, requestId: number, channel: boolean, token: string) {
  if (!channel) return { chat_id: chatId, allow_paid_broadcast: false,
    text: "SearchCar: нажмите кнопку, выберите группу и подтвердите права бота. Затем проверьте состояние в SearchCar. / Натисніть кнопку, оберіть групу й підтвердьте права бота. Потім перевірте стан у SearchCar.",
    reply_markup: { inline_keyboard: [[{ text: "Добавить в группу / Додати до групи",
      url: `https://t.me/search_car_app_bot?startgroup=p_${token}&admin=manage_chat` }]] } };
  const rights = { is_anonymous: false, can_manage_chat: true, can_delete_messages: false,
    can_manage_video_chats: false, can_restrict_members: false, can_promote_members: false,
    can_change_info: false, can_invite_users: false, can_post_stories: false,
    can_edit_stories: false, can_delete_stories: false, can_send_welcome_messages: false, can_post_messages: true };
  return { chat_id: chatId, allow_paid_broadcast: false,
    text: "SearchCar: выберите канал и подтвердите добавление бота с правом публикации. Затем проверьте состояние в SearchCar. / Оберіть канал і підтвердьте додавання бота з правом публікації. Потім перевірте стан у SearchCar.",
    reply_markup: { resize_keyboard: true, one_time_keyboard: true, keyboard: [[{
      text: "Выбрать канал / Обрати канал",
      request_chat: { request_id: requestId, chat_is_channel: true, request_title: true,
        bot_is_member: true, bot_administrator_rights: rights, user_administrator_rights: rights,
      },
    }]] } };
}

export async function sendChatKeyboard(env: Env, chatId: number, requestId: number, channel: boolean, token: string) {
  if (env.CHAT_PAIRING_ENABLED !== "1" || !new RegExp(`^${env.BOT_ID}:[A-Za-z0-9_-]{20,100}$`, "u").test(env.TELEGRAM_BOT_TOKEN ?? "")) {
    throw new KeyboardSendError(503, "PAIRING_KEYBOARD_UNAVAILABLE");
  }
  try {
    const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST", redirect: "manual", signal: AbortSignal.timeout(10000),
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(chatKeyboard(chatId, requestId, channel, token)),
    });
    const value = await boundedJson(response);
    if (response.status === 200 && value.ok === true) {
      const message = asObject(value.result), chat = asObject(message.chat);
      if (typeof message.message_id !== "number" || !Number.isSafeInteger(message.message_id) || message.message_id <= 0
          || chat.id !== chatId || chat.type !== "private") throw Error();
      console.log(JSON.stringify({ event: "telegram_pairing_keyboard_sent" }));
      return;
    }
    if (value.ok === false && value.error_code === response.status) {
      const retry = value.parameters && typeof value.parameters === "object" ? asObject(value.parameters).retry_after : null;
      const retryAfter = response.status === 429 && typeof retry === "number" && Number.isSafeInteger(retry) && retry > 0 && retry <= 2 ** 31 ? retry : null;
      const description = typeof value.description === "string" ? value.description : "";
      // Log only recognized static diagnostics, never provider text, chat IDs or credential URLs.
      const reason = /^Bad Request: ((?:BUTTON|BOT|CHAT|USER)_[A-Z_]{1,64})$/u.exec(description)?.[1]
        ?? (/bot_administrator_rights.*user_administrator_rights/u.test(description) ? "ADMIN_RIGHTS_SUBSET" : "UNCLASSIFIED");
      console.error(JSON.stringify({ event: "telegram_pairing_keyboard_rejected", status: response.status, reason, retry_after: retryAfter }));
      throw new KeyboardSendError(response.status === 429 ? 503 : 502,
        response.status === 429 ? "PAIRING_KEYBOARD_RATE_LIMITED" : "PAIRING_KEYBOARD_REJECTED", retryAfter);
    }
  } catch (error) {
    if (error instanceof KeyboardSendError) throw error;
  }
  console.error(JSON.stringify({ event: "telegram_pairing_keyboard_unavailable" }));
  throw new KeyboardSendError(503, "PAIRING_KEYBOARD_UNAVAILABLE");
}
