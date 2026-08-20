#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

function fail(message) {
  throw new Error(`Updater manifest: ${message}`);
}

function parseArguments(args) {
  const values = new Map();
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index];
    if (!flag.startsWith("--")) fail(`unexpected argument ${JSON.stringify(flag)}`);
    const value = args[index + 1];
    if (!value || value.startsWith("--")) fail(`missing value for ${flag}`);
    if (values.has(flag)) fail(`duplicate argument ${flag}`);
    values.set(flag, value);
    index += 1;
  }
  return values;
}

function required(values, flag) {
  const value = values.get(flag);
  if (!value) fail(`missing required ${flag}`);
  return value;
}

function validateVersion(version) {
  if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/u.test(version)) {
    fail(`version must be SemVer, received ${JSON.stringify(version)}`);
  }
  return version;
}

function validateHttpsUrl(value, flag) {
  let url;
  try {
    url = new URL(value);
  } catch {
    fail(`${flag} must be an absolute HTTPS URL`);
  }
  if (url.protocol !== "https:" || url.username || url.password) {
    fail(`${flag} must be an HTTPS URL without credentials`);
  }
  return url.toString();
}

async function readSignature(path, flag) {
  const signature = (await readFile(path, "utf8")).trim();
  if (!/^[A-Za-z0-9+/=_-]+$/u.test(signature)) {
    fail(`${flag} must contain one non-empty base64 signature`);
  }
  return signature;
}

async function main() {
  const values = parseArguments(process.argv.slice(2));
  const version = validateVersion(required(values, "--version"));
  const output = required(values, "--output");
  const notesPath = required(values, "--notes-file");
  const pubDate = values.get("--pub-date") || new Date().toISOString();
  if (Number.isNaN(Date.parse(pubDate))) fail("--pub-date must be an RFC 3339 date");

  const platforms = {};
  for (const platform of ["darwin-aarch64", "windows-x86_64"]) {
    const prefix = `--${platform}`;
    platforms[platform] = {
      url: validateHttpsUrl(required(values, `${prefix}-url`), `${prefix}-url`),
      signature: await readSignature(required(values, `${prefix}-signature-file`), `${prefix}-signature-file`),
    };
  }

  const manifest = {
    version,
    notes: (await readFile(notesPath, "utf8")).trim(),
    pub_date: new Date(pubDate).toISOString(),
    platforms,
  };
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  console.log(`Wrote signed updater manifest for v${version}: ${output}`);
}

await main();
