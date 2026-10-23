#!/usr/bin/env node
// The channel, as aify-wrapper's `aify-channel` plugin starts it: `node <bridge>/claude-channel-plugin.mjs`.
//
// The same channel as claude-channel.js, in the same process, so its parent is Claude itself and the orphan guard,
// the liveness gate and the runtime marker see what they always saw. It is a separate file so the plugin's command
// differs from the user-scope server's (Claude starts both then, and channel-producer.mjs picks one), and so a bridge
// that predates the gate has no plugin entry at all: the plugin's server then fails to start instead of producing
// ungated. It runs claude-channel.js's runChannel(), so the producer-gate declaration there speaks for this entry
// too, and a claude-channel.js without runChannel() fails this import rather than starting anything.

import path from "node:path";
import { fileURLToPath } from "node:url";

import { runChannel } from "./claude-channel.js";

if (Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await runChannel({ fromPlugin: true });
}
