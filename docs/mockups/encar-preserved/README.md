# Encar inside the multisource design preview

This is a disposable browser preview, not a production module extraction.
`vite.config.mjs` reads selected existing components from `app/page.tsx` and
uses the existing translations, dictionary, report filter and CSS. Neither
`Page` nor authentication/runtime/updater code is mounted. Shadow DOM keeps
auction styles outside the preserved Encar presentation.

Only boundary substitutions: localStorage becomes a private memory map,
query polling is disabled, API/external actions are replaced with fixture
reads or a demo notice. Favorites are simulated in memory. No backend,
Encar, Telegram, filesystem data or credentials are accessed.

From the repository root, build using the installed Node/Vite runtime:

```sh
node node_modules/vite/bin/vite.js build --config docs/mockups/encar-preserved/vite.config.mjs
node --test docs/mockups/encar-preserved/preview-data.test.cjs
python3 -m http.server 8773 --bind 127.0.0.1 --directory docs/mockups
```

Open `http://127.0.0.1:8773/auction-app-integration.html#encar`.
The ignored `dist/` is a regenerated preview bundle; rebuild when changing
the existing components or preview entry. Missing build has an explicit
message, not a silent fallback to the old schematic Encar UI.

Production Encar actions/icons/styles intentionally remain as they are.
The embedding boundary allows the existing project actions and filters to wrap
below 600 px; it does not change car markup, tokens or production CSS. Internal
filter/collapse state may reset when changing the prototype language or source.
The preview also top-aligns the statistics button like its neighboring articles;
the production styles themselves remain untouched.
Encar settings use the separately proposed settings form from the prototype.
Browser verification does not establish installed-app or Windows behavior.
