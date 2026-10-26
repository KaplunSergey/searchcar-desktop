# Auction calendar page prototype

## Apple Reminders + Windows Outlook candidate, 25 October 2026

**Research resumed by the user26October2026; no new fix/deployment yet.**
The previous deferral below is historical. Read-only inspection of the signed
archive found all24 actions and import questions identical to the unsigned
candidate, and no nested run-workflow action. Apple's signer canonicalized the
plist and removed `WFWorkflowName`; the source name alone therefore does not
prove the imported library name. Native lookup/confirmation/alarm remain open.
An independent primary reverse-engineering report on iOS17.4 describes lookup
through `uniqueVisibleReferenceForWorkflowName`. Combined with the user's two
same-labelled tiles, this makes duplicate/ambiguous names a concrete hypothesis,
not a confirmed diagnosis on the user's unknown iOS version. Recommended device
probe: rename one existing copy to a different name, ensure the remaining name
exactly matches `SearchCar Reminder`, and retry the existing Preview with a fresh
synthetic date. No deleting/reinstalling, undocumented ID route or source patch
is justified before observing this result. Research evidence:
`work/reminder-research-20261026.AoiM13Nr/REPORT.md`.

**Deferred by the user25October2026:** native lookup still fails even after the
`+` to `%20` fix. A direct tap of the installed tile shows the expected missing
title guard with no supplied JSON; this proves that guard executes, not successful
reminder creation. Two installed tiles are visible; duplicate names are not a
proven cause. Exact device-side lookup cause remains unresolved. Do not keep
changing the prototype, reinstalling it or promote Preview while working on the
next backlog item. Return to this defect only when the user resumes it.

Published to a separate preview on25 October2026 (details below); the canonical
Telegram page is unchanged. The user chose a reminder15 minutes before the confirmed
auction start through Apple Shortcuts and also requested a Windows option.
Google/ICS and existing Telegram message URLs remain unchanged.

- `Apple · Напоминание` passes validated public JSON to the fixed command name
  `SearchCar Reminder`. It does not fetch an ICS or call our server. Past alert
  times are unavailable; click/focus/timer rechecks prevent an old page launching
  an elapsed alert. Native prototype guards also reject empty/past dates.
- A one-time installer is served at `/SearchCar%20Reminder.shortcut`. The
  import question asks for an iCloud Reminders list. Keep the exact command
  name; sync depends on the same Apple Account and iCloud Reminders on each
  device. First run may request Reminders permission. Creation follows an
  explicit confirmation with title, alert date and listing URL in notes.
- `Outlook · Windows` opens an Outlook.com browser draft for a personal
  Microsoft account, not necessarily a Windows native app. Subject/start UTC/
  listing/notes are prefilled. No invented end is passed. Microsoft may select
  a default end/reminder; user must check those before Save. Corporate365 and
  automatic account writes are not implemented.
- Neither imported reminders nor calendar snapshots automatically follow later
  auction moves; no claim of deduplication in the user's calendar is made.

`scripts/build_reminder_shortcut.py --output <new-file>` deterministically builds
the unsigned XML plist without importing or running it. The candidate was
signed using `shortcuts sign --mode anyone`; Apple receives the definition for
validation, not event data/contact details. Signed binary in `public/` has
SHA256 `d7856a4c925cbf1459794bd311c28de94cdfb161458a2a93730da67d4937bb96`.
The generator uses published action field definitions, not a private shortcut
database: [Apple URL invocation](https://support.apple.com/en-gb/guide/shortcuts/apd624386f42/ios),
[Apple signing](https://support.apple.com/guide/shortcuts-mac/run-shortcuts-from-the-command-line-apd455c82f02/mac),
[SCPL action schema](https://github.com/pfgithub/scpl/blob/master/docs/actions/addnewreminder.md),
[Cherri plist structure](https://github.com/electrikmilk/cherri/blob/main/shortcut.go).

Verified locally:24 Node tests,1 plist/action contract test, TypeScript,
Vite/Functions build and5 browser fixture checks on RU/UK360/800/1280.
The signed archive is checked for its AEA1 header and served with the expected
MIME/inline/no-store headers. This does **not** prove import or execution on
iPhone/Mac. Native editor control is unavailable (Computer Use permission not
granted); no reminder was created and no Outlook sign-in/save was attempted.
Local Wrangler required an isolated temp folder to avoid the root deploy
config and a CLI-only2026-05-22 date (installed workerd maximum). The actual
calendar configuration stays2026-10-21. No tools/dependencies were upgraded.

Before enabling this as the main hosted path: manual import and confirm/cancel
on iPhone, exact instant/alarm
and iCloud sync to Mac, empty/expired input, direct execution on Mac, and an
Outlook save/reminder check. File signing and browser tests do not close these
platform gates. The ordinary Telegram URL is not switched to a preview build.

### Separate device preview published25 October2026

Device follow-up: the user's iPhone screenshot shows `SearchCar Reminder`
installed but an error looking for `SearchCar+Reminder`. The URL had form-style
spaces from `URLSearchParams`; Shortcuts preserved the literal plus. Apple's
[URL example](https://support.apple.com/guide/shortcuts/run-a-shortcut-from-a-url-apd624386f42/ios)
uses `%20`. Normalize encoded spaces for **all** Shortcut parameters, including
JSON text, while keeping literal `+` as `%2B`. Native percent-decoding regression
failed with the exact reported name before the fix and passes afterwards.
37 Node checks, TypeScript/build and5 existing browser checks passed. No need
to reinstall or rename the existing command.

Corrected Preview deployment: `93831d00-03ea-4716-8a08-649cb567491f`, same
`apple-reminder-preview` branch and alias:
https://93831d00.searchcar-auction-calendar-preview.pages.dev/
Use this new deployment for retesting; the earlier immutable URL below still
contains the old code. Live HTML/both assets match the checked fixed build;
the Shortcut archive is unchanged and canonical production bytes remain unchanged.
Native run/confirmation/alarm still require the user's retry on iPhone.
Evidence: `output/playwright/calendar-reminder-urlfix-20261025-REPORT.md`.

User authorized only the isolated device preview. Pages deployment
`f62877d0-db35-4a5e-b39c-f33ff0b5159e` is **Preview**, branch
`apple-reminder-preview`:
https://f62877d0.searchcar-auction-calendar-preview.pages.dev/
Alias: https://apple-reminder-preview.searchcar-auction-calendar-preview.pages.dev/
The bare page requires an event fragment; use the populated test link recorded
in `output/playwright/calendar-reminder-20261025-live.json`.

Uploaded only a fresh copy of the checked `work/calendar-page/dist` from an
isolated temporary directory. Kept compatibility date2026-10-21; no secrets,
bindings, main application, relay or licensing deployment changed. Live HTML,
both JS/CSS assets and the signed Shortcut match local bytes. The installer
returns `application/x-apple.shortcut`, inline disposition and no-store; page
security headers and ICS UTC/start/URL/-15-minute alarm, HEAD200 and malformed400
passed. No native command was launched and no calendar/reminder was written.

Production remains `d447b1f2-b491-49fc-ae18-cde646b7ae37`, branch
`calendar-preview`; canonical HTML hash before/after is unchanged:
`95f2ca3792b359051a4294641cd18d003f27ad7e22cf08f4346e2b4abd11d0d3`.
No desktop rebuild, version bump, commit or push. Native iPhone/Mac and Outlook
acceptance gates above remain open.

Prepared for the calendar action attached to the same Telegram message as the
vehicle details. The static test page is deployed at
https://searchcar-auction-calendar-preview.pages.dev/ (21 October 2026).
Manual Telegram delivery is now enabled separately on staging. Current application source uses
this published address by default in the vehicle-message contract and calendar
preview actions. No new app package was built for this calendar fix; no new
user-facing settings or arbitrary public-host override were added.

Build with the existing frontend toolchain:

```sh
pnpm calendar:build
```

Output: `work/calendar-page/dist/` (`index.html`, local `assets/`, `_headers`,
`_routes.json` and compiled `_worker.js/index.js`). Serve the
complete folder from a separately approved HTTPS location; do not upload the
repository, desktop data or license-service configuration. No new dependency,
database, bot, OAuth or analytics is needed. The local candidate adds one
read-only Pages Function, published with user approval on 24 October 2026.
Deployment/provider/domain selection requires separate approval. Prefer host
headers `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and
`Content-Security-Policy: frame-ancestors 'none'` in addition to the page's meta
CSP. Do not inject analytics/external scripts into an event page.

`calendar/public/_headers` is copied by Vite and applies these protections on
Pages. `calendar/wrangler.jsonc` declares a separate project with no
bindings or secrets. The proposed project name is
`searchcar-auction-calendar-preview`; it was created for this task after the
user confirmed browser sign-in. It is separate from the main/licensing service.

## iPhone Safari defect — published candidate, 24 October 2026

The user reports that the Apple action does not open Calendar even in Safari.
The original implementation used a Blob URL plus `download`, so the server
never supplies a calendar response. Desktop download checks did not prove
native iOS handoff. The exact device-side failure is not independently observed.

The candidate replaces this action with same-origin `GET /event.ics?event=…`:
`text/calendar; charset=utf-8`, inline filename, no-store/no-referrer/nosniff,
one event and the existing -15-minute alarm. No subscription, invented end,
calendar write, account access, source fetch or event storage is added. All
event validation and ICS escaping are reused. Invalid/expired/oversize/private
payloads, extra/duplicate query fields and unsupported methods fail closed.
`_routes.json` invokes the function only for `/event.ics`; other files stay static.
Public event fields remain in the fragment on page load and reach the server
only when opening the Apple/Outlook action. Do not claim that URL query data
is absent from infrastructure request handling/logs; only application storage
and explicit payload logging are absent.

Build uses Wrangler's directory output. In the pinned CLI, deprecated
`--outfile` produces a multipart upload, not executable `_worker.js`; do not
copy that multipart file into the deployment. The resulting Worker directory
was exercised through local Pages, not just a static Python server.

Verified RED on the old Blob action; 17 targeted Node checks and local
Pages/browser scenarios pass (RU/UK, Copart/IAA, 360/800/1280, headers, HTTP ICS,
invalid links and expiry). Evidence: `output/playwright/calendar-https-*-20261024/`.
No external requests or calendar writes occurred. Native iPhone import/save and
the applied reminder remain acceptance gates, not proven by HTTP success.
The user approved publication and the changed server-processing boundary.
Published only the checked isolated calendar folder to production branch
`calendar-preview` of this test project (not the main app, relay or license service).
Deployment: `d447b1f2-b491-49fc-ae18-cde646b7ae37`,
https://d447b1f2.searchcar-auction-calendar-preview.pages.dev.
The canonical URL is unchanged. Both URLs passed live HTTP checks: HTML/JS/CSS
match checked build bytes; security headers, Copart/RU and IAA/UK ICS, exact
UTC, URL/alarm, HEAD, malformed400 and POST405 are correct. No calendar write.
The first asset-verification selector missed `./assets` and checked zero files;
corrected the check, required exactly two assets, then verified both on both hosts.
Native iPhone acceptance still requires the user's actual Safari tap/save;
publication alone does not close the defect. Rollback reference:
`e2364437-bea5-41f3-a6fc-e6671dbc026d`. No new .app was built.

## Published test page — 21 October 2026

The user requested publication and completed OAuth sign-in. Only Pages-write,
account-read and user-read scopes were requested (Wrangler adds offline_access).
The selected account had no Pages projects; no existing site was overwritten.
Created the separate `searchcar-auction-calendar-preview` project with production
branch `calendar-preview`, then uploaded only the checked static folder from a
new temporary staging directory outside the repository. The effective upload
configuration named this calendar project; the main generated config was not
changed. No Functions or database/secret bindings were added.

Canonical URL: https://searchcar-auction-calendar-preview.pages.dev/

Deployment: `e2364437-bea5-41f3-a6fc-e6671dbc026d`,
https://e2364437.searchcar-auction-calendar-preview.pages.dev

The canonical HTTPS URL returned 200. HTML, JS and CSS matched the checked local
build byte-for-byte. Published CSP, no-referrer, DENY, nosniff, noindex and
Permissions-Policy headers were observed. The actual deployed page was opened
in the in-app browser with an explicitly labelled test event for
2026-10-22T13:30:00Z (15:30 Europe/Warsaw); title, local time, Google URL and ICS
download action rendered, with no captured errors/warnings. No Google/Apple
event was saved, no bot message was sent, and no auction site was fetched.
This is hosted-page evidence, not iOS/Android/Telegram/native calendar acceptance.
The bare URL intentionally says the event is unavailable; an event fragment is
required. The test date expires after its start, as for any message link.

Earlier preparation notes below record the previous blocked attempt, not the
current publication state.

## Publication preparation — 21 October 2026

The user asked to continue after discussing Pages hosting. Existing Wrangler
authorization was checked, but token refresh failed with HTTP 400 / “Not logged
in”. No project-create or deploy call was made. Ask the user to confirm browser
sign-in; never request passwords or API tokens in chat. Account/project identity
must be resolved before uploading, and an existing same-name project must not
be overwritten without verifying that it belongs to this task.

Upload archive: `work/calendar-page/SearchCar-calendar-pages.zip` (about 12 KB),
containing only `index.html`, `_headers` and the two generated JS/CSS assets.
ZIP integrity and the local page/header checks passed. No bot, Functions,
bindings, repository source, `.wrangler` settings or secrets are included.

Known local tooling boundaries: this repository has a generated
`.wrangler/deploy/config.json` for the main site. The pinned Wrangler 4.92.0
attempts to discover it from parent directories, and Pages rejects `--config`.
Even a nested Pages config can therefore conflict with that main-site redirect.
Do not delete or rewrite the main site's generated config to publish this page.
Use a separately staged folder outside the repository and an explicit new Pages
project name, or upload only the checked ZIP through the Pages dashboard.
Inspect effective config and the exact upload contents before any CLI deploy.
The installed local workerd supports dates only through 2026-05-22, so the local
static Pages smoke used that date, not the current production compatibility
date in the source config. No dependency upgrade was performed. Local header
checks are not proof of published response headers or native calendar import.

Before adding `_headers`, the running local Pages check failed because the
Referrer-Policy header was absent. After adding it, all four browser/HTTP
scenarios passed in `work/calendar-page/pages-green`, with zero external
requests/calendar writes. Earlier runtime-start/config errors are retained in
the work logs and are not counted as RED or as successful checks.

The user confirmed there is no custom domain. A proposed deployment option is
Cloudflare Pages Direct Upload, which gives a `<project>.pages.dev` address;
buying/connecting a custom domain is not required. Upload only the built folder
after account/project/publication approval. No Pages project was created or
published by this task. See the [official Direct Upload guide](https://developers.cloudflare.com/pages/get-started/direct-upload/).

`calendarPageLink` prepares a bounded HTTPS URL using a fragment containing only
version, source, listing ID, title, clean listing URL, UTC start, round and
language. The fragment is not sent in the HTTP request to the host, but anyone
holding the complete link can read/forward those public vehicle fields. This
is not an authenticated source assertion: recipients must recheck the listing.
No project name, internal car ID, cookies, bot token or owner settings are
included. `readCalendarPage` validates the portable event and reuses the existing
calendar generator, including source URL checks, UTC, ICS escaping/folding and
the 15-minute display alarm. No source site is fetched by the page.

The message formatter defaults to the approved published `calendarPageUrl`. With it,
the same message's inline keyboard offers “Добавить в календарь” / “Додати до
календаря”. If a portable-page URL cannot be formed, it retains the Google action;
no example domain or localhost address is configured in the application.
The formatter must be called on fresh eligible data before future delivery.

The actual desktop preview uses that same event-page URL for its primary action
and clipboard copy (including the manual-copy fallback). Direct Google opening
and local ICS remain explicit alternatives. Both browser actions use the
existing authenticated desktop external-opener endpoint and show failure/retry;
they do not navigate the app WebView. The backend permits only the exact
published HTTPS host/root, default/443 port, no query/params, a nonempty event
fragment and URL length <=2048 for this new destination; it does not allow
arbitrary Pages hosts or preview subdomains. Event-payload validation stays in
the page parser; this allowlist is an origin/route check, not a calendar-data
authenticity assertion. No source, Telegram or Google allowlist was broadened.

The published page shows the device's timezone, Google prefill, and the inline
HTTP ICS response for Apple Calendar / Outlook described above.
Nothing is automatically saved; the
recipient confirms import/save and checks the reminder. No invented end time,
subscription or automatic rescheduling is added. Expired/incomplete/unsafe
links disable actions. Layout and messages support RU/UK and 360px screens.

This is a portable prototype, not verified mobile calendar support. Real taps
from Telegram, iPhone/Apple Calendar, Android/Google Calendar, macOS/Apple
Calendar, Windows/Outlook and desktop Google Calendar remain acceptance gates.
In particular, the original Blob download failed the user's iPhone acceptance.
The approved HTTPS response is now published but native import remains unverified;
do not claim an instruction to open another browser closes this gate. Google
prefill does not guarantee a 15-minute reminder. Authenticated Calendar API
access is outside this version.

## Local checks

Serve the complete built folder using local `wrangler pages dev` on loopback
(never a user's data directory), then run:

```sh
.desktop-build-venv/bin/python scripts/calendar_page_ui_smoke.py --base-url http://127.0.0.1:1422/ --check-headers
```

Use an isolated temporary staging directory outside the repo and the local
compatibility-date override described above. A plain Python HTTP server cannot
run the ICS function or interpret `_headers` and is not a GREEN check now.

The script uses the existing bundled Playwright runtime, fixed clock, public
fixture identities and local HTTP responses; it does not contact Google,
Telegram or the auction sites or write any calendar event. Existing app UI
coverage includes the branded Telegram-preview trigger and keyboard/mobile
behavior via `scripts/auction_workspace_ui_smoke.py --only 'Telegram preview'`.

Telegram branding on that app button uses the official paper-plane path from
[Telegram's SVG](https://telegram.org/img/t_logo.svg), permitted by its
[logo guidance](https://telegram.org/tour/screenshots#logotypes). The button
uses a darkened Telegram blue for white-text contrast and retains its honest
preview label. The colors of actual Telegram inline buttons are controlled
by the Telegram client, not this app's CSS.
