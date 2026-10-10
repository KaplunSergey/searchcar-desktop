---
name: product-plan
description: Plan a product feature, module, or significant behavior change with acceptance criteria and ordered tasks. Use for planning or unresolved requirements, not every small edit.
---

# Product Plan

Turn the user's desired outcome into an executable, proportionate plan.
This skill works for desktop products and websites; discover the actual
project and its existing plans instead of assuming a stack or domain.

## Workflow

1. Identify the requested outcome, affected users, scope, and what is already
   agreed. Keep confirmed decisions distinct from suggestions and unknowns.
2. Read only the relevant current plan and implementation boundaries.
   Reuse the existing backlog; do not create a competing source of task status.
3. Ask about choices that materially change behavior, cost, safety, or UX.
   Explain a recommended default and its tradeoff. Continue independent work
   while an optional question is unanswered; don't invent the missing decision.
4. Describe observable acceptance criteria, including meaningful failure
   states. Map each criterion to a suitable test or inspection.
5. Order tasks by dependencies and identify research/migration prerequisites.
   Each task needs a bounded outcome and evidence required for completion.

For a large feature, include data/API/module boundaries, rollout and existing
behavior to preserve. For a small change, a brief outcome and checks suffice;
do not mandate an architecture document, diagram, or separate chat.

## Decision boundaries

- A plan request is not permission to implement, deploy, publish, or commit.
- Scope stays tied to the requested product; do not substitute a smaller
  feature for explicitly requested functionality in the name of simplicity.
- When a decision is unresolved, mark affected tasks as conditional rather
  than quietly treating a suggestion as accepted.
- Documentation or a fixture is not proof of a working feature.

## Handoff

Lead with the proposed outcome, then essential decisions/questions, ordered
tasks, acceptance evidence, and the next permitted step. Save a plan when
requested or useful for an ongoing feature; link it instead of repeating it.
