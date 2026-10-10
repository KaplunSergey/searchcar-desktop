---
name: source-adapter
description: Research, implement, or repair a SearchCar source adapter for Encar, Copart, IAA, or another listing site. Use for source parsing/contracts, not unrelated UI or general product work.
---

# SearchCar Source Adapter

Keep source-specific collection and normalization isolated without breaking
the established Encar behavior. Generic product skills do not replace the
following domain requirements.

## Read by task

- New module/contract: [architecture](../../../docs/MULTISOURCE_ARCHITECTURE_PLAN.md)
  and the relevant task in [execution backlog](../../../docs/MULTISOURCE_IMPLEMENTATION_BACKLOG.md).
- Auction dates, lifecycle or delivery inputs:
  [auction decisions](../../../docs/AUCTION_MONITORING_PLAN.md).
- Existing source repair: actual parser, callers, related tests and sanitized
  fixtures first; do not reopen every new-source research gate automatically.

For new sources, honor the public-data feasibility and lifecycle decision
gates before implementing the module. Sources are developer-shipped with a
new application version, one source per project. Do not create a runtime
plugin marketplace or merge sites into one project as part of an adapter task.

## Collection and normalization

Identify transport, parsing and normalized mapping separately. Reuse the
existing queue/storage/worker when its semantics fit; don't duplicate an
application per site or invent a universal engine from one source.

Preserve filters and URL encoding. Validate source and listing identity;
use source plus external ID for cross-source identity. Keep monetary meaning,
currency, units, rental fee/bid/Buy It Now distinct. Unknown values remain
unknown instead of overwriting known data with zero.

Report selected scope, observed pages/listings, completeness and failure
separately. An intentional first-page scan isn't failed full coverage;
incomplete/blocked/missing search results are not proof of a sold vehicle.

For auctions retain raw schedule, timezone and precision; date-only is not an
exact time. Local "today" follows the computer timezone. Passing a scheduled
time is not confirmation of sale or completion. Consult accepted auction
decisions before changing retention or Telegram eligibility.

## Proof

Use sanitized real fixtures and deterministic contract/regression tests.
Include pagination, duplicates, empty vs failed/partial results, unavailable
details, changed identity/price and meaningful missing fields. Control time
in timezone tests. New behavior follows verified RED/GREEN.

Verify public access with a scoped live smoke when required and authorized;
fixtures don't prove current availability. Respect access controls and rate
limits; do not bypass CAPTCHA or require credentials the user doesn't have.
Record macOS and Windows evidence separately. Don't publish Telegram messages
just to test parsing.
