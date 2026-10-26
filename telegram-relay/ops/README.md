# Staging webhook operator

This is an operator-only entrypoint, **not exported by the normal relay**. It
does not supply a desktop route, send messages, consume updates or export secrets.
It accepts no URL, chat or Bot API options from the caller. The target bot and
staging webhook are pinned; delivery must remain disabled. A foreign existing
webhook is a conflict, not permission to overwrite it. Pending updates are kept.

`pnpm telegram:operator:build` only builds a dry-run fixture; it does not deploy
or configure Telegram. `pnpm telegram:test` also exercises this fixture.

Only after explicit live setup authorization:

1. Verify existing secret **names**, migration state and deployment scope.
   Never read token values into a terminal/chat. Provision the webhook secret
   through stdin with logging disabled; never rotate an existing secret blindly.
2. Deploy this alternative `ops/index.ts` only to the explicit staging environment,
   with both required secrets and private pairing enabled, delivery disabled.
3. Reach `TelegramSetup.configure()` only through an authenticated Cloudflare
   service binding. There is no public setup endpoint. The method checks getMe,
   getWebhookInfo, authenticated receiver readiness, one setWebhook call with
   `allowed_updates:["message"]` and `drop_pending_updates:false`, then verifies
   getWebhookInfo again. Unconfirmed mutations require inspection, not blind retry.
4. Stop the temporary binding/proxy, then immediately deploy the **normal**
   relay entrypoint to remove the operator export. Confirm external unauthorized
   requests are rejected and delivery remains disabled.

Receiver preflight intentionally tests the public HTTPS webhook endpoint. The
temporary operator needs `global_fetch_strictly_public` alongside `nodejs_compat`
to avoid same-zone workers.dev self-fetch returning404. Without it, the failure
occurs before setWebhook; no events are discarded. Do not replace this check by
ignoring failures or disabling secret verification. The normal relay deployment
does not need this flag. This is a public endpoint readiness probe, not the
license-service communication path, which always uses its service binding.

On2026-10-22 staging setup succeeded and the operator was removed. Evidence:
`work/telegram-webhook-20261022/REPORT.md`. Positive native pairing and sending
remain separate acceptance steps; the webhook alone does not prove them.
