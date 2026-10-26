# SearchCar Telegram pairing and thin delivery relay

This is the **recipient pairing/manual delivery slice** of MSA-15/16. Isolated
staging supports private/group/channel recipients and authorized single-attempt
delivery; production delivery remains disabled. End-to-end native acceptance is
still tracked separately. Task status
and remaining work live in `../docs/MULTISOURCE_IMPLEMENTATION_BACKLOG.md`.
There are no server delivery queues, scheduled handlers, waits or retries.
`POST /v1/send` executes one requested Telegram attempt. The desktop outbox,
intervals, cooldowns and recovery remain local. All three base/production flags
default off; the explicitly authorized staging pairing/chat/delivery flags are on.

25 October 2026: styled-message staging version
`b48c1020-a6fa-4196-b6b5-b3fcc6f47af0` is published. Exact candidate, checks and
rollback reference: `../work/telegram-styled-staging-20261025.pu3MBYTX/REPORT.md`.
The new desktop automatic-Today opt-in is local only: this relay still handles
one authenticated attempt, not a search trigger, scheduler or fleet queue.

## Flow and wire contract

1. Native SearchCar signs a `start` request for one local owner and project.
2. The relay returns an opaque pairing ID and a ten-minute
   `https://t.me/search_car_app_bot?start=p_…` link. Only a hash of its secret is
   stored. The link is returned once; losing it means starting a new request.
3. A verified Telegram webhook delivers the person's **direct private** Start.
   This proposes a recipient, but does not authorize message delivery yet.
4. The app reads the proposal, displays its name **and numeric ID**, and asks
   the user to confirm that specific recipient. A wrong recipient means cancel
   and create a fresh link; another Start cannot replace the first claimant.
5. `confirm` returns a non-secret binding reference for that device/owner/project.
   It atomically retires the prior confirmed binding of that same project.
   Starting or cancelling a replacement alone preserves the old binding.
6. Signed `revoke` revokes that project pairing. The person's `/stop` to the bot
   revokes their earlier consent across projects/devices. An out-of-order older
   stop does not revoke a newer Start. Status checks do not publish anything.
7. The desktop remembers confirmed recipients across its own projects. Signed
   `reuse` takes `source_pairing_id` and a new durable client `pairing_id` for the
   destination project. An atomic INSERT SELECT requires existing confirmed,
   non-revoked consent on the same license/device/key/owner, issues a separate
   binding and retires only the target project's old bindings. Repeating the
   same operation recovers a lost reply; a conflicting/revoked operation fails.
   Original Telegram consent ordering is retained, so `/stop` also revokes
   reused grants without revoking a newer Start. Apply migration0002 as well.

For groups/channels, signed start additionally supplies `target_kind:"group"`
or `"channel"` (omission means private). A private Start sends a checked setup
message, not a private recipient proposal. For groups it contains a native
`startgroup=p_<same nonce>&admin=manage_chat` inline link. Telegram chooses the
group and requests bot administrator consent. The resulting group/supergroup
Start must match the nonce and earlier private human claimant, within the same
ten-minute link. It records a proposed chat, never a delivery grant. For channels
the message uses `request_chat` with both user and bot administrator rights;
the direct private `chat_shared` must match the claimant/request. Existing group
`chat_shared` keyboards remain compatible with unexpired requests.
The desktop never supplies the selected chat ID. Signed status/confirm verify
getChat and getChatMember for both the human administrator and bot; channels
also require posting rights. Only verified getChat metadata is displayed for
confirmation. Rights are rechecked on reuse and before each new send attempt.
`/stop` also revokes group/channel consent and pending selections made by that
person. Read-only verification failures do not create a send receipt;
429/retry_after is returned to the local scheduler, not retried by the server.
Apply additive migration0004 before deploying this code over the private slice.

All app actions use `POST /v1/pairings`, JSON, the existing native Ed25519
`SEARCHCAR-DEVICE-REQUEST-V1` envelope, plus these signed fields:

```json
{
  "audience": "searchcar-telegram-pairing-v1",
  "license_id": "<existing license UUID>",
  "device_id": "<existing device UUID>",
  "telegram": {
    "action": "start",
    "owner_id": 7,
    "project_id": 9
  }
}
```

`status` and `revoke` also require `pairing_id`; `confirm` additionally requires
the `chat_id` the user saw and confirmed. Owner/project IDs alone are not auth:
they are scoped by the authorized device's signing key. A native client must use
the currently authenticated local owner/project, not accept them from a URL.

Responses use `{ok:true,data:…}` or `{ok:false,error:{code:…}}`, always no-store.
Data includes `pairing_id`, `project_id`, `bot_id`, `expires_at_ms`, `status`,
`recipient` and `binding_id`. States are `WAITING_START`,
`AWAITING_CONFIRMATION`, `CONNECTED`, `EXPIRED`, `REVOKED`. The recipient appears
only while awaiting confirmation or connected; the binding appears only when
connected. Only the start response includes `start_url`. A binding is not a
bearer credential, and the local client must not infer delivery is enabled.

`POST /v1/telegram/webhook` authenticates Telegram's secret header before reading
the bounded body. Duplicate Starts cannot replace a selected chat. Arbitrary
group/channel posts, edited/forwarded/anonymous messages cannot grant access.
There is deliberately no
`getUpdates` consumer in each desktop application. Only the explicitly requested
group/channel selection keyboard is sent through a bounded direct Bot API call;
the webhook is acknowledged only after validating the returned message/chat.
Recognized keyboard rejections, flood-control timing and unknown transport
results produce distinct sanitized diagnostic events and non-success responses.
No provider description, token URL, recipient ID or pairing secret is logged.
The same claimant can retry an unexpired Start. This may repeat a setup keyboard
after an ambiguous network result, but never grants access or sends vehicles;
there are no unsolicited or automatic vehicle messages.

## Authorization, persistence and privacy

Every signed request verifies the signature, audience, five-minute freshness,
active device/key/fingerprint and current non-deleted, non-suspended,
unexpired license. Replayed request IDs are rejected. A local client can retry
reads using a fresh signed request; a lost start response requires a new start.
Confirmation is repeatable with a fresh request after a lost response; concurrent
state changes may return `PAIRING_CHANGED`, requiring a status read first.

`LICENSE_AUTH` is a service binding to the license Worker's named
`TelegramAuthorization` RPC entrypoint. Its only method accepts the signed
envelope and returns a boolean, never license/device records or a lease. It
independently verifies the signature, fingerprint, freshness, Telegram audience
and current device/license status with SELECT only. There is no public HTTP
authorization route. Replay protection and abuse windows remain in PAIRING_DB.
The relay has no LICENSE_DB binding, license signing key or admin token. A
missing/unavailable service or non-boolean response fails closed; no positive
decision is cached across attempts. No license-schema migration is required.

`PAIRING_DB` contains authorization state, request protection and one-attempt
receipts (hash/state/message ID, not message content or a delivery queue).
Receipts are retained so a replay cannot resend an old ambiguous attempt.
Unconfirmed/expired/revoked pairing rows are
removed after the link expiry plus one day; connected consent remains until
revocation. Nonces and infrastructure rate windows expire. Cleanup occurs on
authenticated requests, not a cron. The 120 requests/minute/device abuse guard is
shared by signed APIs as infrastructure protection, not fleet-wide Telegram pacing.

No raw link secrets, proof bodies, chat metadata or exception strings are logged.
Do not enable request-body/header logging when deploying. The client should keep
the deep link transient and exclude it from backups/diagnostics. Chat metadata
is private and must not enter a calendar URL. Delivery rechecks the
binding and device/license at **each** attempt; cached CONNECTED state is not
permission to send after revocation.

## One-attempt delivery

`POST /v1/send` uses the same Ed25519 envelope with the separate audience
`searchcar-telegram-delivery-v1`. Signed `telegram` fields are `action:"send"`,
`owner_id`, `project_id`, `binding_id`, configured `bot_id`, a fresh 32-hex
`attempt_id`, and `message:{text,photo,entities}`. The **server derives chat ID
from that project's confirmed, non-revoked binding**, never from the client.
Only source-host HTTPS photos, bold entities and text links to the approved
calendar hosts or Copart/IAA detail pages are accepted. At most16 non-overlapping,
ordered UTF-16 ranges are allowed; split emoji, unknown types/fields, unsafe
links, invalid/overlapping ranges and oversized captions fail before any send
receipt or HTTP attempt. Plain text plus entities is used, not HTML/Markdown
parse mode, so vehicle values cannot inject formatting. The optional inline
button is selected by the validated calendar URL, not the first entity/link.
Old single-calendar-entity messages remain supported.
Arbitrary Bot API options and paid broadcasts are not accepted.
`allow_paid_broadcast` is always false. Request and upstream response bodies are
bounded; HTTP redirects are not followed; raw upstream errors are not returned.
Tracing is disabled to avoid recording token-bearing Telegram subrequest URLs.

Before HTTP the relay atomically claims a receipt keyed by device/owner/attempt.
Concurrent/repeated requests return the receipt, never another send. Changing
that attempt's content returns409. `SENT` requires a valid message ID and the
expected chat ID and type in Telegram's response. Other outcomes are `RATE_LIMITED`
(with optional `retry_after`), `PHOTO_REJECTED`, `FAILED`, or `UNKNOWN`. An
interruption/lost response stays UNKNOWN; no server retry follows. A definite
photo rejection allows the **local** dispatcher to claim another paced attempt
for text. A late /stop cannot recall an HTTP attempt already accepted in flight.

The desktop API accepts owned auction-car IDs, a collection and a durable
`intent_id`, not arbitrary user message text. It builds messages from the current
stored data (not a live website recheck), regenerates time/calendar before send,
and cancels an item if the selected scope, vehicle, filters or recipient changed.
Up to1000 vehicles per explicit request; larger selections must be split.
Closing the modal does not cancel the queue; closing the application stops it
until restart. Only the exclusive local dispatcher may recover IN_FLIGHT as
UNKNOWN. Connect failures before HTTP get bounded local backoff; lost responses
do not. Unknown results require explicit duplicate-risk acknowledgment in UI.

Each **new explicit user action** may intentionally send a vehicle again,
including a new group selection. UI marks prior confirmed deliveries. A retry
of the same technical intent returns the same batch without duplicating it.

## Local checks (no deployment)

From the repository root with its existing pnpm/Node runtime:

```sh
pnpm telegram:typecheck
pnpm telegram:test
```

Types are generated with Wrangler into an ignored file before typecheck.
Workers runtime declarations are isolated from the browser/desktop tsconfig.
`telegram:build` is explicitly `deploy --dry-run`. Tests use Miniflare and two
disposable D1 databases; only explicitly enabled mock Telegram calls are accepted.
They do not send
Telegram messages and do not access the production license database.

## Before any live setup

This document is **not authorization to deploy**. Agree separate staging Worker
and D1 placement and license-auth access first. The base configuration is disabled,
has placeholder database IDs, no public route, no cron/queue and no secrets.
The separate named `staging` environment is described below; always select it
explicitly rather than deploying the base placeholders.

After approval: provision the separate pairing database, apply only its migrations (0001–0004),
generate a random webhook secret and put it in server secrets without terminal
echo or chat disclosure. Store `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`
as Worker secrets, never desktop configuration. Securely obtain the bot token; use `getMe` to verify
`@search_car_app_bot` and set its numeric BOT_ID. The bot token must never enter
the desktop app or calendar site. Configure the one webhook for this bot with
the secret header, `allowed_updates: ["message"]`, and a deliberate policy for
already pending updates; don't silently discard them. Confirm no other webhook/
polling integration uses this new bot before changing it. Keep pairing disabled
until migrations, auth and webhook settings have been verified together.

Set the bot description to explain SearchCar pairing, returning to the app for
confirmation, and `/stop`. The Start action is not a subscription to unsolicited
messages. Bot-token and webhook provisioning status is recorded below; no real
Telegram message delivery has occurred.

### Isolated staging bootstrap (2026-10-22)

With user approval, `searchcar-telegram-relay-staging` was deployed at
`https://searchcar-telegram-relay-staging.mapatar23.workers.dev`, with the separate
WEUR D1 `searchcar-telegram-pairing-staging` and migrations0001–0003 applied.
`PAIRING_ENABLED=0`, `DELIVERY_ENABLED=0`, `BOT_ID=0`; no secrets and no
`LICENSE_DB` binding are installed. Both pairing and webhook probes return
503 `SERVICE_NOT_CONFIGURED`, not an accepted pairing or webhook update.

Use `--config telegram-relay/wrangler.jsonc --env staging` for every operation.
The original bootstrap deliberately omitted LICENSE_DB; the follow-up below
removes it from the base configuration too. The empty staging
`secrets.required` list permits only this disabled bootstrap. Before activation,
restore both required secret names, securely provision them,
verify bot identity and existing webhook ownership, then
perform the authorized pairing acceptance. Leave delivery disabled until its
separate acceptance. Missing authorization configuration fails closed before
processing requests. No production license-service changes were made during
the initial bootstrap; the separately authorized follow-up is below.

Deployment evidence: `../work/telegram-staging-20261022.lKrMen6B/REPORT.md`.

### Narrow authorization follow-up (2026-10-22)

After separate user approval, the named license RPC was deployed and staging
was connected through LICENSE_AUTH, not LICENSE_DB. The license deployment
preserves the downloaded deployed HTTP/scheduled implementation instead of
publishing unrelated local license-service edits. No database migration or
secret change was performed. Live checks: a malformed request and a freshly
signed unknown-device request both return boolean false through the internal
binding; license health returns200 and the nonexistent public authorization
route returns404. Positive authorization is verified with disposable fixtures,
not a real customer's signing key. Staging remains disabled503; no token,
webhook, real pairing or delivery is configured yet. Evidence and rollback
versions: `../work/license-auth-20261022/REPORT.md`.

### Secure token provisioning (2026-10-22, before webhook setup)

The operator entered the token through a native hidden-answer dialog. `getMe`
verified `@search_car_app_bot`, numeric ID `8996925581`; `getWebhookInfo` reported
no configured webhook and zero pending updates. TELEGRAM_BOT_TOKEN is stored
only as a staging Worker secret, not in source, desktop configuration or a local
token file. The operator helper suppresses Wrangler disk logging/output during
secret input, passes the credential through stdin, and returns only safe status.
It never configures a webhook, consumes updates or sends messages. An existing
webhook blocks storage for separate operator review rather than overwriting it.

Helper: `scripts/configure_telegram_staging_token.mjs` (run from repository root
with Node on macOS). Tests: `tests/telegram-staging-token.test.mjs`.
The staging BOT_ID remains0 and both feature flags remain off. Before activation,
set the verified numeric ID, securely provision the webhook secret, restore both
required-secret names, and configure/verify the webhook. There is no real pairing
or delivery acceptance yet. Evidence: `../work/telegram-token-20261022.md`.

### Staging webhook ready (2026-10-22, historical)

`@search_car_app_bot` now has its single webhook pointing to
`https://searchcar-telegram-relay-staging.mapatar23.workers.dev/v1/telegram/webhook`.
Both credentials are Worker secrets; the bot token was not retrieved back to
the computer. The verified BOT_ID is8996925581, PAIRING_ENABLED=1 for staging
acceptance, DELIVERY_ENABLED=0, and both secret names are required in config.
`allowed_updates` is exactly `["message"]`, pending updates0, and
`drop_pending_updates:false` preserved events. A wrong/absent secret gets401;
the authenticated receiver preflight returned200 before configuring Telegram.

A temporary named operator RPC used the already stored secrets entirely inside
Cloudflare. Its pinned bot/target cannot be replaced by caller arguments. After
Telegram confirmed setWebhook/getWebhookInfo, the temporary proxy was stopped
and the normal relay was redeployed to remove the operator export and temporary
public-fetch flag. Final version: `77448aae-4cd3-4647-bbcf-3755961d3984`.
Operator details: `ops/README.md`; evidence: `../work/telegram-webhook-20261022/REPORT.md`.

This is not a real recipient connection: staging still has0 pairings and0
delivery receipts. The23.10 preview build now provides trusted-origin configuration;
the interactive Start/check/confirm flow is the next acceptance step. No customer data,
license schema, desktop build/default, or delivery queue was changed here.

The native client/API and project UI are now implemented for private
pairing, tested only with disposable fixtures. Native configuration currently
uses `SEARCHCAR_TELEGRAM_RELAY_URL`. The macOS auction preview built2026-10-23
pins the staging origin through the existing native preview flag and forces
delivery off, including Finder launches. Ordinary production builds still have
no relay default; without trusted configuration they show an unavailable state.
Native GET reads only cached local metadata; Start/check/confirm/revoke are
explicit, signed one-attempt actions. A lost confirm response is recovered by
check. The deep link is never persisted. Local state is bound to relay origin,
license/device binding and project context, outside the portable backup; it
does not enable delivery or prove present server permission.
The local saved-recipient catalogue is deduplicated by bot/chat, scoped to the
current owner/device/license/relay, and survives deleting a project. It is also
offered in Settings for desktop Encar/Copart/IAA projects; an unverified legacy
channel URL is not imported as consent. Selecting a saved row performs `reuse`,
never copies an old project's grant. A stale/revoked row needs reconnection.

Outside isolated staging acceptance, enable `PAIRING_ENABLED` only after private-pairing acceptance. Enable
`DELIVERY_ENABLED` on the relay only for the agreed delivery smoke. The native
app also requires `SEARCHCAR_TELEGRAM_RELAY_URL` and explicit trusted runtime
`SEARCHCAR_TELEGRAM_DELIVERY_ENABLED=1`; no token enters the desktop. Merely
confirming a recipient does not enable either flag or send a message.

Next: real native private-pairing acceptance with the configured preview build,
then an updated group/channel preview and interactive acceptance.
The manual native sender is implemented but disabled by
default; automatic post-search publication is still not connected.
Production activation still requires the MSA-15/16 acceptance gates.

### Authorized manual-delivery pilot (2026-10-23)

After successful real group pairing, the user explicitly approved enabling
manual staging delivery and building a separate macOS preview. Staging version
`f385f3eb-ad4c-41c0-81eb-91fb9e443965` now uses gates1/1/1. The published JS is
unchanged from the previous recipient-only version; only the staging delivery
variable changes. Base/production remain0/0/0. No migrations, webhook/secret or
license-service changes.48 relay tests and6 negative live probes passed;
delivery_receipts remains empty. No vehicle was sent by the agent.

Native delivery additionally requires the explicit compile-time
`SEARCHCAR_TELEGRAM_DELIVERY_PREVIEW=1` together with source-test-mode. A regular
pairing preview remains delivery-off even if it inherits delivery1. The local
sender now accepts confirmed private/group/supergroup/channel recipients, with
native gate/type/context revalidation; group rights still run on the relay per
attempt. Queueing, pacing, retries and cooldown stay on the desktop. The user
must select and send the first vehicle in the new app; neither app startup nor
pairing starts a broadcast. Live receipt/calendar acceptance and Windows remain
open. Roll back staging to `3d7158dd-7e55-46b2-a75c-b5c9151fd0e7` to disable
delivery without changing the schema or saved recipient bindings.

### Current staging recipient acceptance (2026-10-23)

The user explicitly authorized the staging update without vehicle delivery.
Migration0004 was applied to the existing staging D1, preserving its two private
pairing rows. Normal relay version `b89bdb57-0c07-4205-b194-4e81de966e9c`
enables PAIRING_ENABLED1/CHAT_PAIRING_ENABLED1 while keeping DELIVERY_ENABLED0.
The existing bot webhook and server secrets were neither changed nor read back.
The license service/DB and calendar were not deployed or modified.

Thirty-six fixture tests and six live negative security probes passed. Staging
still had0 confirmed pairings and0 delivery receipts immediately after these
checks; no real message or recipient connection was performed by the probes.
This is readiness for interactive acceptance, not proof of real native pairing.
The old macOS preview remains private-only; group/channel UI requires an updated
preview with trusted `SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED=1`. The native
default remains off and production defaults remain disabled. No .app was built.

Evidence/rollback: `../work/telegram-staging-chat-20261023.syZu37/REPORT.md`.

### Group picker compatibility fix (2026-10-23)

The user reported a missing owner-created group on both desktop and phone.
Group `request_chat` now omits client-side administrator filters; these can
exclude creators whose Telegram client has no separate admin-rights record.
Channels retain their administrator/posting-right requirements. Server-side
`verifyChat` is unchanged: selecting a group alone never grants delivery access;
the human must be its creator/administrator and the bot an administrator.
The bot's group instructions explain the bot membership/admin requirement.

Verified RED/GREEN on the actual webhook keyboard response, plus basic-group and
supergroup permission denials and creator acceptance without a can_manage_chat
field. All39 relay/RPC/operator fixtures, relay/root TypeScript and dry-run pass.
With explicit user approval, only staging was updated to
`768ab357-9fb0-403a-b1b3-f25cce5d2ab5`, bundle SHA256
`f543c5c05ba08a01befd57213d21de256897758fd2ed341ae133c1b4b3a0d72c`.
Recipient gates remain1/1, delivery0. Six live negative probes passed; aggregate
pairing counts were unchanged. No database migration, webhook/secret change,
license/calendar deployment or new desktop build. The existing group-enabled
macOS preview can use the fix. A fresh link/Start is required to replace the old
Telegram keyboard. Real group selection/confirmation still needs user acceptance.
Evidence/previous-version rollback:
`../work/telegram-group-filter-20261023.DrM99F/REPORT.md`.

### Bot addition and rights in the picker (2026-10-23)

The user could then select the previously missing group, but Telegram only
shared it: the share-only request did not ask Telegram to add the bot. Both
group/channel keyboards now request `bot_is_member=true` and minimal
`bot_administrator_rights`: manage chat for groups, plus post messages for
channels. No user-administrator filter is sent, preserving owner visibility.
Telegram handles the addition/rights consent; SearchCar still checks actual
human/bot permissions and requires explicit confirmation before granting access.
No deletion, banning, invitation or administrator-promotion rights are requested.

Verified RED/GREEN for both webhook keyboards; all39 relay/RPC/operator tests,
relay/root TypeScript and the exact-candidate staging dry-run passed. With
explicit user approval, staging is now100% on
`66d5f4cb-2b7d-49b4-a4de-ed289ddccf7f`, SHA256
`9259047cb1f4a6ea5829d6b4cb5d9347d84c5aab1faddb6133e0cc21c79dd409`.
Recipient gates1/1, delivery0 and existing bindings are verified unchanged.
Six live negative probes passed and aggregate pairing counts were unchanged.
No migration, webhook/secret change, license/calendar deploy or .app rebuild.

Create a fresh connection link and press Start to receive the new keyboard;
old messages retain the share-only request. Real Telegram client acceptance of
bot addition/rights remains open. A shared-chat notification alone is not proof
of successful addition. Evidence/rollback target:
`../work/telegram-auto-add-20261023.70eu4X/REPORT.md`.

### Checked Start keyboard transport (2026-10-23)

The user reported a silent Start. Read-only staging evidence confirmed a new
group Start was received, without a selected chat. The old webhook-response
sendMessage shortcut could not expose Telegram's result. It is replaced by a
direct, timeout-bounded sendMessage call, bounded JSON and matching message/chat
validation. Success/rejection/unknown-result logs contain only static diagnostics;
raw provider descriptions and credential URLs are never logged.429 supplies a
validated Retry-After; failed sends do not create a grant. Existing private
pairing and actual human/bot permission checks remain unchanged.

Verified RED on the missing direct request and false success for rejection,
rate-limit and network failure. After correcting a Miniflare-only redirect-mode
assertion, all46 relay/RPC/operator fixtures passed; relay/root TypeScript and
exact-candidate staging dry-run passed. Authorized staging deploy:
`59059191-9fb3-4c97-b702-c63d6dafea0f` (100%), bundle SHA256
`ea3e6a8625a1033e1c987b213c4b9dc91e85522e0c5d61ee3fdc59f85b40da68`.
Recipient gates1/1 and delivery0, bindings unchanged; six live negative probes
passed. No migration, webhook/secret change, license/calendar deploy or .app build.
The actual cause of Telegram's silent keyboard rejection still requires a fresh
user Start with these diagnostics; this transport fix is not yet proof of native
bot addition. Evidence: `../work/telegram-start-direct-20261023.ERoBjT/REPORT.md`.

### Confirmed USER_RIGHTS_MISSING repair (2026-10-23)

Live diagnostics now confirmed Telegram400 `USER_RIGHTS_MISSING`. The user's
project link and Start were correct. Earlier fixtures failed to enforce the
provider requirement: bot_administrator_rights requires covering user rights.
Channels now send identical minimal management/posting rights in both fields.

Simply restoring that filter for groups would risk the original owner-visibility
bug. Groups instead use Telegram's native add-bot link described above. The
private claimant/nonce, freshness, first selection, non-revoked state, actual
human/bot permissions and explicit app confirmation all remain required.
Native links request manage_chat only, not delete/ban/invite/promote rights.
No new webhook update types or database migration are required.

RED: old group message still used the owner-hiding picker; channel user rights
were absent; group Starts could not propose the chat. GREEN:48 relay/RPC/operator
tests, relay/root TypeScript and final exact-bundle staging dry-run passed.
Staging version `3d7158dd-7e55-46b2-a75c-b5c9151fd0e7`, SHA256
`ebc84486dba1a2d634bce3401742aace9ef13a65a9a94324d5153d42abd7e373`.
Delivery stays0. No native group addition or channel-owner visibility acceptance
is claimed yet; a fresh link/Start is needed. No app rebuild, token readback,
webhook change or other service deployment. Evidence:
`../work/telegram-native-group-20261023.m1C4m7/REPORT.md`.

### Desktop automatic completion (2026-10-23)

The user confirmed native group addition; a read-only staging check also showed
the latest real group selected, permission-checked and confirmed (no vehicle sent
by this check). The user then chose automatic desktop completion, provided the
load is bounded and the UI explains the next step.

The desktop now checks an available unexpired pending pairing every5 seconds
only on the open pairing screen, with no background refetch while hidden.
Returning from Telegram checks immediately. It stops on success, error, expiry,
revocation, collapse or leaving the screen. A checked matching recipient triggers
the existing signed CONFIRM command automatically; server rights/auth checks
remain intact. A failed or ambiguous confirmation has no automatic retry loop;
the UI offers a manual check to recover status. No vehicle sender is invoked.

The timer is **in the app**, but each CHECK still consumes a short Cloudflare
request. Until selection the relay does not call getChat/getChatMember; after
selection the existing check/confirm paths verify permissions. No server polling,
scheduler or shared delivery queue was added. Backend/Worker code and flags are
unchanged; this feature needs a new desktop build, not another Worker deployment.
Local evidence: `../output/playwright/telegram-auto-regression/REPORT.md`.

References: [Telegram deep links](https://core.telegram.org/bots/features#deep-linking),
[webhook secret](https://core.telegram.org/bots/api#setwebhook),
[D1 batch](https://developers.cloudflare.com/d1/worker-api/d1-database/#batch).
