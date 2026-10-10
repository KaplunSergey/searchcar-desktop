---
name: searchcar-release
description: Build SearchCar for macOS/Windows or prepare an authorized version release using current runbooks. Use for packaging/updater work; a local build does not authorize publication.
---

# SearchCar Build and Release

First distinguish local preview build, release preparation, publication and
diagnosis. Perform only the stages the user requested.

## Current sources of truth

- Local packaging: [desktop build](../../../docs/DESKTOP_BUILD.md), actual
  platform scripts and relevant workflow.
- Version/release/publishing: [release runbook](../../../docs/RELEASE_RUNBOOK.md),
  `package.json`, version-sync script and current GitHub workflow inputs.

Read the relevant current document in full before executing its workflow.
Don't copy changing commands, artifact names, versions or secrets into this skill.

## Local build

Inspect branch, dirty worktree, tool availability and running application.
Locate the configured/bundled runtime before proposing a global installation.
Build the requested platform with the established script, inspect diagnostics,
and verify the produced artifact/version. If installation/replacement requires
closing the user's app or overwriting their copy, resolve that explicitly;
don't force-quit or replace it just because building is authorized.

A local build does not require a clean release branch and does not implicitly
authorize a version bump, commit, push, tag, workflow dispatch or publication.
Report that the artifact includes current worktree changes when applicable.

## Authorized release

Respect the current runbook's ordered gates and the requested stopping step.
Check clean/scoped release content, synchronized versions, required checks,
platform builds and signatures/checksums before publication. Never distribute
a failed/partially verified job as a completed release.

If unrelated changes prevent a clean candidate, don't automatically stage all
files or discard user work. Explain the exact candidate boundary and obtain
needed direction. Run license-service deployment only when authorized and
needed by the release, not because the runbook mentions it.

Verify installed version and repeat launch/shortcut after update, distinguishing
app executable from sidecar cache. Keep Windows and macOS smoke evidence
separate; updater signing isn't OS trust signing. Do not expose signing keys,
tokens, activation codes or user data in logs/notes.

## Handoff

Provide the real artifact/release link, version, branch, executed gates and
remaining unverified stages. Do not mark remote CI complete until its result
was observed. Commit/push/merge/release still require the user's request.
