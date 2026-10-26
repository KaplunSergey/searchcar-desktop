import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

test("product typecheck covers app sources, not disposable desktop build outputs", () => {
  const root = dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
  mkdirSync(join(root, "work"), { recursive: true });
  const fixture = mkdtempSync(join(root, "work", "typecheck-scope-"));
  try {
    writeFileSync(join(fixture, "generated.ts"), "export const generatedBuildOutput = true;\n");
    const config = ts.readConfigFile(`${root}/tsconfig.json`, ts.sys.readFile);
    assert.equal(config.error, undefined);
    const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
    assert.equal(parsed.errors.length, 0);
    const files = parsed.fileNames.map((path) => relative(root, path).split(sep).join("/"));
    assert.ok(files.includes("app/page.tsx"), "product code must remain typechecked");
    assert.ok(files.some((path) => path.startsWith("desktop/frontend/")), "desktop frontend must remain typechecked");
    const buildOutputs = files.filter((path) => path.startsWith("work/"));
    assert.equal(buildOutputs.length, 0, `temporary build files entered typecheck: ${buildOutputs.slice(0, 3)}`);
    const workerFiles = files.filter(path => path.startsWith("license-service/") || path.startsWith("telegram-relay/"));
    assert.deepEqual(workerFiles, [], "Cloudflare runtime declarations must not override browser DOM types");
    for (const [folder,entry] of [["license-service","src/telegram_authorization.ts"],["telegram-relay","ops/webhook.ts"]]) {
      const workerConfig = ts.readConfigFile(`${root}/${folder}/tsconfig.json`, ts.sys.readFile);
      assert.equal(workerConfig.error, undefined);
      const worker = ts.parseJsonConfigFileContent(workerConfig.config, ts.sys, `${root}/${folder}`);
      assert.equal(worker.errors.length,0);
      assert.ok(worker.fileNames.some(path=>path.endsWith(`/${folder}/${entry}`)), `${folder} must retain its own typecheck`);
    }
  } finally {
    rmSync(fixture, { recursive: true }); // Only this test's unique temporary directory.
  }
});
