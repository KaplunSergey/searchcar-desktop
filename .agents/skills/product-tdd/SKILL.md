---
name: product-tdd
description: Implement or fix observable product behavior using verified RED/GREEN and targeted regression checks. Use for business rules and behavior changes, not prose or purely visual edits.
---

# Product TDD

Prove the requested behavior, not just that new code executes.
Use the project's existing test runner and fixtures unless they cannot test
the needed boundary. Read current callers and related tests before editing.

## Start from the baseline

Determine the acceptance criterion and the closest relevant checks. Preserve
existing uncommitted work. If a check already fails, record the failing case
and classify the cause before modifying its test or implementation.

## RED → GREEN → regression

1. Write the smallest meaningful test of the requested observable behavior.
   Prefer public behavior/data over source-text matching or implementation
   details. Use controlled time, sanitized fixtures and disposable storage.
2. Run it before the fix. Verify the failure is the missing/wrong behavior,
   not an import, runtime, fixture, mock, or permission problem. An unavailable
   runner is a blocked check, not RED.
3. Implement the requested behavior at the owning boundary and run the test
   again. Inspect other callers so the fix doesn't cover only one UI path.
4. Exercise affected normal/failure cases and nearby regression checks.
   Refactor duplication only while preserving the established behavior.

A characterization test for moving existing code should pass before the
refactor. Label it baseline, not RED. A test unexpectedly passing is evidence
to investigate, not a reason to deliberately break the product.

## When a test fails

Classify first: requested regression, pre-existing defect, stale expectation,
test/fixture fault, or environment failure. Establish the actual user contract
before changing an assertion. Never weaken a check solely to make it green.
Fix failures caused by this task; disclose unrelated failures and expand the
repair only with appropriate scope. Recheck after each supported correction.

Do not impose arbitrary coverage percentages, RED/GREEN commits, Docker,
framework changes, or a full suite for a one-line style change. These are
task decisions, not prerequisites of TDD.

## Evidence

Report the criterion, why RED failed, GREEN result, regression checks and any
not-run/blocked checks. Do not describe structural or mocked success as a
verified live integration or proof on an untested operating system.
