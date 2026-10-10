---
name: product-ux
description: Design user flows, screen states, and reusable visual rules for product interfaces or websites. Use for UX/design work, not routine copy edits or backend-only changes.
---

# Product UX

Design for the user's task and the project's existing visual language.
A design tool is optional; use available files and inspectable prototypes.

## Establish the design context

- Read the relevant brief, screenshots, existing components and styles.
  Separate reference appearance from explicit user requirements.
- For an application, prioritize frequent actions, data density, navigation,
  filters, feedback, and retaining user context.
- For a public website, prioritize audience, content, hierarchy, responsive
  behavior and conversion. Add SEO only where public indexing is relevant.
- Respect a request for a plan or mockup only. Do not modify production UI,
  install a tool, or connect an external design service just to propose it.

## Design the behavior before polishing

Map the main flow and relevant empty, loading, error, disabled, partial and
success states. Resolve where actions belong and what their scope is before
choosing decoration. Test long labels, missing data, keyboard focus and the
smallest supported viewport; match the project's languages.

Find existing semantic tokens and components first. Reuse primitives where
behavior matches; keep domain-specific compositions separate when their
information or actions differ. Similar styling is not a reason to force
unrelated cards into a universal component with many conditional fields.

## Keep the design and implementation aligned

For design-system work, define token roles, component variants/states, and
usage rules. Choose a maintained source of truth with the user; a mockup
library and a code library do not synchronize automatically.

For a new screen, show an inspectable mockup/prototype and what is reused.
If implementation is requested, verify the rendered result, interactions,
contrast/focus, overflow and reduced-motion behavior where animation exists.
Do not claim visual verification from source code alone.

## Handoff

Report the key UX decisions, relevant states, reused/new elements and evidence.
Distinguish a proposed layout, a saved mockup and a working interface.
