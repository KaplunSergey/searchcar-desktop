---
name: product-check
description: Verify an implemented change or build before handoff using risk-matched tests and runtime evidence. Use for completion checks, not research-only or planning-only requests.
---

# Product Check

Establish what is actually verified before handing off the requested change.
Use current project manifests, scripts and runbooks instead of memorized commands.

## Choose evidence by the changed boundary

- Behavior/data: relevant deterministic tests and affected regression cases.
- Types/build: checks for the touched package and consumers of changed contracts.
- UI: rendered view and important interactions, viewport/language states.
- External integration: fixtures/contracts plus authorized live smoke when
  needed; label them separately.
- Installer/updater: real artifact/installed-app evidence on each target OS.
  Browser success or a cross-platform source file is not a Windows/macOS smoke.
- Instructions/configuration: parse/schema/link checks and host discovery;
  these do not prove the model's future behavior.

Reuse a check already run on the same final code/state. Run more only where
the risk warrants it. Do not install a framework to validate a trivial edit.
Follow strict release gates when preparing an actual release.

## Interpret failures

Determine whether the failure belongs to the current change, the baseline,
the test, or the environment before altering code or expectations. Preserve
the initial error. Correct in-scope defects and rerun affected checks; report
other failures without presenting the overall check as green.

Before completion, inspect the scoped diff and detect whitespace/unintended
changes. Preserve unrelated user work. Don't package unreviewed changes just
because they are present in the same checkout.

## Handoff

Lead with the delivered outcome and usable file/artifact. Include concise
passed, failed, blocked/not-run evidence and the next permitted step.
Never treat a build request as permission for version bumps, commits, push,
tags, releases, deployment or messages. Verification does not grant those
actions either.
