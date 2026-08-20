import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import test from "node:test";

const execute = promisify(execFile);
const script = new URL("../scripts/generate_tauri_updater_manifest.mjs", import.meta.url);

test("creates a complete static Tauri updater manifest for both desktop targets", async () => {
  const directory = await mkdtemp(join(tmpdir(), "searchcar-updater-manifest-"));
  const notes = join(directory, "notes.md");
  const macSignature = join(directory, "mac.sig");
  const windowsSignature = join(directory, "windows.sig");
  const output = join(directory, "latest.json");
  await Promise.all([
    writeFile(notes, "Исправлена синхронизация лицензии.\n", "utf8"),
    writeFile(macSignature, "c2lnbmF0dXJlLW1hYw==\n", "utf8"),
    writeFile(windowsSignature, "c2lnbmF0dXJlLXdpbmRvd3M=\n", "utf8"),
  ]);
  await execute(process.execPath, [
    script.pathname,
    "--version", "0.2.0",
    "--notes-file", notes,
    "--pub-date", "2026-08-20T12:00:00Z",
    "--darwin-aarch64-url", "https://github.com/KaplunSergey/searchcar-desktop/releases/download/v0.2.0/SearchCar-mac.tar.gz",
    "--darwin-aarch64-signature-file", macSignature,
    "--windows-x86_64-url", "https://github.com/KaplunSergey/searchcar-desktop/releases/download/v0.2.0/SearchCar-Setup.nsis.zip",
    "--windows-x86_64-signature-file", windowsSignature,
    "--output", output,
  ]);
  const manifest = JSON.parse(await readFile(output, "utf8"));
  assert.deepEqual(manifest, {
    version: "0.2.0",
    notes: "Исправлена синхронизация лицензии.",
    pub_date: "2026-08-20T12:00:00.000Z",
    platforms: {
      "darwin-aarch64": {
        url: "https://github.com/KaplunSergey/searchcar-desktop/releases/download/v0.2.0/SearchCar-mac.tar.gz",
        signature: "c2lnbmF0dXJlLW1hYw==",
      },
      "windows-x86_64": {
        url: "https://github.com/KaplunSergey/searchcar-desktop/releases/download/v0.2.0/SearchCar-Setup.nsis.zip",
        signature: "c2lnbmF0dXJlLXdpbmRvd3M=",
      },
    },
  });
});

test("rejects an updater manifest with an insecure download URL", async () => {
  const directory = await mkdtemp(join(tmpdir(), "searchcar-updater-manifest-"));
  const notes = join(directory, "notes.md");
  const signature = join(directory, "release.sig");
  await Promise.all([writeFile(notes, "notes", "utf8"), writeFile(signature, "c2ln", "utf8")]);
  await assert.rejects(
    execute(process.execPath, [
      script.pathname,
      "--version", "0.2.0",
      "--notes-file", notes,
      "--darwin-aarch64-url", "http://example.test/mac.tar.gz",
      "--darwin-aarch64-signature-file", signature,
      "--windows-x86_64-url", "https://example.test/windows.zip",
      "--windows-x86_64-signature-file", signature,
      "--output", join(directory, "latest.json"),
    ]),
    /must be an HTTPS URL/,
  );
});
