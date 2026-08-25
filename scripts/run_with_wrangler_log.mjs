import { spawnSync } from "node:child_process";

const [tool, ...args] = process.argv.slice(2);
const allowedTools = new Set(["vinext", "wrangler"]);

if (!tool || !allowedTools.has(tool)) {
  console.error("Expected an allowed local tool: vinext or wrangler.");
  process.exit(2);
}
if (args.some((value) => /[\r\n]/u.test(value))) {
  console.error("Tool arguments cannot contain line breaks.");
  process.exit(2);
}

const result = spawnSync(tool, args, {
  stdio: "inherit",
  env: {
    ...process.env,
    WRANGLER_LOG_PATH: ".wrangler/wrangler.log",
  },
  // npm/pnpm exposes local package binaries as .cmd shims on Windows.
  shell: process.platform === "win32",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
