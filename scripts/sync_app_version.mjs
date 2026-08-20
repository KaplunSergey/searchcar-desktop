#!/usr/bin/env node

import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const write = process.argv.includes("--write");
const packagePath = new URL("package.json", root);
const targets = [
  {
    label: "Tauri configuration",
    path: new URL("desktop/src-tauri/tauri.conf.json", root),
    read: (source) => JSON.parse(source).version,
    update: (source, version) => `${JSON.stringify({ ...JSON.parse(source), version }, null, 2)}\n`,
  },
  {
    label: "Cargo package",
    path: new URL("desktop/src-tauri/Cargo.toml", root),
    read: (source) => source.match(/^version\s*=\s*"([^"]+)"/m)?.[1],
    update: (source, version) => source.replace(/^version\s*=\s*"[^"]+"/m, `version = "${version}"`),
  },
  {
    label: "backend",
    path: new URL("backend/app/version.py", root),
    read: (source) => source.match(/^APP_VERSION\s*=\s*"([^"]+)"/m)?.[1],
    update: (_source, version) => `\"\"\"Build version shared by the desktop backend and API.\"\"\"\n\nAPP_VERSION = "${version}"\n`,
  },
  {
    label: "frontend",
    path: new URL("app/version.ts", root),
    read: (source) => source.match(/^export const APP_VERSION\s*=\s*"([^"]+)"/m)?.[1],
    update: (_source, version) => `/** Generated from package.json by scripts/sync_app_version.mjs. */\nexport const APP_VERSION = "${version}";\n`,
  },
];

const packageJson = JSON.parse(await readFile(packagePath, "utf8"));
const version = packageJson.version;
if (typeof version !== "string" || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/u.test(version)) {
  throw new Error(`package.json version must be SemVer, received ${JSON.stringify(version)}`);
}

const mismatches = [];
for (const target of targets) {
  let source;
  try {
    source = await readFile(target.path, "utf8");
  } catch (error) {
    if (!write || error?.code !== "ENOENT") throw error;
    await writeFile(target.path, target.update("", version), "utf8");
    console.log(`Created ${fileURLToPath(target.path)} at v${version}`);
    continue;
  }
  const actual = target.read(source);
  if (actual === version) continue;
  if (write) {
    await writeFile(target.path, target.update(source, version), "utf8");
    console.log(`Updated ${fileURLToPath(target.path)} to v${version}`);
  } else {
    mismatches.push(`${target.label}: expected v${version}, found ${actual ? `v${actual}` : "no version"}`);
  }
}

if (mismatches.length) {
  throw new Error(`Version metadata is out of sync. Run \`pnpm version:sync\`.\n${mismatches.join("\n")}`);
}

console.log(`Version metadata is synchronized at v${version}.`);
