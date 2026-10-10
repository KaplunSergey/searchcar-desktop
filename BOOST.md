# SearchCar working rules

## Scope and evidence

- Distinguish planning, diagnosis, implementation, build and publication.
  A question/plan is not permission for a fix; a local build isn't a release.
- Use the existing execution backlog for the current feature. Complete and
  verify the requested task, not an unrequested next task from that backlog.
- Preserve unrelated uncommitted work, especially existing Windows diagnostics.
  Commit/push/merge/release only when requested; don't stage the entire dirty
  checkout without resolving which changes belong in the candidate.
- When working in a feature branch, report its actual name at meaningful
  handoffs/builds/commits. Don't hard-code today's branch as a permanent rule.
- Behavioral changes use TDD: inspect baseline, verify RED's cause, implement
  GREEN, run affected regression checks. Investigate failures before changing
  assertions. Old failures and unavailable tools are not successful checks.
- A source-text test, fixture, browser or macOS run does not prove real source
  availability or Windows installed-app behavior. Report evidence separately.

## Skill selection

Use the smallest set matching the task and read each selected `SKILL.md` fully.
The process scales to risk; a copy/spacing change doesn't need a full plan.

| Request | Relevant skill(s) |
| --- | --- |
| Feature plan, unresolved requirements | `product-plan` |
| UX, new screens, visual system | `product-ux` |
| New behavior or authorized bug fix | `product-tdd`; `product-debug` for a defect |
| Why something failed, existing test failure | `product-debug` |
| Review a diff/feature | `product-review` |
| Verify an implemented change/build | `product-check` |
| Source collection/parsing/normalization | `source-adapter`, plus TDD for implementation |
| macOS/Windows packaging or release | `searchcar-release`, plus completion checks |

Use bundled `skill-creator` for creating/updating skills. Ponytail governs
coding/refactoring/dependency choices only. It does not govern product research,
requirements, planning, UX or visual design, and cannot remove explicitly
requested behavior, meaningful tests, error handling or accessibility.

Review roles are optional and read-only. Delegate only with user authorization
or an applicable explicit instruction; installing roles does not authorize
launching them. Do not change the user's model, permissions or plugin settings.

## Contextual project routes

- Current work: `docs/CURRENT_PRODUCT_PLAN.md`.
- Source module boundaries: `docs/MULTISOURCE_ARCHITECTURE_PLAN.md`.
- Task order/status: `docs/MULTISOURCE_IMPLEMENTATION_BACKLOG.md`.
- Auctions/lifecycle/Telegram decisions: `docs/AUCTION_MONITORING_PLAN.md`.
- Site grouping/navigation: `docs/SOURCE_NAVIGATION_UX_PLAN.md`.
- Deferred visual system: `docs/DESIGN_SYSTEM_PLAN.md`.
- Local desktop packaging: `docs/DESKTOP_BUILD.md` and actual platform scripts.
- Release gates: `docs/RELEASE_RUNBOOK.md` and current workflow inputs.
- Using/maintaining this set: `docs/PRODUCT_SKILLSET.md`.

Read the routes relevant to the task, not the whole table. For a new source,
honor feasibility/decision prerequisites; for an existing source bug, trace the
actual parser and its callers first. Preserve site isolation and existing
Encar behavior. Never infer sold from incomplete search or an elapsed auction.

Design-system implementation is deferred until the user returns to its plan.
Don't use that deferral to block explicitly requested UI fixes. General skills
stay portable: don't introduce SearchCar/Tauri/Encar into an unrelated website.
