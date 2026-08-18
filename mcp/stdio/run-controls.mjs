// Out-of-band CONTROLS against a run that is already executing: interrupt it, or steer it.
//
// A dispatched turn is not a closed box. While it runs, the service can queue controls against it — an
// operator hitting stop, or a message that should reach the agent mid-turn rather than waiting for the next
// one. The bridge claims those controls on each dispatch poll and applies them to the live controller.
//
// STEERS ARE BATCHED AND EVERYTHING ELSE IS NOT, which is the one piece of real logic here. Steering
// interrupts the model's turn to inject text, so applying four queued steers as four separate steers would
// disrupt the turn four times and deliver them as four unrelated interjections. They are instead collapsed
// into ONE steer carrying an explicit `[AIFY STEER BATCH]` envelope that tells the agent how many arrived
// and to apply them in order. A single steer is deliberately NOT wrapped — the envelope is overhead the
// agent has to read, and it earns its place only when there is genuinely more than one message.
//
// EVERY CONTROL IS ANSWERED, AND THAT IS NOT DECORATION. A control the bridge claims but never PATCHes back
// stays claimed forever: the operator's stop button reports nothing and the control sits in the queue. So
// each non-steer control has its own try/catch — one unsupported action must not abandon the controls
// behind it — and a failed steer marks the WHOLE batch failed, because none of them were applied.
//
// CAPABILITY GATING IS A REPORTED FAILURE, NOT A CRASH. Runtimes differ: some can interrupt, some can
// steer, some can do neither. Asking a runtime for something it cannot do answers the control with
// "not supported by this runtime" rather than throwing into the dispatch loop, where it would take down
// polling for every agent this bridge serves.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { httpCall } from "./aify-service-endpoint.mjs";
import { defaultMachineId } from "./runtimes.js";

// Pure function of env and hostname, so deriving it here agrees with every other derivation in the bridge.
const MACHINE_ID = defaultMachineId();

export async function processRunControls(agentId, activeRun) {
  if (!activeRun?.runId || !activeRun?.controller) return;
  const claim = await httpCall("POST", "/dispatch/controls/claim", {
    agentId,
    runId: activeRun.runId,
    machineId: MACHINE_ID,
  });
  const controls = claim.controls || [];
  const steerControls = controls.filter((control) => control.action === "steer");
  const otherControls = controls.filter((control) => control.action !== "steer");
  for (const control of otherControls) {
    try {
      if (control.action === "interrupt") {
        if (!activeRun.controller.capabilities?.interrupt || !activeRun.controller.interrupt) {
          throw new Error("Interrupt is not supported by this runtime");
        }
        await activeRun.controller.interrupt();
      } else if (control.action === "steer") {
        if (!activeRun.controller.capabilities?.steer || !activeRun.controller.steer) {
          throw new Error("Steer is not supported by this runtime");
        }
        await activeRun.controller.steer(control.body || "");
      } else {
        throw new Error(`Unknown control action "${control.action}"`);
      }

      await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
        status: "completed",
        response: `${control.action} accepted`,
        handledBy: agentId,
        machineId: MACHINE_ID,
      });
    } catch (error) {
      await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
        status: "failed",
        response: error?.message || String(error),
        handledBy: agentId,
        machineId: MACHINE_ID,
      });
    }
  }
  if (steerControls.length) {
    try {
      if (!activeRun.controller.capabilities?.steer || !activeRun.controller.steer) {
        throw new Error("Steer is not supported by this runtime");
      }
      const body = steerControls.length === 1
        ? steerControls[0].body || ""
        : [
            "[AIFY STEER BATCH]",
            `${steerControls.length} messages arrived while this run was active. Apply them to the current turn in order.`,
            "",
            ...steerControls.map((control, index) => [
              `--- Steer ${index + 1} of ${steerControls.length} ---`,
              control.body || "",
            ].join("\n")),
            "[/AIFY STEER BATCH]",
          ].join("\n\n");
      await activeRun.controller.steer(body);
      for (const control of steerControls) {
        await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
          status: "completed",
          response: steerControls.length === 1 ? "steer accepted" : `batched steer accepted (${steerControls.length})`,
          handledBy: agentId,
          machineId: MACHINE_ID,
        });
      }
    } catch (error) {
      for (const control of steerControls) {
        await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
          status: "failed",
          response: error?.message || String(error),
          handledBy: agentId,
          machineId: MACHINE_ID,
        });
      }
    }
  }
}
