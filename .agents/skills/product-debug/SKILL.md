---
name: product-debug
description: Diagnose a product defect or failing check by reproducing it and tracing evidence to its cause. Use for debugging; a diagnosis-only question does not authorize a fix.
---

# Product Debug

Explain the cause with evidence before choosing a fix.

## Investigation

1. Capture expected vs actual behavior, version/platform, inputs and timing.
   Use existing sanitized logs/support bundles when available; don't expose
   credentials, personal data, or activation material.
2. Reproduce with the closest disposable fixture or safe read-only diagnostic.
   Record baseline failures and the exact command/steps. If reproduction is
   incomplete, keep the conclusion explicitly conditional.
3. Trace the actual path through caller, transport, parser/API, persistence,
   queue and UI as applicable. Examine other callers of the suspected owner.
4. Compare hypotheses against evidence and identify the earliest boundary
   violating the contract. Separate a cause from downstream symptoms.

For installation/update bugs, distinguish the launched executable, installed
resources, runtime/cache, shortcut target and displayed version. Old cache
directories alone do not prove the wrong application is launched.

## Boundaries

Read-only diagnostics can proceed within the task. A question asking why an
error occurs is not permission to change code, clear caches, delete installs
or migrate user data. If fixing is requested, first add a regression check
for the proven cause and follow the project's TDD practice.

Distinguish application defects from environment, access/rate limits, stale
fixtures and test faults. Do not "fix the tests" until their expectation is
compared with the accepted behavior. Never mask an external failure as empty
results or silently turn unknown data into a destructive status update.

## Handoff

Give the cause or ranked hypotheses, supporting evidence, impact, proposed
repair and what remains unverified. State what additional evidence would
resolve uncertainty; do not require inaccessible evidence before exhausting
safe alternatives already available.
