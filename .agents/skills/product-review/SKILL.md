---
name: product-review
description: Review a diff or feature against requirements, regressions, error handling, tests, and relevant security risks. Use for requested review or a defined review gate, not automatic rewriting.
---

# Product Review

Find actionable defects with a concrete scenario and evidence.
Review-only by default; scope an implementation separately if requested.

## Review focus

Establish the comparison base, requested behavior, changed files and relevant
existing work. Read the affected execution path and tests, not only the diff.
Review requirements and regressions before stylistic preferences.

At changed boundaries, consider data loss, unknown vs empty values, identity,
authorization, validation, cancellation, retries, concurrency and partial
results. For external integrations, trace errors to the user-visible result;
a swallowed failure must not become success. Consider deduplication and
uncertain delivery where side effects can be retried.

Inspect whether tests actually assert the contract, cover meaningful failures
and failed for the right reason. Passing tests do not prove platform behavior
or live external availability they never exercised.

For UI changes, inspect a render before making visual claims. Identify missing
evidence explicitly if only source inspection is available. Reuse existing
component/style rules rather than proposing a redesign during correctness review.

## Findings

For each finding, give priority, precise file/symbol, triggering scenario,
user impact and the contract it violates. Keep a hypothesis separate from a
confirmed finding. Do not manufacture issues to fill a quota or report a
general best-practice preference as a bug.

Lead with findings, then open questions and verification gaps. If no defects
were found, say so with the scope and remaining risk. Do not automatically
edit files, publish a review, or spawn additional agents.
