#!/usr/bin/env node

import { webcrypto } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const privateOutput = resolve(process.argv[2] ?? "license-signing-private-key.key");
const publicOutput = resolve(process.argv[3] ?? "license-signing-public-key.json");
const keyId = process.argv[4] ?? "searchcar-license-v1";
const keys = await webcrypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const privateKey = Buffer.from(
  await webcrypto.subtle.exportKey("pkcs8", keys.privateKey),
).toString("base64url");
const publicKey = Buffer.from(
  await webcrypto.subtle.exportKey("raw", keys.publicKey),
).toString("base64url");

await mkdir(dirname(privateOutput), { recursive: true });
await mkdir(dirname(publicOutput), { recursive: true });
await writeFile(privateOutput, `${privateKey}\n`, { encoding: "utf8", mode: 0o600 });
await writeFile(
  publicOutput,
  `${JSON.stringify({ algorithm: "Ed25519", key_id: keyId, public_key: publicKey }, null, 2)}\n`,
  "utf8",
);

console.log(`Private signing key: ${privateOutput}`);
console.log(`Public verification key: ${publicOutput}`);
console.log("Keep the private file offline and set it as the Worker secret LICENSE_SIGNING_PRIVATE_KEY.");
