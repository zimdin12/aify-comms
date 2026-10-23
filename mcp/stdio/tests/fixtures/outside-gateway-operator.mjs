// An operator-shaped process that does not inherit the test runner's gateway ancestry.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { isAlive, startTimes } from "aify-wrapper/lib/process-identity.mjs";

export class OutsideGatewayOperator {
  constructor(pid, startedAt) {
    this.pid = pid;
    this.startedAt = startedAt;
  }

  static start({ sessionId, env }) {
    assert.match(sessionId, /^[A-Za-z0-9_-]+$/);
    // Bound an admission failure too: a broker-created child cannot be held through a ChildProcess handle.
    const argv = ["-e", "setTimeout(()=>{},180000)", "hermes", "--resume", sessionId];
    let result;
    if (process.platform === "win32") {
      // CIM's provider, not this runner, creates the process. A detached spawn alone keeps our ancestors.
      const script = String.raw`
$ErrorActionPreference = 'Stop'
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
$variables = @($data.env.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" })
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
  ShowWindow = [uint16]0
  CreateFlags = [uint32]0x00000400
  EnvironmentVariables = [string[]]$variables
}
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = [string]$data.commandLine
  CurrentDirectory = [string]$data.cwd
  ProcessStartupInformation = $startup
}
if ($result.ReturnValue -ne 0) { throw "Win32_Process.Create refused: $($result.ReturnValue)" }
@{ pid = $result.ProcessId } | ConvertTo-Json -Compress
`;
      const commandLine = [`"${process.execPath}"`, ...argv.map(word => `"${word}"`)].join(" ");
      result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
        encoding: "utf8", windowsHide: true, timeout: 15000, env,
        input: JSON.stringify({ commandLine, cwd: process.cwd(), env }),
      });
    } else {
      // Once this short-lived broker exits, the operator is reparented outside the runner's tree.
      const script = `const child = require("node:child_process").spawn(process.execPath, JSON.parse(process.argv[1]), { detached: true, stdio: "ignore" }); child.unref(); console.log(JSON.stringify({ pid: child.pid }));`;
      result = spawnSync(process.execPath, ["-e", script, JSON.stringify(argv)], {
        encoding: "utf8", timeout: 15000, env,
      });
    }
    assert.equal(result.status, 0, `operator broker failed: ${result.error?.message || result.stderr}`);
    const { pid } = JSON.parse(result.stdout.trim());
    assert.ok(Number.isInteger(pid) && pid > 1, "the broker returned no operator pid");
    const startedAt = startTimes([pid], { strict: true })?.get(pid);
    assert.ok(Number.isFinite(startedAt), "the new operator's creation identity is unreadable");
    return new OutsideGatewayOperator(pid, startedAt);
  }

  async stop() {
    if (!isAlive(this.pid)) return;
    assert.equal(startTimes([this.pid], { strict: true })?.get(this.pid), this.startedAt,
      "refusing to clean up an operator pid whose creation identity changed or is unreadable");
    process.kill(this.pid);
    const deadline = Date.now() + 5000;
    while (isAlive(this.pid) && Date.now() < deadline) await delay(50);
    assert.equal(isAlive(this.pid), false, "the owned operator survived fixture cleanup");
  }
}
