import { RuntimeAdapter } from "./base.js";

export class ClaudeAdapter extends RuntimeAdapter {
  get name() { return "claude-code"; }
  get displayName() { return "Claude Code"; }
  get sessionEnvVars() { return ["CLAUDE_SESSION_ID"]; }
}
