# Telegram production acceptance and operations

The ordinary desktop profile uses `searchcar-telegram-relay.mapatar23.workers.dev`
and the existing `@search_car_app_bot`. Only Copart/IAA support Telegram; Encar
remains disabled. Bot credentials exist only in Cloudflare secrets, not installers.
The license Worker and its database are unchanged by this promotion.

## Data and sending

- Production and staging have separate D1 databases. No test projects,
  device grants, recipients or delivery receipts are copied to production.
- The shared bot has one webhook, now production. Staging pairing, chat
  pairing and delivery are disabled; an old preview cannot send or reconnect.
- Use the ordinary build (`auction_preview=false` on Windows CI). Connect the
  recipient once in this profile, even if the bot already belongs to the chat.
- Connecting never sends a vehicle. Initial automatic Today posting defaults
  OFF per project; enabling it requires a subsequent fully successful search.
  Existing independent schedule-change rules are unchanged.
- Explicit trusted `SEARCHCAR_TELEGRAM_DELIVERY_ENABLED=0` disables desktop
  dispatch; server `DELIVERY_ENABLED=0` independently disables relay delivery.

## Installed acceptance — Windows and macOS separately

1. Activate a valid license; open an ordinary Copart or IAA project.
2. Connect a private recipient, group or channel using the app's fresh link.
   For group/channel, approve bot addition and the requested minimal rights.
   Return to SearchCar and wait for Connected; the app checks and saves it.
3. Verify no vehicle was sent merely by connecting. Automatic Today is OFF.
4. Explicitly send one chosen vehicle to your agreed test chat. Check photo,
   bold labels, listing/calendar links and auction time. Then test selected
   bulk delivery; do not retry an uncertain result blindly.
5. Select that saved recipient in another project. Sending should need no
   second Telegram setup, while the server still checks current permissions.
6. Revoke bot posting rights or use `/stop`: subsequent sends must fail safely,
   not silently move to another recipient. Reconnect explicitly to resume.
7. Verify Encar has no Telegram controls, and restart preserves the recipient.
8. Separately opt into Today on a disposable project; only a complete successful
   run may queue it. Failed/cancelled/partial multi-project runs must not send.

## Coordinated webhook cutover / rollback

Never copy secret values out of Workers or expose a public setup HTTP endpoint.
The temporary `ops/index.ts` export is deployed only with explicit operator
approval and delivery OFF. A local loopback proxy uses an authenticated
Cloudflare service binding to `TelegramSetup`; it is not a public admin route.

`configureProduction()` verifies the pinned bot and accepts only the exact
existing staging/production webhook. It preflights the authenticated receiver,
sets only `allowed_updates=["message"]`, preserves pending updates, then verifies
the result. Errors are sanitized; ambiguous mutations are not automatically
retried. The helper accepts no arbitrary target through RPC.

For rollback, first pause production delivery and record deployed versions plus
both D1 backups. Restore a verified staging receiver with pairing/chat enabled,
delivery OFF and its original secrets. Temporarily export the same operator
there and call `restoreStaging()` through an authenticated service binding.
It accepts only the pinned production/staging webhook and uses staging's own
secret. Verify the switch, remove the operator/proxy and disable production
pairing/delivery. Do not copy or merge recipient grants, drop pending updates,
restore an unrelated database or roll blindly to a pre-secret/operator version.

After any successful setup, immediately deploy normal `src/index.ts` (no
`TelegramSetup` export or temporary public-fetch flag) and stop the local proxy.
Check unsigned webhook requests fail, public setup is absent, and a freshly
signed unknown device is denied through the live license binding. Those checks
send no vehicle and do not substitute for installed recipient/send acceptance.
