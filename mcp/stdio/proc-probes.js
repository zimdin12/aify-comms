// Parsing the process listings the bridge reads.
//
// `parseProcLines` is the one parser for the PID/PPID/CMDLINE lines `reap-managed-claude.js` reads. This module
// held the hermes process probes and the tree kill too, for the gateway and delivery-loop reapers; those left comms
// with the gateway start in 0.9.10 (aify-env owns reaping), and nothing else called them.

// Parse "PID\tPPID\tCOMMANDLINE" lines (win path). Exported for tests.
export function parseProcLines(stdout) {
  return String(stdout || "")
    .split(/\r?\n/)
    .map((line) => {
      const parts = line.split("\t");
      if (parts.length < 3) return null;
      const pid = Number(parts[0].trim());
      const ppid = Number(parts[1].trim());
      const commandLine = parts.slice(2).join("\t");
      if (!Number.isInteger(pid) || pid <= 0) return null;
      return { pid, ppid: Number.isInteger(ppid) ? ppid : 0, commandLine };
    })
    .filter(Boolean);
}
