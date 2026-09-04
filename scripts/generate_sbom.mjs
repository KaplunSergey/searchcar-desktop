import { spawnSync } from "node:child_process";

const commands = process.platform === "win32" ? ["python", "py"] : ["python3", "python"];
for (const command of commands) {
  const result = spawnSync(command, ["scripts/generate_sbom.py"], { stdio: "inherit" });
  if (result.error?.code === "ENOENT") continue;
  process.exit(result.status ?? 1);
}
throw new Error("Python 3 was not found. Install Python 3.9+ and try again.");
